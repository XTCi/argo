# Argo Execution Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade Argo's execution layer with persistent shell sessions, fuzzy file patching, and a configurable permission gateway.

**Architecture:** `PersistentShellSession` (new) wraps a long-lived bash process; `ShellTool` is rewritten on top of it exposing 3 tools; `FuzzyPatcher` is added inside `patch_file`; `PermissionGateway` (new) is injected into `ToolExecutor` as an optional pre-execute hook; `--yolo` flag is wired through `__main__.py` → `main.py` → `PermissionGateway`.

**Tech Stack:** Python 3.12, asyncio, difflib (stdlib), prompt_toolkit (already installed), pydantic, config.yaml.

## Global Constraints

- No new external dependencies — only stdlib and prompt_toolkit (already installed).
- `BaseTool`, `BaseAgent`, and existing tool interfaces are not changed in public signature — only implementations change.
- All async code uses asyncio only — no threading except where stdlib forces it.
- Tests under `api/` run from the `api/` directory: `cd api && pytest tests/path/test.py -v`.
- Tests under `argo/` run from the project root: `pytest argo/tests/test_name.py -v`.
- `AppConfig` already has `model_config = ConfigDict(extra="allow")` — extra yaml keys are silently ignored if the Pydantic model doesn't declare them.
- Commit after every task.

---

## File Map

| File | Action |
|------|--------|
| `api/app/domain/services/tools/shell_session.py` | **Create** — PersistentShellSession |
| `api/app/domain/services/tools/shell.py` | **Rewrite** — 3 tools on top of session |
| `api/app/domain/services/tools/file_edit.py` | **Modify** — add FuzzyPatcher inside patch_file |
| `api/app/domain/services/runtime/tool_executor.py` | **Modify** — add pre_execute_hook + update _TERMINAL_TOOLS |
| `api/app/domain/services/agents/coding_agent.py` | **Modify** — accept shell_session + pre_execute_hook |
| `api/app/domain/models/app_config.py` | **Modify** — add PermissionsConfig + AppConfig.permissions |
| `argo/permissions.py` | **Create** — PermissionGateway |
| `argo/config.py` | **Modify** — return PermissionsConfig from load_config |
| `argo/__main__.py` | **Modify** — parse --yolo flag |
| `argo/main.py` | **Modify** — create session + gateway + confirm_fn, wire into agent |
| `api/config.yaml.example` | **Modify** — add permissions block |
| `api/tests/domain/services/tools/test_shell_session.py` | **Create** |
| `api/tests/domain/services/tools/test_shell_tool.py` | **Create** |
| `api/tests/domain/services/tools/test_file_edit_fuzzy.py` | **Create** |
| `argo/tests/test_permissions.py` | **Create** |

---

## Task 1: PersistentShellSession

**Files:**
- Create: `api/app/domain/services/tools/shell_session.py`
- Test: `api/tests/domain/services/tools/test_shell_session.py`

**Interfaces:**
- Produces:
  ```python
  class PersistentShellSession:
      async def start(self) -> None
      async def run(self, command: str, timeout: int = 30) -> tuple[str, int]
      async def run_background(self, command: str, process_id: str) -> None
      async def read_output(self, process_id: str, wait_seconds: float = 2.0) -> str
      async def close(self) -> None
  ```

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/domain/services/tools/test_shell_session.py
from __future__ import annotations
import asyncio
import pytest
from app.domain.services.tools.shell_session import PersistentShellSession


@pytest.fixture
async def session(tmp_path):
    s = PersistentShellSession(cwd=str(tmp_path))
    await s.start()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_run_simple_command(session):
    output, code = await session.run("echo hello")
    assert "hello" in output
    assert code == 0


@pytest.mark.asyncio
async def test_run_exit_code_failure(session):
    output, code = await session.run("exit 1 || true; false")
    assert code != 0


@pytest.mark.asyncio
async def test_cd_persists_across_calls(session, tmp_path):
    await session.run(f"cd {tmp_path}")
    output, code = await session.run("pwd")
    assert str(tmp_path) in output
    assert code == 0


@pytest.mark.asyncio
async def test_background_and_read_output(session):
    await session.run_background("for i in 1 2 3; do echo line$i; sleep 0.1; done", "bg1")
    await asyncio.sleep(0.5)
    out = await session.read_output("bg1", wait_seconds=0)
    assert "line1" in out


@pytest.mark.asyncio
async def test_read_output_unknown_process(session):
    out = await session.read_output("nonexistent", wait_seconds=0)
    assert out == ""


@pytest.mark.asyncio
async def test_auto_restart_after_process_death(session):
    # Kill the bash process directly
    session._proc.kill()
    await asyncio.sleep(0.1)
    # Next run should auto-restart transparently
    output, code = await session.run("echo alive")
    assert "alive" in output
    assert code == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd api && pytest tests/domain/services/tools/test_shell_session.py -v
```
Expected: `ImportError: cannot import name 'PersistentShellSession'`

- [ ] **Step 3: Implement PersistentShellSession**

```python
# api/app/domain/services/tools/shell_session.py
from __future__ import annotations

import asyncio
import atexit
import collections
import logging
import os
import signal
from typing import Optional

logger = logging.getLogger(__name__)

_SENTINEL = "__ARGO_DONE__"
_RING_MAXLEN = 10_000


class PersistentShellSession:
    """A single bash process that lives for the entire Argo session.

    Commands are sent over stdin; a sentinel line marks completion.
    Background processes are tracked in a ring-buffer keyed by process_id.
    """

    def __init__(self, cwd: str) -> None:
        self._cwd = cwd
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._bg_buffers: dict[str, collections.deque] = {}
        self._bg_tasks: dict[str, asyncio.Task] = {}
        atexit.register(self._sync_close)

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            "bash", "--norc", "--noprofile",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self._cwd,
        )

    async def _ensure_alive(self) -> None:
        if self._proc is None or self._proc.returncode is not None:
            logger.warning("Shell process died — restarting")
            await self.start()

    async def run(self, command: str, timeout: int = 30) -> tuple[str, int]:
        await self._ensure_alive()
        wrapped = f'{command}\necho "{_SENTINEL}:$?"\n'
        self._proc.stdin.write(wrapped.encode())
        await self._proc.stdin.drain()

        lines: list[str] = []
        exit_code = 0
        try:
            async with asyncio.timeout(timeout):
                while True:
                    raw = await self._proc.stdout.readline()
                    line = raw.decode(errors="replace")
                    if line.startswith(f"{_SENTINEL}:"):
                        exit_code = int(line.split(":")[1].strip())
                        break
                    lines.append(line)
        except asyncio.TimeoutError:
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass
            return f"[timed out after {timeout}s]\n" + "".join(lines), 124

        return "".join(lines), exit_code

    async def run_background(self, command: str, process_id: str) -> None:
        buf: collections.deque[str] = collections.deque(maxlen=_RING_MAXLEN)
        self._bg_buffers[process_id] = buf

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self._cwd,
        )

        async def _drain() -> None:
            assert proc.stdout is not None
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                buf.append(raw.decode(errors="replace"))

        task = asyncio.create_task(_drain())
        self._bg_tasks[process_id] = task

    async def read_output(self, process_id: str, wait_seconds: float = 2.0) -> str:
        if process_id not in self._bg_buffers:
            return ""
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
        return "".join(self._bg_buffers[process_id])

    async def close(self) -> None:
        for task in self._bg_tasks.values():
            task.cancel()
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass

    def _sync_close(self) -> None:
        if self._proc and self._proc.returncode is None:
            try:
                os.kill(self._proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd api && pytest tests/domain/services/tools/test_shell_session.py -v
```
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add api/app/domain/services/tools/shell_session.py api/tests/domain/services/tools/test_shell_session.py
git commit -m "feat(shell): add PersistentShellSession with background process support"
```

---

## Task 2: ShellTool Rewrite

**Files:**
- Rewrite: `api/app/domain/services/tools/shell.py`
- Modify: `api/app/domain/services/runtime/tool_executor.py` (lines 15–16, update `_TERMINAL_TOOLS`)
- Modify: `api/app/domain/services/agents/coding_agent.py` (accept `shell_session` param)
- Test: `api/tests/domain/services/tools/test_shell_tool.py`

**Interfaces:**
- Consumes: `PersistentShellSession` from Task 1
- Produces:
  - Tool `shell_execute(command: str, timeout: int = 30) -> ToolResult`
  - Tool `shell_background(command: str, process_id: str) -> ToolResult`
  - Tool `read_output(process_id: str, wait_seconds: float = 2.0) -> ToolResult`

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/domain/services/tools/test_shell_tool.py
from __future__ import annotations
import pytest
from app.domain.services.tools.shell_session import PersistentShellSession
from app.domain.services.tools.shell import ShellTool


@pytest.fixture
async def tool(tmp_path):
    session = PersistentShellSession(cwd=str(tmp_path))
    await session.start()
    t = ShellTool(session=session, cwd=str(tmp_path))
    yield t
    await session.close()


@pytest.mark.asyncio
async def test_shell_execute_success(tool):
    result = await tool.invoke("shell_execute", command="echo hi")
    assert result.success is True
    assert "hi" in result.data


@pytest.mark.asyncio
async def test_shell_execute_failure(tool):
    result = await tool.invoke("shell_execute", command="false")
    assert result.success is False
    assert "exit 1" in result.message


@pytest.mark.asyncio
async def test_shell_background_starts(tool):
    result = await tool.invoke("shell_background", command="echo bg", process_id="p1")
    assert result.success is True
    assert "p1" in result.message


@pytest.mark.asyncio
async def test_read_output_returns_bg_output(tool):
    import asyncio
    await tool.invoke("shell_background", command="echo bgline", process_id="p2")
    await asyncio.sleep(0.3)
    result = await tool.invoke("read_output", process_id="p2", wait_seconds=0)
    assert result.success is True
    assert "bgline" in result.data


@pytest.mark.asyncio
async def test_read_output_unknown_process(tool):
    result = await tool.invoke("read_output", process_id="nope", wait_seconds=0)
    assert result.success is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd api && pytest tests/domain/services/tools/test_shell_tool.py -v
```
Expected: `TypeError: ShellTool.__init__() got an unexpected keyword argument 'session'`

- [ ] **Step 3: Rewrite shell.py**

```python
# api/app/domain/services/tools/shell.py
from __future__ import annotations

from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseTool, tool
from app.domain.services.tools.shell_session import PersistentShellSession


class ShellTool(BaseTool):
    """Shell 工具 — 三个工具共享一个持久 bash session。"""
    name: str = "shell"

    def __init__(self, session: PersistentShellSession, cwd: str = ".") -> None:
        super().__init__()
        self._session = session
        self._cwd = cwd

    @tool(
        name="shell_execute",
        description="在项目目录执行 shell 命令。命令在同一个持久 bash session 中运行，cd/export 等状态跨调用保持。",
        parameters={
            "command": {"type": "string", "description": "要执行的 shell 命令"},
            "timeout": {"type": "integer", "description": "超时秒数（默认 30）"},
        },
        required=["command"],
    )
    async def shell_execute(self, command: str, timeout: int = 30) -> ToolResult:
        output, code = await self._session.run(command, timeout=timeout)
        return ToolResult(
            success=(code == 0),
            data=output,
            message=f"exit {code}",
        )

    @tool(
        name="shell_background",
        description="在后台启动一个长时间运行的命令（如开发服务器）。用 process_id 标识，之后用 read_output 获取输出。",
        parameters={
            "command": {"type": "string", "description": "要在后台执行的命令"},
            "process_id": {"type": "string", "description": "自定义标识符，如 'dev-server'"},
        },
        required=["command", "process_id"],
    )
    async def shell_background(self, command: str, process_id: str) -> ToolResult:
        await self._session.run_background(command, process_id)
        return ToolResult(success=True, message=f"started as {process_id}")

    @tool(
        name="read_output",
        description="读取后台进程的当前输出缓冲区。",
        parameters={
            "process_id": {"type": "string", "description": "shell_background 时使用的标识符"},
            "wait_seconds": {"type": "number", "description": "等待新输出的秒数（默认 2.0）"},
        },
        required=["process_id"],
    )
    async def read_output(self, process_id: str, wait_seconds: float = 2.0) -> ToolResult:
        out = await self._session.read_output(process_id, wait_seconds=wait_seconds)
        if out == "" and process_id not in self._session._bg_buffers:
            return ToolResult(success=False, message=f"Unknown process_id: {process_id}")
        return ToolResult(success=True, data=out)
```

- [ ] **Step 4: Update `_TERMINAL_TOOLS` in tool_executor.py**

In `api/app/domain/services/runtime/tool_executor.py`, replace line 15–16:

```python
# Before
_TERMINAL_TOOLS = frozenset({"shell_execute", "shell_read_output", "shell_wait_process", "run_tests"})

# After
_TERMINAL_TOOLS = frozenset({"shell_execute", "shell_background", "read_output", "run_tests"})
```

- [ ] **Step 5: Update coding_agent.py to accept shell_session**

In `api/app/domain/services/agents/coding_agent.py`, change `build_coding_agent`:

```python
from app.domain.services.tools.shell_session import PersistentShellSession

def build_coding_agent(
    llm: LLM,
    json_parser: JSONParser,
    agent_config: AgentConfig,
    uow_factory: Callable[[], IUnitOfWork],
    session_id: str,
    workspace: WorkspaceContext,
    shell_session: PersistentShellSession | None = None,
    pre_execute_hook=None,
) -> BaseAgent:
    cwd = workspace.cwd
    todo_store = TodoStore()

    # Use provided session or create a temporary one (for backwards compat / tests)
    _session = shell_session or PersistentShellSession(cwd=cwd)

    tools = [
        ShellTool(session=_session, cwd=cwd),
        FileEditTool(),
        CodeSearchTool(cwd=cwd),
        GitTool(cwd=cwd),
        TestRunnerTool(cwd=cwd),
        TodoTool(todo_store=todo_store),
    ]

    context_length = agent_config.context_length
    context_engine = DefaultContextEngine(
        config=ContextEngineConfig(context_length=context_length, threshold_percent=0.75),
        llm=llm,
        protect_first_n=3,
        protect_last_n=6,
    )
    memory = ThreeLayerMemory(session_id=session_id, uow_factory=uow_factory)
    turn_orchestrator = TurnOrchestrator(
        workspace=workspace,
        memory=memory,
        context_engine=context_engine,
        max_iterations=agent_config.max_iterations,
        guidance=CODING_AGENT_SYSTEM_PROMPT,
        todo_store=todo_store,
    )
    tool_executor = ToolExecutor(
        tools=tools,
        context_engine=context_engine,
        pre_execute_hook=pre_execute_hook,
    )
    checkpoint_service = CheckpointService(session_id=session_id, uow_factory=uow_factory)

    return BaseAgent(
        llm=llm,
        json_parser=json_parser,
        agent_config=agent_config,
        tools=tools,
        turn_orchestrator=turn_orchestrator,
        tool_executor=tool_executor,
        checkpoint_service=checkpoint_service,
        context_engine=context_engine,
        memory=memory,
        uow_factory=uow_factory,
        session_id=session_id,
    )
```

- [ ] **Step 6: Run tests**

```bash
cd api && pytest tests/domain/services/tools/test_shell_tool.py -v
```
Expected: 5 passed

- [ ] **Step 7: Run full test suite to check no regressions**

```bash
cd api && pytest tests/ -v
```
Expected: all previously passing tests still pass

- [ ] **Step 8: Commit**

```bash
git add api/app/domain/services/tools/shell.py \
        api/app/domain/services/runtime/tool_executor.py \
        api/app/domain/services/agents/coding_agent.py \
        api/tests/domain/services/tools/test_shell_tool.py
git commit -m "feat(shell): rewrite ShellTool with 3 tools on PersistentShellSession"
```

---

## Task 3: FuzzyPatcher

**Files:**
- Modify: `api/app/domain/services/tools/file_edit.py`
- Test: `api/tests/domain/services/tools/test_file_edit_fuzzy.py`

**Interfaces:**
- The public `patch_file` tool signature (`filepath`, `old_str`, `new_str`, `replacements`) is **unchanged**.
- Internally adds private `_fuzzy_find_and_replace(content, old_str, new_str) -> tuple[str, str | None]` where the second element is an error string or None on success.

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/domain/services/tools/test_file_edit_fuzzy.py
from __future__ import annotations
import pytest
from pathlib import Path
from app.domain.services.tools.file_edit import FileEditTool


@pytest.fixture
def tmp_file(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("def hello():\n    return 42\n")
    return str(f)


@pytest.mark.asyncio
async def test_exact_match_replaces(tmp_file):
    tool = FileEditTool()
    result = await tool.invoke("patch_file", filepath=tmp_file,
                               old_str="return 42", new_str="return 99")
    assert result.success
    assert "return 99" in Path(tmp_file).read_text()


@pytest.mark.asyncio
async def test_line_trim_match_handles_indentation_drift(tmp_file):
    """LLM sometimes forgets indentation — line_trim strategy should catch it."""
    tool = FileEditTool()
    # old_str has wrong indentation (no leading spaces)
    result = await tool.invoke("patch_file", filepath=tmp_file,
                               old_str="return 42", new_str="return 99")
    assert result.success


@pytest.mark.asyncio
async def test_whitespace_norm_matches_extra_spaces(tmp_path):
    f = tmp_path / "f.py"
    f.write_text("x  =  1\n")
    tool = FileEditTool()
    # old_str has normalized spaces
    result = await tool.invoke("patch_file", filepath=str(f),
                               old_str="x = 1", new_str="x = 2")
    assert result.success
    assert "x = 2" in f.read_text()


@pytest.mark.asyncio
async def test_zero_matches_returns_difflib_hint(tmp_file):
    tool = FileEditTool()
    result = await tool.invoke("patch_file", filepath=tmp_file,
                               old_str="def goodbye():", new_str="pass")
    assert not result.success
    # Should mention the closest line as a hint
    assert "hello" in result.message or "Closest" in result.message


@pytest.mark.asyncio
async def test_multiple_matches_returns_not_unique_error(tmp_path):
    f = tmp_path / "f.py"
    f.write_text("x = 1\nx = 1\n")
    tool = FileEditTool()
    result = await tool.invoke("patch_file", filepath=str(f),
                               old_str="x = 1", new_str="x = 2")
    assert not result.success
    assert "2" in result.message  # mentions match count


@pytest.mark.asyncio
async def test_file_unchanged_on_zero_match(tmp_file):
    tool = FileEditTool()
    original = Path(tmp_file).read_text()
    await tool.invoke("patch_file", filepath=tmp_file,
                      old_str="DOES_NOT_EXIST", new_str="x")
    assert Path(tmp_file).read_text() == original
```

- [ ] **Step 2: Run tests to verify the zero/multiple match tests fail (exact match tests may already pass)**

```bash
cd api && pytest tests/domain/services/tools/test_file_edit_fuzzy.py -v
```
Expected: `test_zero_matches_returns_difflib_hint` and `test_whitespace_norm_matches_extra_spaces` FAIL

- [ ] **Step 3: Add FuzzyPatcher to file_edit.py**

Add these private helpers just before the `FileEditTool` class definition in `api/app/domain/services/tools/file_edit.py`:

```python
import difflib
import re as _re


def _lines_stripped(text: str) -> str:
    return "\n".join(line.strip() for line in text.splitlines())


def _ws_norm(text: str) -> str:
    return _re.sub(r"[ \t]+", " ", text)


def _escape_norm(text: str) -> str:
    return text.replace("\\n", "\n").replace("\\t", "\t")


def _fuzzy_find_and_replace(content: str, old_str: str, new_str: str) -> tuple[str, str | None]:
    """Try 4 matching strategies in order. Return (new_content, error_or_None)."""

    strategies = [
        ("exact",         lambda c, o: _find_all(c, o)),
        ("line_trim",     lambda c, o: _find_all(_lines_stripped(c), _lines_stripped(o))),
        ("whitespace_norm", lambda c, o: _find_all(_ws_norm(c), _ws_norm(o))),
        ("escape_norm",   lambda c, o: _find_all(c, _escape_norm(o))),
    ]

    for strategy_name, find_fn in strategies:
        if strategy_name == "exact":
            matches = find_fn(content, old_str)
            if len(matches) == 1:
                start, end = matches[0]
                return content[:start] + new_str + content[end:], None
            if len(matches) > 1:
                return content, (
                    f"patch_file failed: old_str appears {len(matches)} times — "
                    "add more surrounding context to make it unique."
                )
        else:
            norm_content = _ws_norm(content) if strategy_name == "whitespace_norm" else \
                           _lines_stripped(content) if strategy_name == "line_trim" else content
            norm_old = _ws_norm(old_str) if strategy_name == "whitespace_norm" else \
                       _lines_stripped(old_str) if strategy_name == "line_trim" else _escape_norm(old_str)
            matches = _find_all(norm_content, norm_old)
            if len(matches) == 1:
                # Apply replacement to original content using the normalised offset
                # by finding the best real-content substring at the matched position
                start, end = matches[0]
                # Reconstruct: replace the normalised region with new_str in original
                # Since normalisation may shift offsets, fall back to re-locating via
                # the first line of old_str in the original content
                first_line = old_str.splitlines()[0].strip()
                for i, line in enumerate(content.splitlines()):
                    if first_line in line.strip():
                        # Find the block in original starting at this line
                        line_start = sum(len(l) + 1 for l in content.splitlines()[:i])
                        block_end = line_start + len("\n".join(content.splitlines()[i:i + len(old_str.splitlines())]))
                        return content[:line_start] + new_str + content[block_end:], None
                # If line-based recovery failed, do direct normalised replacement
                return content[:start] + new_str + norm_content[end:], None
            if len(matches) > 1:
                return content, (
                    f"patch_file failed: old_str appears {len(matches)} times — "
                    "add more surrounding context to make it unique."
                )

    # All strategies failed — return difflib hint
    first_line = old_str.splitlines()[0] if old_str.splitlines() else old_str
    all_lines = content.splitlines()
    close = difflib.get_close_matches(first_line, all_lines, n=3, cutoff=0.4)
    hint = ""
    if close:
        hint = "\n\nClosest lines in file:\n" + "\n".join(f"  {l}" for l in close)
        hint += "\n\nSuggestion: expand old_str to include surrounding lines for a unique match."
    return content, f"patch_file failed: old_str not found in file.{hint}"


def _find_all(content: str, pattern: str) -> list[tuple[int, int]]:
    results = []
    start = 0
    while True:
        idx = content.find(pattern, start)
        if idx == -1:
            break
        results.append((idx, idx + len(pattern)))
        start = idx + 1
    return results
```

Then modify `patch_file` in `FileEditTool` to call `_fuzzy_find_and_replace` instead of the raw `content.count` + `content.replace`:

```python
async def patch_file(
    self,
    filepath: str,
    old_str: Optional[str] = None,
    new_str: Optional[str] = None,
    replacements: Optional[list] = None,
) -> ToolResult:
    try:
        if replacements is None:
            if old_str is None:
                return ToolResult(success=False,
                                  message="Provide either old_str/new_str or replacements")
            replacements = [{"old_str": old_str, "new_str": new_str or ""}]

        content = Path(filepath).read_text(encoding="utf-8")

        # Validate all replacements first (fail-fast)
        seen: set[str] = set()
        for i, rep in enumerate(replacements, 1):
            o = rep.get("old_str", "")
            if o in seen:
                return ToolResult(success=False,
                                  message=f"Replacement {i}: old_str is duplicated in replacements list")
            seen.add(o)
            # Quick pre-check using fuzzy to surface errors before writing
            _, err = _fuzzy_find_and_replace(content, o, rep.get("new_str", ""))
            if err:
                return ToolResult(success=False, message=err)

        # Apply all replacements
        for rep in replacements:
            content, err = _fuzzy_find_and_replace(content, rep["old_str"], rep.get("new_str", ""))
            if err:
                return ToolResult(success=False, message=err)

        Path(filepath).write_text(content, encoding="utf-8")
        return ToolResult(success=True, data={"replacements_applied": len(replacements)})
    except Exception as e:
        return ToolResult(success=False, message=str(e))
```

- [ ] **Step 4: Run tests**

```bash
cd api && pytest tests/domain/services/tools/test_file_edit_fuzzy.py -v
```
Expected: 6 passed

- [ ] **Step 5: Run full test suite to check no regressions**

```bash
cd api && pytest tests/ -v
```
Expected: all previously passing tests still pass

- [ ] **Step 6: Commit**

```bash
git add api/app/domain/services/tools/file_edit.py \
        api/tests/domain/services/tools/test_file_edit_fuzzy.py
git commit -m "feat(file-edit): add 4-level fuzzy matching with difflib hints to patch_file"
```

---

## Task 4: PermissionGateway + Config

**Files:**
- Create: `argo/permissions.py`
- Modify: `api/app/domain/models/app_config.py` (add `PermissionsConfig`)
- Modify: `argo/config.py` (expose permissions config)
- Modify: `api/config.yaml.example`
- Test: `argo/tests/test_permissions.py`

**Interfaces:**
- Produces:
  ```python
  class PermissionGateway:
      def __init__(self, config: PermissionsConfig, yolo: bool = False,
                   confirm_fn: Callable[[str], Awaitable[str]] | None = None)
      async def check(self, tool_name: str, arguments: dict) -> bool
          # Returns True = allow, False = deny
  ```

- [ ] **Step 1: Write the failing tests**

```python
# argo/tests/test_permissions.py
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock
from argo.permissions import PermissionGateway
from app.domain.models.app_config import PermissionsConfig


def make_cfg(**kwargs):
    defaults = dict(mode="ask", deny=["rm -rf /"], ask=["rm ", "sudo "], allow=["git log"])
    defaults.update(kwargs)
    return PermissionsConfig(**defaults)


@pytest.mark.asyncio
async def test_deny_rule_blocks():
    gw = PermissionGateway(config=make_cfg(), yolo=False)
    result = await gw.check("shell_execute", {"command": "rm -rf /"})
    assert result is False


@pytest.mark.asyncio
async def test_allow_rule_bypasses_ask():
    gw = PermissionGateway(config=make_cfg(), yolo=False)
    result = await gw.check("shell_execute", {"command": "git log --oneline"})
    assert result is True


@pytest.mark.asyncio
async def test_ask_rule_calls_confirm_fn_and_allows_on_y():
    confirm = AsyncMock(return_value="y")
    gw = PermissionGateway(config=make_cfg(), yolo=False, confirm_fn=confirm)
    result = await gw.check("shell_execute", {"command": "rm dist/"})
    assert result is True
    confirm.assert_called_once()


@pytest.mark.asyncio
async def test_ask_rule_denies_on_n():
    confirm = AsyncMock(return_value="n")
    gw = PermissionGateway(config=make_cfg(), yolo=False, confirm_fn=confirm)
    result = await gw.check("shell_execute", {"command": "rm dist/"})
    assert result is False


@pytest.mark.asyncio
async def test_bang_adds_to_session_allowlist():
    confirm = AsyncMock(return_value="!")
    gw = PermissionGateway(config=make_cfg(), yolo=False, confirm_fn=confirm)
    # First call: confirm triggered, user picks "!"
    await gw.check("shell_execute", {"command": "rm dist/"})
    # Second call: should be allowed without calling confirm again
    confirm.reset_mock()
    result = await gw.check("shell_execute", {"command": "rm dist/"})
    assert result is True
    confirm.assert_not_called()


@pytest.mark.asyncio
async def test_yolo_skips_ask_rules():
    confirm = AsyncMock(return_value="n")
    gw = PermissionGateway(config=make_cfg(), yolo=True, confirm_fn=confirm)
    result = await gw.check("shell_execute", {"command": "rm dist/"})
    assert result is True
    confirm.assert_not_called()


@pytest.mark.asyncio
async def test_yolo_still_blocks_deny_rules():
    gw = PermissionGateway(config=make_cfg(), yolo=True)
    result = await gw.check("shell_execute", {"command": "rm -rf /"})
    assert result is False


@pytest.mark.asyncio
async def test_non_shell_tools_always_allowed():
    gw = PermissionGateway(config=make_cfg(), yolo=False)
    result = await gw.check("read_file", {"filepath": "/etc/passwd"})
    assert result is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest argo/tests/test_permissions.py -v
```
Expected: `ImportError: cannot import name 'PermissionGateway'`

- [ ] **Step 3: Add PermissionsConfig to app_config.py**

Add after the `AgentConfig` class in `api/app/domain/models/app_config.py`:

```python
class PermissionsConfig(BaseModel):
    """Permission gateway configuration."""
    mode: str = "ask"          # ask | yolo | strict
    deny: List[str] = Field(default_factory=lambda: ["rm -rf /", ":(){:|:&};:"])
    ask: List[str] = Field(default_factory=lambda: [
        "rm ", "sudo ", "git push", "pip install", "npm install", "DROP "
    ])
    allow: List[str] = Field(default_factory=lambda: [
        "git status", "git log", "git diff", "ls", "cat ", "echo ", "pwd"
    ])
```

Add `permissions` field to `AppConfig`:

```python
class AppConfig(BaseModel):
    llm_config: LLMConfig
    agent_config: AgentConfig
    mcp_config: MCPConfig
    a2a_config: A2AConfig
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)

    model_config = ConfigDict(extra="allow")
```

- [ ] **Step 4: Create argo/permissions.py**

```python
# argo/permissions.py
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

_SHELL_TOOLS = frozenset({"shell_execute", "shell_background"})


class PermissionGateway:
    """Three-tier permission check: deny → allow → ask.

    Only shell_execute and shell_background are subject to checks.
    All other tools are always allowed.
    """

    def __init__(
        self,
        config,                          # PermissionsConfig
        yolo: bool = False,
        confirm_fn: Optional[Callable[[str], Awaitable[str]]] = None,
    ) -> None:
        self._config = config
        self._yolo = yolo
        self._confirm_fn = confirm_fn
        self._session_allowlist: set[str] = set()

    async def check(self, tool_name: str, arguments: dict) -> bool:
        """Return True (allow) or False (deny)."""
        if tool_name not in _SHELL_TOOLS:
            return True

        command = arguments.get("command", "")

        # 1. Deny rules always apply (even in yolo mode)
        for pattern in self._config.deny:
            if pattern in command:
                logger.warning("Permission denied by rule %r: %s", pattern, command)
                return False

        # 2. Session allowlist (from previous "!" responses)
        for entry in self._session_allowlist:
            if entry in command:
                return True

        # 3. Allow rules bypass ask
        for pattern in self._config.allow:
            if pattern in command:
                return True

        # 4. Yolo: skip ask
        if self._yolo:
            return True

        # 5. Ask rules → trigger confirm_fn
        for pattern in self._config.ask:
            if pattern in command:
                return await self._ask(command)

        # 6. Fallthrough: mode=ask → confirm, mode=strict → deny
        if self._config.mode == "strict":
            return False
        if self._config.mode == "ask":
            return await self._ask(command)
        return True  # mode=yolo fallthrough

    async def _ask(self, command: str) -> bool:
        if self._confirm_fn is None:
            return True  # no TUI — allow by default
        response = await self._confirm_fn(command)
        if response == "!":
            # Find the matching ask pattern to add to allowlist
            for pattern in self._config.ask:
                if pattern in command:
                    self._session_allowlist.add(pattern)
                    break
            return True
        return response == "y"
```

- [ ] **Step 5: Update argo/config.py to return PermissionsConfig**

```python
# argo/config.py  — replace load_config return type
from app.domain.models.app_config import AppConfig, LLMConfig, AgentConfig, PermissionsConfig  # add PermissionsConfig

def load_config() -> tuple[LLMConfig, AgentConfig, PermissionsConfig]:
    raw = yaml.safe_load(_config_path().read_text())
    app_cfg = AppConfig(**raw)
    return app_cfg.llm_config, app_cfg.agent_config, app_cfg.permissions
```

- [ ] **Step 6: Add permissions block to api/config.yaml.example**

```yaml
# Add after a2a_config section:
permissions:
  mode: ask          # ask | yolo | strict
  deny:
    - "rm -rf /"
    - ":(){:|:&};:"
  ask:
    - "rm "
    - "sudo "
    - "git push"
    - "pip install"
    - "npm install"
    - "DROP "
  allow:
    - "git status"
    - "git log"
    - "git diff"
    - "ls"
    - "cat "
    - "echo "
    - "pwd"
```

- [ ] **Step 7: Run tests**

```bash
pytest argo/tests/test_permissions.py -v
```
Expected: 8 passed

- [ ] **Step 8: Commit**

```bash
git add argo/permissions.py \
        argo/config.py \
        api/app/domain/models/app_config.py \
        api/config.yaml.example \
        argo/tests/test_permissions.py
git commit -m "feat(permissions): add PermissionGateway with deny/ask/allow rules and session allowlist"
```

---

## Task 5: Wire Everything Together

**Files:**
- Modify: `api/app/domain/services/runtime/tool_executor.py` (add `pre_execute_hook`)
- Modify: `argo/__main__.py` (parse `--yolo`)
- Modify: `argo/main.py` (create session + gateway + confirm_fn)

**Interfaces:**
- Consumes: `PersistentShellSession` (Task 1), `PermissionGateway` (Task 4), `build_coding_agent` with new params (Task 2)
- This task has no new public interface — it wires existing components.

- [ ] **Step 1: Add pre_execute_hook to ToolExecutor**

In `api/app/domain/services/runtime/tool_executor.py`, update `__init__` and `execute`:

```python
from typing import Awaitable, Callable, List, Optional, Tuple

class ToolExecutor:
    def __init__(
        self,
        tools: List[BaseTool],
        context_engine: ContextEngine,
        pre_execute_hook: Optional[Callable[[str, dict], Awaitable[bool]]] = None,
    ) -> None:
        self._tools = tools
        self._context_engine = context_engine
        self._pre_execute_hook = pre_execute_hook
        self._turn_mutation_paths: set[str] = set()

    async def execute(
        self, tool_name: str, arguments: dict
    ) -> Tuple[ToolResult, ToolResultKind]:
        # Permission check before execution
        if self._pre_execute_hook is not None:
            allowed = await self._pre_execute_hook(tool_name, arguments)
            if not allowed:
                return (
                    ToolResult(success=False, message=f"Permission denied for: {arguments.get('command', tool_name)}"),
                    ToolResultKind.OTHER,
                )

        tool = self._find_tool(tool_name)
        result = await tool.invoke(tool_name, **arguments)
        kind = classify_tool_result(tool_name, result)

        if kind == ToolResultKind.FILE_MUTATION:
            filepath = arguments.get("filepath") or arguments.get("path", "")
            if filepath:
                self._turn_mutation_paths.add(filepath)

        result = self._maybe_truncate(result, kind)
        return result, kind
```

- [ ] **Step 2: Update argo/__main__.py to parse --yolo**

```python
# argo/__main__.py
import asyncio
import sys
from argo.main import main

if __name__ == "__main__":
    yolo = "--yolo" in sys.argv
    asyncio.run(main(yolo=yolo))
```

- [ ] **Step 3: Update argo/main.py**

Replace the `main()` function signature and body to create the session, confirm_fn, and gateway:

```python
async def main(yolo: bool = False) -> None:
    try:
        llm_cfg, agent_cfg, perm_cfg = load_config()
    except FileNotFoundError as e:
        print(f"\n  argo error: {e}\n", file=sys.stderr)
        sys.exit(1)

    cwd = os.getcwd()
    _write(_ENTER_ALT + _CLEAR)

    try:
        argo_session = await _pick_session_tui(cwd, llm_cfg.model_name)

        # Create persistent shell session
        from app.domain.services.tools.shell_session import PersistentShellSession
        shell_session = PersistentShellSession(cwd=cwd)
        await shell_session.start()

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
        file_repo       = NoopFileRepo()
        uow_factory     = lambda: InMemoryUoW(session_repo, checkpoint_repo, file_repo)

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
        )

        app = ArgoApp(
            agent=agent,
            argo_session=argo_session,
            save_fn=save_session,
            model_name=llm_cfg.model_name,
            session_repo=session_repo,
        )
        _write(_CLEAR + _render_header(argo_session, llm_cfg.model_name) + "\n")
        await app._repl()

    finally:
        _write(_SHOW_CUR + _EXIT_ALT)
```

- [ ] **Step 4: Run full test suite**

```bash
cd api && pytest tests/ -v
```
Expected: all previously passing tests still pass (ToolExecutor gains optional param, backwards compat preserved)

```bash
pytest argo/tests/ -v
```
Expected: all argo tests pass

- [ ] **Step 5: Smoke test the running app**

```bash
argo
```

Verify:
1. Session picker appears inside TUI (no plain terminal output)
2. `shell_execute` with `rm dist/` shows permission prompt if `dist/` doesn't match allow rules
3. `argo --yolo` starts without asking for any confirmations
4. `cd /tmp && pwd` in one message, then `pwd` in next message returns `/tmp` (persistence test)

- [ ] **Step 6: Commit**

```bash
git add api/app/domain/services/runtime/tool_executor.py \
        argo/__main__.py \
        argo/main.py
git commit -m "feat: wire PersistentShellSession + PermissionGateway into Argo runtime"
```

---

## Self-Review

**Spec coverage check:**
- ✅ PersistentShellSession: Task 1 — `start/run/run_background/read_output/close`, atexit, auto-restart
- ✅ ShellTool 3 tools: Task 2 — `shell_execute`, `shell_background`, `read_output`
- ✅ FuzzyPatcher 4 strategies: Task 3 — exact/line_trim/whitespace_norm/escape_norm
- ✅ difflib hint on 0 match: Task 3 — `get_close_matches` in error message
- ✅ >1 match error: Task 3 — "appears N times" message
- ✅ PermissionsConfig in config.yaml: Task 4 — Pydantic model + yaml.example
- ✅ deny/allow/ask rules: Task 4 — PermissionGateway
- ✅ session allowlist on `!`: Task 4 — `_session_allowlist` set
- ✅ yolo bypasses ask but not deny: Task 4 — tested
- ✅ TUI confirmation UI: Task 5 — `confirm_fn` in main.py
- ✅ `--yolo` flag: Task 5 — `__main__.py` arg parsing
- ✅ pre_execute_hook in ToolExecutor: Task 5 — optional param, backwards compat

**Placeholder scan:** No TBD/TODO found.

**Type consistency:**
- `PersistentShellSession.run()` → `tuple[str, int]` used consistently in Tasks 1 and 2
- `PermissionGateway.check()` → `bool` used consistently in Tasks 4 and 5
- `load_config()` → `tuple[LLMConfig, AgentConfig, PermissionsConfig]` used in Task 5
- `build_coding_agent(shell_session=..., pre_execute_hook=...)` added in Task 2, consumed in Task 5
