# Argo CLI TUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `argo/` — a standalone terminal TUI for ny-agent that runs locally without Docker/PostgreSQL, using in-memory adapters and JSON session persistence in `~/.argo/sessions/`.

**Architecture:** A new `argo/` top-level directory adds `api/` to `sys.path` and imports `build_coding_agent()` directly. `InMemoryUoW` replaces the PostgreSQL-backed `IUnitOfWork`. `prompt_toolkit` handles input; raw ANSI codes drive the alternate-screen TUI. Sessions persist as JSON in `~/.argo/sessions/<session_id>.json`, autosaved after each agent turn.

**Tech Stack:** Python 3.12, asyncio, prompt_toolkit 3.0.43 (already installed), json_repair (already installed), pyyaml (already installed in api/), ANSI escape codes.

## Global Constraints

- No new pip packages — only stdlib + packages already installed in the project (`prompt_toolkit`, `json_repair`, `pyyaml`, `openai`, `pydantic`).
- `build_coding_agent()` in `api/app/domain/services/agents/coding_agent.py` must not be modified.
- `api/` is added to `sys.path` at process startup (before any domain import) by `argo/config.py`.
- Session JSON stored at `~/.argo/sessions/<session_id>.json`, written atomically (tmp → rename).
- Entry command: `python -m argo` from the `ny-agent/` project root.
- Working directory: `os.getcwd()` at startup — wherever the user launched from.
- All `argo/` code is pure Python; no Cython, no C extensions.
- Tests live in `argo/tests/` and use `pytest` + `pytest-asyncio`; run with `cd ny-agent && python -m pytest argo/tests/ -v`.
- The project/CLI is named **Argo** in all user-facing output.

---

## File Map

```
ny-agent/
  argo/
    __init__.py               ← empty
    __main__.py               ← entry: asyncio.run(main())
    config.py                 ← sys.path patch + load api/config.yaml → LLMConfig, AgentConfig
    session.py                ← ArgoSession dataclass + save/load/list/new helpers
    adapters/
      __init__.py             ← empty
      repos.py                ← InMemorySessionRepo, InMemoryCheckpointRepo, NoopFileRepo
      uow.py                  ← InMemoryUoW (implements IUnitOfWork)
    renderer.py               ← event stream → ANSI terminal strings
    app.py                    ← TUI core: alternate screen, prompt_toolkit, render loop
    main.py                   ← startup orchestrator tying all pieces together
    tests/
      __init__.py
      test_session.py
      test_adapters.py
      test_renderer.py
      test_config.py
```

---

## Task 1: `config.py` — sys.path patch and config loading

**Files:**
- Create: `argo/__init__.py` (empty)
- Create: `argo/config.py`
- Create: `argo/tests/__init__.py` (empty)
- Create: `argo/tests/test_config.py`

**Interfaces:**
- Produces: `load_config() -> tuple[LLMConfig, AgentConfig]` — used by Task 7 (`main.py`)
- Produces: `PROJECT_ROOT: Path` — absolute path to `ny-agent/`

- [ ] **Step 1: Create empty package files**

```bash
mkdir -p argo/tests
touch argo/__init__.py argo/tests/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `argo/tests/test_config.py`:

```python
import sys
import os
from pathlib import Path
import pytest

# Patch sys.path so argo is importable from ny-agent/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def test_load_config_returns_llm_and_agent_config():
    from argo.config import load_config
    llm_cfg, agent_cfg = load_config()
    assert llm_cfg.model_name == "deepseek-chat"
    assert agent_cfg.max_iterations > 0


def test_load_config_raises_on_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGO_CONFIG_PATH", str(tmp_path / "nonexistent.yaml"))
    # Re-import forces re-read
    import importlib
    import argo.config as cfg_mod
    importlib.reload(cfg_mod)
    with pytest.raises(FileNotFoundError):
        cfg_mod.load_config()
    # Restore env
    monkeypatch.delenv("ARGO_CONFIG_PATH")
    importlib.reload(cfg_mod)


def test_api_on_sys_path_after_import():
    import argo.config  # noqa: F401 — side-effect: patches sys.path
    # If api/ is on path, this import works
    from app.domain.models.app_config import LLMConfig  # noqa: F401
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd /Users/nuoyunzhibo/xu/project/github-project/my-agent/ny-agent
python -m pytest argo/tests/test_config.py -v
```

Expected: `ModuleNotFoundError: No module named 'argo'`

- [ ] **Step 4: Write `argo/config.py`**

```python
from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
_API_PATH = PROJECT_ROOT / "api"

if str(_API_PATH) not in sys.path:
    sys.path.insert(0, str(_API_PATH))

from app.domain.models.app_config import AppConfig, LLMConfig, AgentConfig  # noqa: E402


def _config_path() -> Path:
    env = os.environ.get("ARGO_CONFIG_PATH")
    if env:
        p = Path(env)
        if not p.exists():
            raise FileNotFoundError(f"Config not found: {p}")
        return p
    p = PROJECT_ROOT / "api" / "config.yaml"
    if not p.exists():
        raise FileNotFoundError(
            f"Config file not found at {p}. "
            "Copy api/config.yaml.example to api/config.yaml and fill in your API key."
        )
    return p


def load_config() -> tuple[LLMConfig, AgentConfig]:
    raw = yaml.safe_load(_config_path().read_text())
    app_cfg = AppConfig(**raw)
    return app_cfg.llm_config, app_cfg.agent_config
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd /Users/nuoyunzhibo/xu/project/github-project/my-agent/ny-agent
python -m pytest argo/tests/test_config.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add argo/__init__.py argo/config.py argo/tests/__init__.py argo/tests/test_config.py
git commit -m "feat(argo): add config loader with sys.path patch"
```

---

## Task 2: `session.py` — JSON session persistence

**Files:**
- Create: `argo/session.py`
- Create: `argo/tests/test_session.py`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces:
  - `ArgoSession` dataclass with fields: `session_id: str`, `cwd: str`, `created_at: float`, `updated_at: float`, `messages: list[dict]`, `checkpoints: list[dict]`
  - `new_session(cwd: str) -> ArgoSession`
  - `save_session(session: ArgoSession) -> None` — atomic write to `~/.argo/sessions/<session_id>.json`
  - `load_session(session_id: str) -> ArgoSession`
  - `list_sessions(cwd: str) -> list[ArgoSession]` — sorted by `updated_at` desc, max 5, filtered by `cwd`

- [ ] **Step 1: Write the failing tests**

Create `argo/tests/test_session.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import json
import time
import uuid
import pytest
from unittest.mock import patch

from argo.session import ArgoSession, new_session, save_session, load_session, list_sessions


@pytest.fixture
def tmp_sessions(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    with patch("argo.session.SESSIONS_DIR", sessions_dir):
        yield sessions_dir


def test_new_session_has_correct_fields(tmp_sessions):
    s = new_session(cwd="/my/project")
    assert s.cwd == "/my/project"
    assert len(s.session_id) == 36  # UUID format
    assert s.messages == []
    assert s.checkpoints == []
    assert s.created_at > 0


def test_save_and_load_roundtrip(tmp_sessions):
    s = new_session(cwd="/my/project")
    s.messages.append({"role": "user", "content": "hello"})
    save_session(s)

    loaded = load_session(s.session_id)
    assert loaded.session_id == s.session_id
    assert loaded.cwd == "/my/project"
    assert loaded.messages == [{"role": "user", "content": "hello"}]


def test_save_is_atomic(tmp_sessions):
    s = new_session(cwd="/my/project")
    save_session(s)
    json_file = tmp_sessions / f"{s.session_id}.json"
    assert json_file.exists()
    # tmp file must not remain
    assert not (tmp_sessions / f"{s.session_id}.json.tmp").exists()


def test_list_sessions_filtered_by_cwd(tmp_sessions):
    s1 = new_session(cwd="/proj/a")
    s2 = new_session(cwd="/proj/b")
    s3 = new_session(cwd="/proj/a")
    for s in [s1, s2, s3]:
        save_session(s)

    result = list_sessions(cwd="/proj/a")
    ids = {r.session_id for r in result}
    assert s1.session_id in ids
    assert s3.session_id in ids
    assert s2.session_id not in ids


def test_list_sessions_sorted_desc_by_updated_at(tmp_sessions):
    sessions = []
    for i in range(3):
        s = new_session(cwd="/proj")
        s.updated_at = float(i)
        save_session(s)
        sessions.append(s)

    result = list_sessions(cwd="/proj")
    assert result[0].session_id == sessions[2].session_id


def test_list_sessions_max_5(tmp_sessions):
    for _ in range(7):
        s = new_session(cwd="/proj")
        save_session(s)
    result = list_sessions(cwd="/proj")
    assert len(result) <= 5


def test_load_corrupt_session_raises(tmp_sessions):
    bad_file = tmp_sessions / "corrupt.json"
    bad_file.write_text("not json {{{")
    with pytest.raises(Exception):
        load_session("corrupt")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/nuoyunzhibo/xu/project/github-project/my-agent/ny-agent
python -m pytest argo/tests/test_session.py -v
```

Expected: `ModuleNotFoundError: No module named 'argo.session'`

- [ ] **Step 3: Write `argo/session.py`**

```python
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

ARGO_DIR = Path.home() / ".argo"
SESSIONS_DIR = ARGO_DIR / "sessions"


def _ensure_dirs() -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class ArgoSession:
    session_id: str
    cwd: str
    created_at: float
    updated_at: float
    messages: list = field(default_factory=list)
    checkpoints: list = field(default_factory=list)


def new_session(cwd: str) -> ArgoSession:
    now = time.time()
    return ArgoSession(
        session_id=str(uuid.uuid4()),
        cwd=cwd,
        created_at=now,
        updated_at=now,
    )


def save_session(session: ArgoSession) -> None:
    _ensure_dirs()
    target = SESSIONS_DIR / f"{session.session_id}.json"
    tmp = SESSIONS_DIR / f"{session.session_id}.json.tmp"
    session.updated_at = time.time()
    tmp.write_text(json.dumps(asdict(session), ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)


def load_session(session_id: str) -> ArgoSession:
    path = SESSIONS_DIR / f"{session_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return ArgoSession(**data)


def list_sessions(cwd: str) -> list[ArgoSession]:
    _ensure_dirs()
    sessions: list[ArgoSession] = []
    for f in SESSIONS_DIR.glob("*.json"):
        try:
            s = load_session(f.stem)
            if s.cwd == cwd:
                sessions.append(s)
        except Exception:
            continue
    sessions.sort(key=lambda s: s.updated_at, reverse=True)
    return sessions[:5]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/nuoyunzhibo/xu/project/github-project/my-agent/ny-agent
python -m pytest argo/tests/test_session.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add argo/session.py argo/tests/test_session.py
git commit -m "feat(argo): add session persistence to ~/.argo/sessions/"
```

---

## Task 3: `adapters/` — in-memory UoW, repos

**Files:**
- Create: `argo/adapters/__init__.py` (empty)
- Create: `argo/adapters/repos.py`
- Create: `argo/adapters/uow.py`
- Create: `argo/tests/test_adapters.py`

**Interfaces:**
- Consumes: `Memory` from `app.domain.models.memory`; `Checkpoint` from `app.domain.models.checkpoint`; `IUnitOfWork` from `app.domain.repositories.uow`; `ArgoSession` from Task 2
- Produces:
  - `InMemorySessionRepo(initial_messages: list[dict])` — `save_memory(session_id, agent_name, memory)` stores in `._memory_store[agent_name]`; `get_memory(session_id, agent_name) -> Memory` returns stored or empty; all other protocol methods are async no-ops
  - `InMemoryCheckpointRepo()` — `save(checkpoint)`, `get_latest(session_id, filepath) -> Checkpoint | None`, `get_by_session_and_path(...) -> list[Checkpoint]`, `delete_by_session(session_id)`; all in-memory
  - `NoopFileRepo()` — `save(file)` and `get_by_id(file_id)` are async no-ops returning `None`
  - `InMemoryUoW(session_repo, checkpoint_repo, file_repo)` — implements `IUnitOfWork`; `commit()` and `rollback()` are async no-ops; `__aenter__` returns `self`; `__aexit__` does nothing

- [ ] **Step 1: Create adapter package**

```bash
touch argo/adapters/__init__.py
```

- [ ] **Step 2: Write the failing tests**

Create `argo/tests/test_adapters.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
import asyncio

# config.py must be imported first so api/ is on sys.path
import argo.config  # noqa: F401


@pytest.mark.asyncio
async def test_inmemory_session_repo_save_and_get_memory():
    from argo.adapters.repos import InMemorySessionRepo
    from app.domain.models.memory import Memory

    repo = InMemorySessionRepo(initial_messages=[{"role": "user", "content": "hi"}])
    mem = Memory()
    mem.add_message({"role": "assistant", "content": "hello"})
    await repo.save_memory("sess1", "base", mem)

    result = await repo.get_memory("sess1", "base")
    assert result.messages == [{"role": "assistant", "content": "hello"}]


@pytest.mark.asyncio
async def test_inmemory_session_repo_get_memory_unknown_returns_empty():
    from argo.adapters.repos import InMemorySessionRepo
    from app.domain.models.memory import Memory

    repo = InMemorySessionRepo(initial_messages=[])
    result = await repo.get_memory("sess1", "unknown_agent")
    assert isinstance(result, Memory)
    assert result.messages == []


@pytest.mark.asyncio
async def test_checkpoint_repo_save_and_get_latest():
    from argo.adapters.repos import InMemoryCheckpointRepo
    from app.domain.models.checkpoint import Checkpoint

    repo = InMemoryCheckpointRepo()
    c = Checkpoint(session_id="s1", turn_id="t1", filepath="/f.py", content="old")
    await repo.save(c)
    latest = await repo.get_latest("s1", "/f.py")
    assert latest is not None
    assert latest.content == "old"


@pytest.mark.asyncio
async def test_checkpoint_repo_delete_by_session():
    from argo.adapters.repos import InMemoryCheckpointRepo
    from app.domain.models.checkpoint import Checkpoint

    repo = InMemoryCheckpointRepo()
    c = Checkpoint(session_id="s1", turn_id="t1", filepath="/f.py", content="x")
    await repo.save(c)
    await repo.delete_by_session("s1")
    assert await repo.get_latest("s1", "/f.py") is None


@pytest.mark.asyncio
async def test_inmemory_uow_context_manager():
    from argo.adapters.repos import InMemorySessionRepo, InMemoryCheckpointRepo, NoopFileRepo
    from argo.adapters.uow import InMemoryUoW

    session_repo = InMemorySessionRepo(initial_messages=[])
    checkpoint_repo = InMemoryCheckpointRepo()
    file_repo = NoopFileRepo()
    uow = InMemoryUoW(session_repo, checkpoint_repo, file_repo)

    async with uow as u:
        assert u is uow
        await u.commit()   # must not raise
        await u.rollback() # must not raise


@pytest.mark.asyncio
async def test_uow_factory_shares_same_repo_instances():
    from argo.adapters.repos import InMemorySessionRepo, InMemoryCheckpointRepo, NoopFileRepo
    from argo.adapters.uow import InMemoryUoW
    from app.domain.models.memory import Memory

    session_repo = InMemorySessionRepo(initial_messages=[])
    checkpoint_repo = InMemoryCheckpointRepo()
    uow_factory = lambda: InMemoryUoW(session_repo, checkpoint_repo, NoopFileRepo())

    mem = Memory()
    mem.add_message({"role": "user", "content": "test"})

    async with uow_factory() as u1:
        await u1.session.save_memory("s", "agent", mem)

    async with uow_factory() as u2:
        result = await u2.session.get_memory("s", "agent")
        assert result.messages[0]["content"] == "test"
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd /Users/nuoyunzhibo/xu/project/github-project/my-agent/ny-agent
python -m pytest argo/tests/test_adapters.py -v
```

Expected: `ModuleNotFoundError: No module named 'argo.adapters'`

- [ ] **Step 4: Write `argo/adapters/repos.py`**

```python
from __future__ import annotations

from typing import Optional
import argo.config  # noqa: F401 — ensures api/ on sys.path

from app.domain.models.checkpoint import Checkpoint
from app.domain.models.file import File
from app.domain.models.memory import Memory


class InMemorySessionRepo:
    def __init__(self, initial_messages: list[dict]) -> None:
        self._initial_messages = initial_messages
        self._memory_store: dict[str, Memory] = {}

    async def save_memory(self, session_id: str, agent_name: str, memory: Memory) -> None:
        self._memory_store[agent_name] = memory

    async def get_memory(self, session_id: str, agent_name: str) -> Memory:
        if agent_name in self._memory_store:
            return self._memory_store[agent_name]
        mem = Memory()
        if self._initial_messages:
            mem.add_messages(self._initial_messages)
            self._memory_store[agent_name] = mem
        return mem

    # Protocol no-ops
    async def save(self, session): ...
    async def get_all(self): return []
    async def get_by_id(self, session_id): return None
    async def delete_by_id(self, session_id): ...
    async def update_title(self, session_id, title): ...
    async def update_latest_message(self, session_id, message, timestamp): ...
    async def update_unread_message_count(self, session_id, count): ...
    async def increment_unread_message_count(self, session_id): ...
    async def decrement_unread_message_count(self, session_id): ...
    async def update_status(self, session_id, status): ...
    async def add_event(self, session_id, event): ...
    async def add_file(self, session_id, file): ...
    async def remove_file(self, session_id, file_id): ...
    async def get_file_by_path(self, session_id, filepath): return None


class InMemoryCheckpointRepo:
    def __init__(self) -> None:
        self._store: dict[str, list[Checkpoint]] = {}

    def _key(self, session_id: str, filepath: str) -> str:
        return f"{session_id}::{filepath}"

    async def save(self, checkpoint: Checkpoint) -> None:
        k = self._key(checkpoint.session_id, checkpoint.filepath)
        self._store.setdefault(k, []).append(checkpoint)

    async def get_by_session_and_path(self, session_id: str, filepath: str) -> list[Checkpoint]:
        return self._store.get(self._key(session_id, filepath), [])

    async def get_latest(self, session_id: str, filepath: str) -> Optional[Checkpoint]:
        checkpoints = self._store.get(self._key(session_id, filepath), [])
        return checkpoints[-1] if checkpoints else None

    async def delete_by_session(self, session_id: str) -> None:
        keys_to_delete = [k for k in self._store if k.startswith(f"{session_id}::")]
        for k in keys_to_delete:
            del self._store[k]


class NoopFileRepo:
    async def save(self, file: File) -> None:
        ...

    async def get_by_id(self, file_id: str) -> None:
        return None
```

- [ ] **Step 5: Write `argo/adapters/uow.py`**

```python
from __future__ import annotations

from app.domain.repositories.uow import IUnitOfWork
from argo.adapters.repos import InMemorySessionRepo, InMemoryCheckpointRepo, NoopFileRepo


class InMemoryUoW(IUnitOfWork):
    def __init__(
        self,
        session_repo: InMemorySessionRepo,
        checkpoint_repo: InMemoryCheckpointRepo,
        file_repo: NoopFileRepo,
    ) -> None:
        self.session = session_repo
        self.checkpoint = checkpoint_repo
        self.file = file_repo

    async def commit(self) -> None:
        ...

    async def rollback(self) -> None:
        ...

    async def __aenter__(self) -> "InMemoryUoW":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        ...
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd /Users/nuoyunzhibo/xu/project/github-project/my-agent/ny-agent
python -m pytest argo/tests/test_adapters.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add argo/adapters/__init__.py argo/adapters/repos.py argo/adapters/uow.py argo/tests/test_adapters.py
git commit -m "feat(argo): add in-memory UoW and repository adapters"
```

---

## Task 4: `renderer.py` — agent event → ANSI strings

**Files:**
- Create: `argo/renderer.py`
- Create: `argo/tests/test_renderer.py`

**Interfaces:**
- Consumes: `ToolEvent`, `ToolEventStatus`, `MessageEvent`, `ErrorEvent`, `DoneEvent` from `app.domain.models.event`; `ToolResult` from `app.domain.models.tool_result`
- Produces:
  - `render_event(event: BaseEvent) -> str | None` — returns ANSI-formatted string or `None` (for DoneEvent/unknown)
  - ANSI color constants: `RESET`, `GRAY`, `GREEN`, `RED`, `BOLD`
  - `_truncate_args(args: dict) -> str` — renders most useful arg, max 60 chars
  - `_truncate_output(text: str, max_lines: int = 10) -> str` — truncates and appends fold hint

- [ ] **Step 1: Write the failing tests**

Create `argo/tests/test_renderer.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import argo.config  # noqa: F401

from app.domain.models.event import ToolEvent, ToolEventStatus, MessageEvent, ErrorEvent, DoneEvent
from app.domain.models.tool_result import ToolResult


def test_tool_event_calling_renders_spinner():
    from argo.renderer import render_event
    event = ToolEvent(
        tool_call_id="tc1",
        tool_name="shell",
        function_name="shell_execute",
        function_args={"command": "ls -la"},
        status=ToolEventStatus.CALLING,
    )
    result = render_event(event)
    assert result is not None
    assert "shell_execute" in result
    assert "⟳" in result


def test_tool_event_called_success_renders_checkmark():
    from argo.renderer import render_event
    event = ToolEvent(
        tool_call_id="tc1",
        tool_name="shell",
        function_name="shell_execute",
        function_args={"command": "ls"},
        function_result=ToolResult(success=True, message="ok"),
        status=ToolEventStatus.CALLED,
    )
    result = render_event(event)
    assert "✓" in result


def test_tool_event_called_failure_renders_x():
    from argo.renderer import render_event
    event = ToolEvent(
        tool_call_id="tc1",
        tool_name="shell",
        function_name="shell_execute",
        function_args={"command": "bad"},
        function_result=ToolResult(success=False, message="error: not found"),
        status=ToolEventStatus.CALLED,
    )
    result = render_event(event)
    assert "✗" in result


def test_message_event_renders_content():
    from argo.renderer import render_event
    event = MessageEvent(role="assistant", message="Here is the answer.")
    result = render_event(event)
    assert "Here is the answer." in result


def test_error_event_renders_error():
    from argo.renderer import render_event
    event = ErrorEvent(error="something went wrong")
    result = render_event(event)
    assert "something went wrong" in result


def test_done_event_returns_none():
    from argo.renderer import render_event
    event = DoneEvent()
    assert render_event(event) is None


def test_truncate_args_limits_to_60_chars():
    from argo.renderer import _truncate_args
    long_cmd = "x" * 100
    result = _truncate_args({"command": long_cmd})
    assert len(result) <= 63  # 60 + "..."


def test_truncate_output_folds_at_10_lines():
    from argo.renderer import _truncate_output
    text = "\n".join([f"line {i}" for i in range(20)])
    result = _truncate_output(text, max_lines=10)
    assert result.count("\n") < 15
    assert "+10 more" in result
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/nuoyunzhibo/xu/project/github-project/my-agent/ny-agent
python -m pytest argo/tests/test_renderer.py -v
```

Expected: `ModuleNotFoundError: No module named 'argo.renderer'`

- [ ] **Step 3: Write `argo/renderer.py`**

```python
from __future__ import annotations

import argo.config  # noqa: F401

from app.domain.models.event import (
    BaseEvent, ToolEvent, ToolEventStatus, MessageEvent, ErrorEvent, DoneEvent,
)
from app.domain.models.tool_result import ToolResult

# ANSI codes
RESET = "\033[0m"
GRAY  = "\033[90m"
GREEN = "\033[32m"
RED   = "\033[31m"
BOLD  = "\033[1m"


def _truncate_args(args: dict) -> str:
    preferred_keys = ["command", "filepath", "path", "query", "pattern"]
    for key in preferred_keys:
        if key in args:
            val = str(args[key])
            return val[:60] + "…" if len(val) > 60 else val
    if args:
        val = str(next(iter(args.values())))
        return val[:60] + "…" if len(val) > 60 else val
    return ""


def _truncate_output(text: str, max_lines: int = 10) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    visible = lines[:max_lines]
    hidden = len(lines) - max_lines
    return "\n".join(visible) + f"\n{GRAY}[+{hidden} more lines]{RESET}"


def render_event(event: BaseEvent) -> str | None:
    if isinstance(event, ToolEvent):
        arg_preview = _truncate_args(event.function_args)
        if event.status == ToolEventStatus.CALLING:
            return f"{GRAY}  ⟳ {event.function_name} {arg_preview}{RESET}"
        # CALLED
        result: ToolResult | None = event.function_result
        summary = ""
        if result:
            summary = _truncate_output(result.message or "", max_lines=10)
        if result and result.success:
            return f"{GREEN}  ✓ {event.function_name}{RESET}" + (f" → {summary}" if summary else "")
        else:
            return f"{RED}  ✗ {event.function_name}{RESET}" + (f" → {summary}" if summary else "")

    if isinstance(event, MessageEvent):
        return event.message

    if isinstance(event, ErrorEvent):
        return f"{RED}{BOLD}[error]{RESET} {event.error}"

    if isinstance(event, DoneEvent):
        return None

    return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/nuoyunzhibo/xu/project/github-project/my-agent/ny-agent
python -m pytest argo/tests/test_renderer.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add argo/renderer.py argo/tests/test_renderer.py
git commit -m "feat(argo): add ANSI event renderer"
```

---

## Task 5: `app.py` — TUI core (alternate screen + prompt_toolkit)

**Files:**
- Create: `argo/app.py`

**Interfaces:**
- Consumes: `render_event()` from Task 4; `BaseAgent` event stream (`AsyncGenerator[BaseEvent, None]`)
- Produces:
  - `ArgoApp(agent, argo_session, save_fn)` class
  - `ArgoApp.run() -> None` — async, enters alternate screen, runs REPL until exit
  - `ArgoApp.handle_slash(cmd: str) -> bool` — returns `True` if `/exit` or `/quit`

No unit tests for `app.py` — it drives the terminal and cannot be meaningfully tested without a TTY. Manual smoke test is described in Task 7.

- [ ] **Step 1: Write `argo/app.py`**

```python
from __future__ import annotations

import asyncio
import os
import sys
from typing import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout

import argo.config  # noqa: F401

from app.domain.models.event import DoneEvent
from argo.renderer import render_event
from argo.session import ArgoSession, ARGO_DIR

# Alternate screen ANSI sequences
_ENTER_ALT  = "\033[?1049h"
_EXIT_ALT   = "\033[?1049l"
_HIDE_CUR   = "\033[?25l"
_SHOW_CUR   = "\033[?25h"
_CLEAR      = "\033[2J\033[H"
_BOLD       = "\033[1m"
_RESET      = "\033[0m"
_GRAY       = "\033[90m"
_CYAN       = "\033[36m"

HISTORY_FILE = ARGO_DIR / "history.txt"


def _write(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def _header(session: ArgoSession, model: str) -> str:
    cwd = session.cwd
    if len(cwd) > 40:
        cwd = "…" + cwd[-39:]
    return f"{_BOLD}{_CYAN}  argo{_RESET}  {_GRAY}{model}  {cwd}{_RESET}\n"


class ArgoApp:
    def __init__(
        self,
        agent,
        argo_session: ArgoSession,
        save_fn: Callable[[ArgoSession], None],
        model_name: str = "deepseek-chat",
    ) -> None:
        self._agent = agent
        self._session = argo_session
        self._save_fn = save_fn
        self._model_name = model_name
        ARGO_DIR.mkdir(parents=True, exist_ok=True)
        self._prompt = PromptSession(
            history=FileHistory(str(HISTORY_FILE)),
        )

    async def run(self) -> None:
        _write(_ENTER_ALT + _HIDE_CUR + _CLEAR)
        _write(_header(self._session, self._model_name))
        _write("\n")
        try:
            await self._repl()
        finally:
            _write(_SHOW_CUR + _EXIT_ALT)

    async def _repl(self) -> None:
        while True:
            try:
                with patch_stdout():
                    user_input = await self._prompt.prompt_async("> ")
            except (EOFError, KeyboardInterrupt):
                break

            user_input = user_input.strip()
            if not user_input:
                continue

            if self.handle_slash(user_input):
                break

            _write(f"\n{_BOLD}You:{_RESET} {user_input}\n\n")
            await self._run_agent_turn(user_input)

    def handle_slash(self, cmd: str) -> bool:
        if cmd in ("/exit", "/quit"):
            return True
        if cmd == "/clear":
            _write(_CLEAR + _header(self._session, self._model_name) + "\n")
            return False
        if cmd == "/help":
            _write(
                f"{_GRAY}"
                "  /new     start a new session\n"
                "  /clear   clear the screen\n"
                "  /exit    exit argo\n"
                "  /help    show this help\n"
                f"{_RESET}\n"
            )
            return False
        if cmd.startswith("/"):
            _write(f"{_GRAY}  Unknown command: {cmd}  (try /help){_RESET}\n\n")
        return False

    async def _run_agent_turn(self, user_message: str) -> None:
        session_messages = list(self._session.messages)
        try:
            async for event in self._agent.run(user_message, session_messages):
                rendered = render_event(event)
                if rendered:
                    _write(rendered + "\n")
                if isinstance(event, DoneEvent):
                    break
        except KeyboardInterrupt:
            _write("\n[interrupted]\n")
        finally:
            # Sync updated messages from the repo back to the session
            mem = await self._agent._memory.get_working_memory(
                self._agent._session_id, self._agent.name
            )
            if mem:
                self._session.messages = list(mem.get_messages())
            self._save_fn(self._session)
            _write("\n")
```

- [ ] **Step 2: Commit**

```bash
git add argo/app.py
git commit -m "feat(argo): add TUI core with alternate screen and prompt_toolkit"
```

---

## Task 6: `main.py` and `__main__.py` — startup orchestrator

**Files:**
- Create: `argo/main.py`
- Create: `argo/__main__.py`

**Interfaces:**
- Consumes: `load_config()` (Task 1), `new_session()`, `list_sessions()`, `save_session()` (Task 2), `InMemorySessionRepo`, `InMemoryCheckpointRepo`, `NoopFileRepo` (Task 3), `InMemoryUoW` (Task 3), `ArgoApp` (Task 5)
- Consumes from domain: `build_coding_agent()`, `OpenAILLM`, `RepairJSONParser`, `WorkspaceContext`, `resolve_workspace()`
- Produces: `async def main() -> None` — full startup flow

- [ ] **Step 1: Write `argo/__main__.py`**

```python
import asyncio
from argo.main import main

if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Write `argo/main.py`**

```python
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

from argo.config import load_config
from argo.session import ArgoSession, new_session, save_session, load_session, list_sessions
from argo.adapters.repos import InMemorySessionRepo, InMemoryCheckpointRepo, NoopFileRepo
from argo.adapters.uow import InMemoryUoW
from argo.app import ArgoApp

import argo.config  # noqa: F401 — ensures api/ on sys.path

from app.domain.services.runtime.workspace import resolve_workspace
from app.infrastructure.external.llm.openai_llm import OpenAILLM
from app.infrastructure.external.json_parser.repair_json_parser import RepairJSONParser
from app.domain.services.agents.coding_agent import build_coding_agent

_BOLD  = "\033[1m"
_RESET = "\033[0m"
_GRAY  = "\033[90m"
_CYAN  = "\033[36m"


def _print_banner() -> None:
    print(f"\n{_BOLD}{_CYAN}  argo{_RESET}  terminal coding agent\n")


def _pick_session(cwd: str) -> ArgoSession:
    sessions = list_sessions(cwd)
    if not sessions:
        return new_session(cwd)

    print(f"{_GRAY}  Recent sessions in {cwd}:{_RESET}")
    for i, s in enumerate(sessions, 1):
        from datetime import datetime
        dt = datetime.fromtimestamp(s.updated_at).strftime("%Y-%m-%d %H:%M")
        msg_count = len(s.messages)
        print(f"  [{i}] {dt}  ({msg_count} messages)")
    print(f"  [N] New session")
    print()

    while True:
        choice = input("  Choose > ").strip()
        if choice.upper() == "N" or choice == "":
            return new_session(cwd)
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(sessions):
                return sessions[idx]
        except ValueError:
            pass
        print(f"  Invalid choice.")


async def main() -> None:
    _print_banner()

    try:
        llm_cfg, agent_cfg = load_config()
    except FileNotFoundError as e:
        print(f"  Error: {e}", file=sys.stderr)
        sys.exit(1)

    cwd = os.getcwd()
    argo_session = _pick_session(cwd)

    session_repo = InMemorySessionRepo(initial_messages=list(argo_session.messages))
    checkpoint_repo = InMemoryCheckpointRepo()
    file_repo = NoopFileRepo()
    uow_factory = lambda: InMemoryUoW(session_repo, checkpoint_repo, file_repo)

    llm = OpenAILLM(llm_cfg)
    json_parser = RepairJSONParser()
    workspace = resolve_workspace(cwd)

    agent = build_coding_agent(
        llm=llm,
        json_parser=json_parser,
        agent_config=agent_cfg,
        uow_factory=uow_factory,
        session_id=argo_session.session_id,
        workspace=workspace,
    )

    app = ArgoApp(
        agent=agent,
        argo_session=argo_session,
        save_fn=save_session,
        model_name=llm_cfg.model_name,
    )
    await app.run()
```

- [ ] **Step 3: Smoke test that the entry point resolves without error**

```bash
cd /Users/nuoyunzhibo/xu/project/github-project/my-agent/ny-agent
python -c "import asyncio; import argo.main; print('OK')"
```

Expected: `OK` (no import errors).

- [ ] **Step 4: Commit**

```bash
git add argo/__main__.py argo/main.py
git commit -m "feat(argo): add startup orchestrator and entry point"
```

---

## Task 7: Run the full test suite and fix any issues

**Files:**
- Modify: any file flagged by failing tests

- [ ] **Step 1: Run all argo tests**

```bash
cd /Users/nuoyunzhibo/xu/project/github-project/my-agent/ny-agent
python -m pytest argo/tests/ -v
```

Expected: all tests pass. If any fail, fix the root cause (do not delete tests).

- [ ] **Step 2: Run existing api tests to check for regressions**

```bash
cd /Users/nuoyunzhibo/xu/project/github-project/my-agent/ny-agent
python -m pytest api/tests/ -v --ignore=api/tests/test_status_routes.py
```

Expected: same pass count as before (59 tests passing). The pre-existing failure in `test_status_routes.py` is expected and unrelated.

- [ ] **Step 3: Manual smoke test**

```bash
cd /some/project/directory   # or stay in ny-agent/
python -m argo
```

Expected:
- Prints `argo  terminal coding agent` banner
- Shows session picker (or directly enters TUI if no history)
- Entering alternate screen: terminal clears, header bar with `argo  deepseek-chat  <cwd>` appears
- Typing a message and pressing Enter triggers agent; tool events render with `⟳`/`✓`/`✗`
- `/help` shows command list; `/clear` clears screen; `/exit` restores terminal
- After conversation, `~/.argo/sessions/<id>.json` exists with messages

- [ ] **Step 4: Commit any fixes made**

```bash
git add -p   # stage only related changes
git commit -m "fix(argo): address issues found in test and smoke-test pass"
```

---

## Self-Review Checklist (completed inline)

1. **Spec coverage:**
   - `argo/` directory ✓ (Tasks 1-6)
   - `config.py` with sys.path patch ✓ (Task 1)
   - `session.py` with atomic save/load/list ✓ (Task 2)
   - `InMemorySessionRepo` / `InMemoryCheckpointRepo` / `NoopFileRepo` ✓ (Task 3)
   - `InMemoryUoW` context manager ✓ (Task 3)
   - `uow_factory` captures same repo instances in closure ✓ (Task 3, test 6)
   - `renderer.py` with all event types ✓ (Task 4)
   - `app.py` with alternate screen, header, slash commands, autosave ✓ (Task 5)
   - `main.py` startup orchestration with session picker ✓ (Task 6)
   - `__main__.py` entry point ✓ (Task 6)
   - Session picker: max 5 sessions, sorted desc by updated_at, filtered by CWD ✓ (Task 2 + Task 6)
   - Error handling: missing config → stderr + exit ✓ (Task 6); corrupt session skipped ✓ (Task 2)
   - Ctrl+C during agent turn → `[interrupted]` ✓ (Task 5 `_run_agent_turn`)
   - Tests in `argo/tests/` ✓ (Tasks 1-4)
   - No new pip dependencies ✓

2. **No placeholders:** all code shown in full.

3. **Type consistency:**
   - `new_session()` returns `ArgoSession` ✓
   - `save_session(ArgoSession)` used in Task 5 `save_fn` param ✓
   - `InMemoryUoW.__aenter__` returns `InMemoryUoW` (typed as `"InMemoryUoW"`) ✓
   - `build_coding_agent()` called with correct keyword args matching `coding_agent.py:27-34` ✓
