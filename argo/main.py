from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.patch_stdout import patch_stdout

import argo.config  # noqa: F401 — ensures api/ on sys.path

from argo.config import load_config
from argo.session import ArgoSession, new_session, save_session, list_sessions
from argo.adapters.repos import InMemorySessionRepo, InMemoryCheckpointRepo
from argo.adapters.uow import InMemoryUoW
from argo.app import ArgoApp, _write, _render_header, _render_input_hint, _prompt_str, _term_width

from app.domain.services.runtime.workspace import resolve_workspace
from app.domain.services.runtime.project_context import load_project_context
from app.infrastructure.external.llm.openai_llm import OpenAILLM
from app.infrastructure.external.json_parser.repair_json_parser import RepairJSONParser
from app.domain.services.agents.coding_agent import build_coding_agent

# ── ANSI shared with app.py ────────────────────────────────────────────────────
_ENTER_ALT = "\x1b[?1049h"
_EXIT_ALT  = "\x1b[?1049l"
_SHOW_CUR  = "\x1b[?25h"
_CLEAR     = "\x1b[2J\x1b[H"

_RESET  = "\x1b[0m"
_BOLD   = "\x1b[1m"
_DIM    = "\x1b[2m"
_GRAY   = "\x1b[90m"
_SUBTLE = "\x1b[38;5;243m"
_SAGE   = "\x1b[38;2;130;160;100m"
_TEAL   = "\x1b[38;2;100;150;150m"
_OCHRE  = "\x1b[38;2;160;130;100m"


# ── Session picker (runs inside alt screen) ───────────────────────────────────

async def _pick_session_tui(cwd: str, model: str) -> ArgoSession:
    """Interactive session picker rendered inside the alternate screen."""
    sessions = list_sessions(cwd)
    w = _term_width()

    # Header
    _write(_render_header(
        ArgoSession(session_id="", cwd=cwd, created_at=0, updated_at=0),
        model,
    ))

    if not sessions:
        _write(f"\n  {_TEAL}Starting a new session in{_RESET} {_GRAY}{cwd}{_RESET}\n\n")
        return new_session(cwd)

    # Session list
    _write(f"\n  {_SAGE}{_BOLD}Recent sessions{_RESET}\n")
    _write(f"  {_SAGE}{'─' * min(52, w - 4)}{_RESET}\n\n")
    for i, s in enumerate(sessions, 1):
        dt = datetime.fromtimestamp(s.updated_at).strftime("%Y-%m-%d %H:%M")
        n  = len(s.messages)
        _write(
            f"  {_SUBTLE}[{_RESET}{_BOLD}{i}{_RESET}{_SUBTLE}]{_RESET}"
            f"  {dt}  {_GRAY}{n} message{'s' if n != 1 else ''}{_RESET}\n"
        )
    _write(f"\n  {_SUBTLE}[{_RESET}{_BOLD}N{_RESET}{_SUBTLE}]{_RESET}  New session\n")
    _write(f"  {_SUBTLE}[{_RESET}{_BOLD}Q{_RESET}{_SUBTLE}]{_RESET}  Quit\n\n")

    picker = PromptSession()
    while True:
        try:
            with patch_stdout():
                raw = await picker.prompt_async(
                    ANSI(f"\x1b[38;2;130;160;100m  Choose\x1b[90m›\x1b[0m ")
                )
        except (EOFError, KeyboardInterrupt):
            _write(_SHOW_CUR + _EXIT_ALT)
            sys.exit(0)

        choice = raw.strip().upper()

        if choice in ("Q",):
            _write(_SHOW_CUR + _EXIT_ALT)
            sys.exit(0)

        if choice in ("N", ""):
            return new_session(cwd)

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(sessions):
                return sessions[idx]
        except ValueError:
            pass

        _write(f"  {_GRAY}Enter 1–{len(sessions)}, N, or Q{_RESET}\n")


# ── Entry point ───────────────────────────────────────────────────────────────

async def main(yolo: bool = False) -> None:
    # Load config before entering alt screen so errors print normally
    try:
        llm_cfg, agent_cfg, perm_cfg = load_config()
    except FileNotFoundError as e:
        print(f"\n  argo error: {e}\n", file=sys.stderr)
        sys.exit(1)

    cwd = os.getcwd()

    # Enter full-screen TUI immediately — everything from here is inside alt screen
    _write(_ENTER_ALT + _CLEAR)

    shell_session = None
    try:
        argo_session = await _pick_session_tui(cwd, llm_cfg.model_name)

        # Create persistent shell session
        from app.domain.services.tools.shell_session import PersistentShellSession
        shell_session = PersistentShellSession(cwd=cwd)
        await shell_session.start()

        project_context = await load_project_context(cwd)

        # Streaming callback: write tokens to TUI as they arrive
        _prefix_state = {"written": False}

        def stream_cb(chunk: str) -> None:
            if not _prefix_state["written"]:
                _write(f"\n  {_TEAL}argo{_RESET}{_GRAY}›{_RESET} ")
                _prefix_state["written"] = True
            _write(chunk)
            sys.stdout.flush()

        def stream_reset_fn() -> None:
            _prefix_state["written"] = False

        # Confirm function shown in TUI for permission asks
        async def confirm_fn(command: str) -> str:
            _write(
                f"\n  {_OCHRE}⚠  argo wants to run:{_RESET}\n"
                f"     {command}\n\n"
                f"  {_SUBTLE}[y]{_RESET} allow once"
                f"  {_SUBTLE}[!]{_RESET} always allow this session"
                f"  {_SUBTLE}[n]{_RESET} deny\n"
            )
            picker = PromptSession()
            with patch_stdout():
                raw = await picker.prompt_async(
                    ANSI(f"\x1b[38;2;130;160;100m  Choose\x1b[90m›\x1b[0m ")
                )
            return raw.strip().lower() or "n"

        # Permission gateway
        from argo.permissions import PermissionGateway
        gateway = PermissionGateway(
            config=perm_cfg,
            yolo=yolo,
            confirm_fn=confirm_fn,
        )

        session_repo    = InMemorySessionRepo(initial_messages=list(argo_session.messages))
        checkpoint_repo = InMemoryCheckpointRepo()
        uow_factory     = lambda: InMemoryUoW(session_repo, checkpoint_repo)

        llm         = OpenAILLM(llm_cfg)
        json_parser = RepairJSONParser()
        workspace   = resolve_workspace(cwd)

        agent = build_coding_agent(
            llm=llm,
            json_parser=json_parser,
            agent_config=agent_cfg,
            uow_factory=uow_factory,
            session_id=argo_session.session_id,
            workspace=workspace,
            shell_session=shell_session,
            pre_execute_hook=gateway.check,
            project_context=project_context,
            stream_callback=stream_cb,      # NEW
            stream_reset=stream_reset_fn,   # NEW
        )

        app = ArgoApp(
            agent=agent,
            argo_session=argo_session,
            save_fn=save_session,
            model_name=llm_cfg.model_name,
            session_repo=session_repo,
        )
        # Draw fresh screen before entering the REPL
        _write(_CLEAR + _render_header(argo_session, llm_cfg.model_name) + "\n")
        await app._repl()

    finally:
        if shell_session is not None:
            await shell_session.close()
        _write(_SHOW_CUR + _EXIT_ALT)

def sync_main() -> None:
    asyncio.run(main(yolo="--yolo" in sys.argv))
