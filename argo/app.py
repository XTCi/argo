from __future__ import annotations

import os
import sys
from typing import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout

import argo.config  # noqa: F401

from app.domain.models.event import DoneEvent
from argo.renderer import render_event, RESET, DIM, BOLD, GRAY
from argo.session import ArgoSession, ARGO_DIR

# ── Terminal sequences ─────────────────────────────────────────────────────────
_ENTER_ALT = "\x1b[?1049h"
_EXIT_ALT  = "\x1b[?1049l"
_HIDE_CUR  = "\x1b[?25l"
_SHOW_CUR  = "\x1b[?25h"
_CLEAR     = "\x1b[2J\x1b[H"
_MOVE_COL1 = "\x1b[1G"

# ── Morandi palette ────────────────────────────────────────────────────────────
_TEAL   = "\x1b[38;2;100;150;150m"   # header border / assistant
_SAGE   = "\x1b[38;2;130;160;100m"   # input box / prompt
_OCHRE  = "\x1b[38;2;160;130;100m"   # user label
_PURPLE = "\x1b[38;2;140;100;160m"   # accent
_SUBTLE = "\x1b[38;5;243m"           # very dim text

HISTORY_FILE = ARGO_DIR / "history.txt"


def _term_width() -> int:
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 80


def _write(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


# ── Header bar ────────────────────────────────────────────────────────────────

def _render_header(session: ArgoSession, model: str) -> str:
    cwd = session.cwd
    w = _term_width()
    label = f"  argo  {model}  {cwd}  "
    if len(label) > w - 2:
        # Truncate cwd
        budget = w - 2 - len(f"  argo  {model}  …  ")
        cwd = "…" + cwd[-budget:] if budget > 0 else "…"
        label = f"  argo  {model}  {cwd}  "
    pad = max(0, w - len(label) - 2)
    top    = f"{_TEAL}╭{'─' * (w - 2)}╮{RESET}"
    middle = f"{_TEAL}│{BOLD}{_SAGE} argo{RESET}{_TEAL}  {RESET}{GRAY}{model}  {cwd}{_TEAL}{' ' * (pad + 1)}│{RESET}"
    bottom = f"{_TEAL}╰{'─' * (w - 2)}╯{RESET}"
    return f"{top}\n{middle}\n{bottom}\n"


# ── Input area ────────────────────────────────────────────────────────────────

def _render_input_hint() -> str:
    """Separator line with key hints, drawn above the prompt."""
    w = _term_width()
    hints_plain = "  [Enter] send  [^C] exit  [/help] cmds  [↑↓] history"
    hints_colored = (
        f"  {_SUBTLE}[{RESET}{DIM}Enter{RESET}{_SUBTLE}]{RESET} send"
        f"  {_SUBTLE}[{RESET}{DIM}^C{RESET}{_SUBTLE}]{RESET} exit"
        f"  {_SUBTLE}[{RESET}{DIM}/help{RESET}{_SUBTLE}]{RESET} cmds"
        f"  {_SUBTLE}[{RESET}{DIM}↑↓{RESET}{_SUBTLE}]{RESET} history"
    )
    pad = max(0, w - len(hints_plain) - 1)
    sep = f"{_SAGE}{'─' * pad}{RESET}"
    return f"\n{hints_colored} {sep}"


def _prompt_str() -> ANSI:
    """Colored prompt: '  argo› '"""
    return ANSI(f"\x1b[38;2;130;160;100m  argo\x1b[90m›\x1b[0m ")


# ── Main TUI app ──────────────────────────────────────────────────────────────

class ArgoApp:
    def __init__(
        self,
        agent,
        argo_session: ArgoSession,
        save_fn: Callable[[ArgoSession], None],
        model_name: str = "deepseek-chat",
        session_repo=None,
    ) -> None:
        self._agent = agent
        self._session = argo_session
        self._save_fn = save_fn
        self._model_name = model_name
        self._session_repo = session_repo
        ARGO_DIR.mkdir(parents=True, exist_ok=True)
        self._prompt = PromptSession(
            history=FileHistory(str(HISTORY_FILE)),
        )

    async def run(self) -> None:
        """Enter alternate screen, run REPL, restore on exit."""
        _write(_ENTER_ALT + _CLEAR)
        _write(_render_header(self._session, self._model_name) + "\n")
        try:
            await self._repl()
        finally:
            _write(_SHOW_CUR + _EXIT_ALT)

    async def _repl(self) -> None:
        while True:
            _write(_render_input_hint() + "\n")
            try:
                with patch_stdout():
                    user_input = await self._prompt.prompt_async(_prompt_str())
            except (EOFError, KeyboardInterrupt):
                break

            user_input = user_input.strip()
            if not user_input:
                continue

            if self.handle_slash(user_input):
                break

            _write(f"\n{_OCHRE}{BOLD}  You{RESET}{GRAY}›{RESET} {user_input}\n\n")
            await self._run_agent_turn(user_input)

    def handle_slash(self, cmd: str) -> bool:
        """Returns True if the app should exit."""
        if cmd in ("/exit", "/quit"):
            return True
        if cmd == "/clear":
            _write(_CLEAR + _render_header(self._session, self._model_name) + "\n")
            return False
        if cmd == "/help":
            w = _term_width()
            border = f"{_SAGE}{'─' * min(50, w - 4)}{RESET}"
            _write(
                f"\n  {border}\n"
                f"  {_SAGE}{BOLD}Commands{RESET}\n"
                f"  {border}\n"
                f"  {GRAY}/clear{RESET}   clear the screen (keeps session)\n"
                f"  {GRAY}/exit{RESET}    exit argo\n"
                f"  {GRAY}/help{RESET}    show this help\n"
                f"  {border}\n\n"
            )
            return False
        if cmd.startswith("/"):
            _write(f"\n  {GRAY}Unknown command: {cmd}  (try /help){RESET}\n")
        return False

    async def _run_agent_turn(self, user_message: str) -> None:
        session_messages = list(self._session.messages)
        _write(f"  {_TEAL}{'─' * max(0, _term_width() - 4)}{RESET}\n\n")
        try:
            async for event in self._agent.run(user_message, session_messages):
                rendered = render_event(event)
                if rendered:
                    _write(rendered + "\n")
                if isinstance(event, DoneEvent):
                    break
        except KeyboardInterrupt:
            _write(f"\n  {GRAY}[interrupted]{RESET}\n")
        finally:
            await self._sync_messages()
            self._save_fn(self._session)
            _write("\n")

    async def _sync_messages(self) -> None:
        """Pull updated conversation messages from the in-memory session repo."""
        if self._session_repo is not None:
            session_id = self._agent._session_id
            agent_name = self._agent.name
            mem = await self._session_repo.get_memory(session_id, agent_name)
            if mem:
                self._session.messages = list(mem.get_messages())
        else:
            working = self._agent._memory.working
            if working:
                self._session.messages = list(working.get_messages())
