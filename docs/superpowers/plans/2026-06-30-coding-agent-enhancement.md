# Coding Agent Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a structured test runner, an in-session todo tracker, batch file patching, and parallel tool execution so the agent can autonomously complete real coding tasks with a write→test→fix loop.

**Architecture:** Six tasks in dependency order. Tasks 1–4 add new domain components independently. Task 5 upgrades the ToolExecutor scheduler. Task 6 wires everything together in BaseAgent and coding_agent factory. All code follows ny-agent's DDD layering: domain models in `domain/models/`, tool impls in `domain/services/tools/`, runtime state in `domain/services/runtime/`, factory wiring in `domain/services/agents/`.

**Tech Stack:** Python 3.12, asyncio, Pydantic v2, pytest, existing `BaseTool` / `ToolExecutor` / `TurnOrchestrator` patterns in the ny-agent codebase.

## Global Constraints

- All new tools inherit `BaseTool` from `api/app/domain/services/tools/base.py` and use the `@tool` decorator.
- All new domain models use Pydantic v2 `BaseModel`.
- Tests use `pytest-asyncio`; mark async tests with `@pytest.mark.asyncio`. Use `AsyncMock` for async methods, `MagicMock` for sync.
- No new external dependencies — stdlib + packages already installed.
- Run tests from `api/` directory: `cd api && python3.12 -m pytest <path> -v --noconftest`.
- `ToolResultKind` classification sets in `tool_executor.py` must be updated whenever a new tool name is introduced.
- `patch_file` changes must be backward-compatible: the old `(filepath, old_str, new_str)` call signature must continue to work unchanged.

---

### Task 1: TodoItem domain model + TodoStore runtime state

**Files:**
- Create: `api/app/domain/models/todo.py`
- Create: `api/app/domain/services/runtime/todo_store.py`
- Test: `api/tests/domain/services/runtime/test_todo_store.py`

**Interfaces:**
- Produces: `TodoItem(id, content, status)` Pydantic model; `TodoStore` with `.write(todos) -> list[TodoItem]`, `.read() -> list[TodoItem]`, `.format_for_injection() -> str` — consumed by Tasks 2 and 6.

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/domain/services/runtime/test_todo_store.py
import pytest
from app.domain.services.runtime.todo_store import TodoStore


def test_write_replaces_full_list():
    store = TodoStore()
    store.write([{"id": "1", "content": "first", "status": "pending"}])
    store.write([{"id": "2", "content": "second", "status": "in_progress"}])
    items = store.read()
    assert len(items) == 1
    assert items[0].id == "2"


def test_read_returns_empty_list_initially():
    store = TodoStore()
    assert store.read() == []


def test_format_for_injection_renders_status_icons():
    store = TodoStore()
    store.write([
        {"id": "t1", "content": "write tests", "status": "done"},
        {"id": "t2", "content": "implement", "status": "in_progress"},
        {"id": "t3", "content": "review", "status": "pending"},
    ])
    block = store.format_for_injection()
    assert "## Current Tasks" in block
    assert "[x]" in block
    assert "[→]" in block
    assert "[ ]" in block


def test_format_for_injection_returns_empty_string_when_no_items():
    store = TodoStore()
    assert store.format_for_injection() == ""


def test_content_truncated_at_500_chars():
    store = TodoStore()
    store.write([{"id": "1", "content": "x" * 600, "status": "pending"}])
    item = store.read()[0]
    assert len(item.content) <= 501  # 500 chars + "…"
    assert item.content.endswith("…")


def test_max_50_items_enforced():
    store = TodoStore()
    todos = [{"id": str(i), "content": f"task {i}", "status": "pending"} for i in range(60)]
    store.write(todos)
    assert len(store.read()) == 50
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd api && python3.12 -m pytest tests/domain/services/runtime/test_todo_store.py -v --noconftest
```
Expected: `ModuleNotFoundError` or `ImportError` — files don't exist yet.

- [ ] **Step 3: Create `TodoItem` domain model**

```python
# api/app/domain/models/todo.py
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel


class TodoItem(BaseModel):
    """Agent 任务列表条目。"""
    id: str
    content: str
    status: Literal["pending", "in_progress", "done"]
```

- [ ] **Step 4: Create `TodoStore`**

```python
# api/app/domain/services/runtime/todo_store.py
from __future__ import annotations
from app.domain.models.todo import TodoItem

_MAX_ITEMS = 50
_MAX_CONTENT_CHARS = 500
_STATUS_ICONS = {"pending": "[ ]", "in_progress": "[→]", "done": "[x]"}


class TodoStore:
    """In-session 任务列表状态。每轮注入 system prompt，session 结束清空。"""

    def __init__(self) -> None:
        self._items: list[TodoItem] = []

    def write(self, todos: list[dict]) -> list[TodoItem]:
        """替换整个任务列表（截断过长内容和超限条目数）。"""
        validated: list[TodoItem] = []
        for t in todos[:_MAX_ITEMS]:
            content = str(t.get("content", ""))
            if len(content) > _MAX_CONTENT_CHARS:
                content = content[:_MAX_CONTENT_CHARS] + "…"
            validated.append(TodoItem(
                id=str(t.get("id", "")),
                content=content,
                status=t.get("status", "pending"),
            ))
        self._items = validated
        return list(self._items)

    def read(self) -> list[TodoItem]:
        """返回当前任务列表副本。"""
        return list(self._items)

    def format_for_injection(self) -> str:
        """格式化为可注入 system prompt 的 markdown 块。"""
        if not self._items:
            return ""
        lines = ["## Current Tasks"]
        for item in self._items:
            icon = _STATUS_ICONS.get(item.status, "[ ]")
            lines.append(f"- {icon} {item.id}: {item.content}")
        return "\n".join(lines)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd api && python3.12 -m pytest tests/domain/services/runtime/test_todo_store.py -v --noconftest
```
Expected: `6 passed`.

- [ ] **Step 6: Commit**

```bash
git add api/app/domain/models/todo.py \
        api/app/domain/services/runtime/todo_store.py \
        api/tests/domain/services/runtime/test_todo_store.py
git commit -m "feat: add TodoItem domain model and TodoStore in-session state"
```

---

### Task 2: TodoTool + TurnOrchestrator todo injection

**Files:**
- Create: `api/app/domain/services/tools/todo.py`
- Modify: `api/app/domain/services/runtime/turn.py` (add `todo_store` param to `TurnOrchestrator`)
- Test: `api/tests/domain/services/tools/test_todo.py`

**Interfaces:**
- Consumes: `TodoStore` from Task 1 — `TodoStore.write()`, `TodoStore.read()`, `TodoStore.format_for_injection()`
- Consumes: existing `TurnOrchestrator.__init__(workspace, memory, context_engine, max_iterations, guidance)` in `turn.py:75`
- Produces: `TodoTool(todo_store)` with tools `todo_write` and `todo_read`; updated `TurnOrchestrator.__init__` adds `todo_store: Optional[TodoStore] = None` — consumed by Task 6.

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/domain/services/tools/test_todo.py
import json
import pytest
from app.domain.services.runtime.todo_store import TodoStore
from app.domain.services.tools.todo import TodoTool


@pytest.fixture
def store():
    return TodoStore()


@pytest.fixture
def tool(store):
    return TodoTool(todo_store=store)


@pytest.mark.asyncio
async def test_todo_write_updates_store(tool, store):
    result = await tool.invoke("todo_write", todos=[
        {"id": "1", "content": "do something", "status": "pending"}
    ])
    assert result.success
    assert len(store.read()) == 1
    assert store.read()[0].id == "1"


@pytest.mark.asyncio
async def test_todo_write_returns_full_list_as_json(tool):
    result = await tool.invoke("todo_write", todos=[
        {"id": "a", "content": "task a", "status": "in_progress"},
        {"id": "b", "content": "task b", "status": "done"},
    ])
    assert result.success
    data = json.loads(result.data)
    assert len(data) == 2
    assert data[0]["id"] == "a"


@pytest.mark.asyncio
async def test_todo_read_returns_current_list(tool, store):
    store.write([{"id": "x", "content": "existing", "status": "pending"}])
    result = await tool.invoke("todo_read")
    assert result.success
    data = json.loads(result.data)
    assert data[0]["id"] == "x"


@pytest.mark.asyncio
async def test_todo_read_empty_store_returns_empty_list(tool):
    result = await tool.invoke("todo_read")
    assert result.success
    assert json.loads(result.data) == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd api && python3.12 -m pytest tests/domain/services/tools/test_todo.py -v --noconftest
```
Expected: `ImportError` — `todo.py` doesn't exist yet.

- [ ] **Step 3: Create `TodoTool`**

```python
# api/app/domain/services/tools/todo.py
from __future__ import annotations
import json
from typing import TYPE_CHECKING

from app.domain.models.tool_result import ToolResult
from .base import BaseTool, tool

if TYPE_CHECKING:
    from app.domain.services.runtime.todo_store import TodoStore


class TodoTool(BaseTool):
    """Agent 任务列表工具 —— 写入/读取 in-session todo 状态。"""
    name: str = "todo"

    def __init__(self, todo_store: "TodoStore") -> None:
        super().__init__()
        self._store = todo_store

    @tool(
        name="todo_write",
        description=(
            "Replace the full task list. Use at the start of a task to create your plan, "
            "and update status (pending → in_progress → done) as you work. "
            "Always call todo_write before starting a new sub-task."
        ),
        parameters={
            "todos": {
                "type": "array",
                "description": "Full replacement list of tasks",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Unique short identifier, e.g. '1' or 'write-tests'"},
                        "content": {"type": "string", "description": "Task description (max 500 chars)"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "done"],
                            "description": "Current status",
                        },
                    },
                    "required": ["id", "content", "status"],
                },
            }
        },
        required=["todos"],
    )
    async def todo_write(self, todos: list) -> ToolResult:
        items = self._store.write(todos)
        return ToolResult(success=True, data=json.dumps([i.model_dump() for i in items]))

    @tool(
        name="todo_read",
        description="Read the current task list to check progress.",
        parameters={},
        required=[],
    )
    async def todo_read(self) -> ToolResult:
        items = self._store.read()
        return ToolResult(success=True, data=json.dumps([i.model_dump() for i in items]))
```

- [ ] **Step 4: Add `todo_store` to `TurnOrchestrator`**

In `api/app/domain/services/runtime/turn.py`, make two edits:

**Edit 1** — add import at the top of the `TYPE_CHECKING` block (line 7–10):
```python
if TYPE_CHECKING:
    from app.domain.services.runtime.workspace import WorkspaceContext
    from app.domain.services.runtime.memory import ThreeLayerMemory
    from app.domain.services.runtime.context_engine import ContextEngine
    from app.domain.services.runtime.todo_store import TodoStore
```

**Edit 2** — update `TurnOrchestrator.__init__` (line 75–87) to add the `todo_store` parameter:
```python
    def __init__(
        self,
        workspace: "WorkspaceContext",
        memory: "ThreeLayerMemory",
        context_engine: "ContextEngine",
        max_iterations: int = 40,
        guidance: str = "",
        todo_store: "TodoStore | None" = None,
    ) -> None:
        self._workspace = workspace
        self._memory = memory
        self._context_engine = context_engine
        self._max_iterations = max_iterations
        self._guidance = guidance
        self._todo_store = todo_store
```

**Edit 3** — update `_build_system_prompt` (line 89–103) to append the todo block:
```python
    def _build_system_prompt(self) -> str:
        """构建 system prompt stable tier（workspace + memory + todos）。"""
        blocks = []
        if self._guidance:
            blocks.append(self._guidance)
        else:
            blocks.extend([
                "You are a production-grade coding agent pairing with the user inside their codebase.",
                "Operate like a careful senior engineer: read before writing, verify after changing.",
            ])
        blocks.append(self._workspace.system_block())
        memory_block = self._memory.build_context_block()
        if memory_block:
            blocks.append(memory_block)
        if self._todo_store:
            todo_block = self._todo_store.format_for_injection()
            if todo_block:
                blocks.append(todo_block)
        return "\n\n".join(filter(None, blocks))
```

- [ ] **Step 5: Run all tests to verify they pass**

```bash
cd api && python3.12 -m pytest tests/domain/services/tools/test_todo.py tests/domain/services/runtime/test_turn.py -v --noconftest
```
Expected: all pass (4 new + existing TurnOrchestrator tests).

- [ ] **Step 6: Commit**

```bash
git add api/app/domain/services/tools/todo.py \
        api/app/domain/services/runtime/turn.py \
        api/tests/domain/services/tools/test_todo.py
git commit -m "feat: add TodoTool and wire TodoStore into TurnOrchestrator system prompt"
```

---

### Task 3: TestRunnerTool

**Files:**
- Create: `api/app/domain/services/tools/test_runner.py`
- Test: `api/tests/domain/services/tools/test_test_runner.py`

**Interfaces:**
- Produces: `TestRunnerTool(cwd)` with tool `run_tests(path, pattern, timeout, verbose) -> ToolResult(data={"passed": N, "failed": N, "errors": N, "skipped": N, "failure_details": "..."})` — consumed by Task 6 wiring.

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/domain/services/tools/test_test_runner.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.domain.services.tools.test_runner import TestRunnerTool, _parse_pytest_output


# --- Unit tests for the parser (no subprocess) ---

def test_parse_extracts_passed_count():
    output = "5 passed, 1 failed in 2.3s"
    result = _parse_pytest_output(output)
    assert result["passed"] == 5
    assert result["failed"] == 1


def test_parse_extracts_failure_details():
    output = (
        "===== FAILURES =====\n"
        "FAILED test_foo.py::test_bar - AssertionError\n"
        "===================="
    )
    result = _parse_pytest_output(output)
    assert "test_foo" in result["failure_details"]


def test_parse_returns_zeros_on_empty_output():
    result = _parse_pytest_output("")
    assert result["passed"] == 0
    assert result["failed"] == 0
    assert result["failure_details"] == ""


# --- Integration-style tests with mocked subprocess ---

@pytest.mark.asyncio
async def test_run_tests_returns_structured_result_on_success():
    tool = TestRunnerTool(cwd="/tmp")
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"3 passed in 0.4s", b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("asyncio.wait_for", new=AsyncMock(return_value=(b"3 passed in 0.4s", b""))):
        result = await tool.invoke("run_tests", path=".")

    assert result.success
    assert isinstance(result.data, dict)
    assert result.data["passed"] == 3


@pytest.mark.asyncio
async def test_run_tests_returns_failure_on_nonzero_exit():
    tool = TestRunnerTool(cwd="/tmp")
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"1 failed in 0.2s", b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("asyncio.wait_for", new=AsyncMock(return_value=(b"1 failed in 0.2s", b""))):
        result = await tool.invoke("run_tests")

    assert not result.success
    assert result.data["failed"] == 1


@pytest.mark.asyncio
async def test_run_tests_kills_process_on_timeout():
    tool = TestRunnerTool(cwd="/tmp")
    mock_proc = MagicMock()
    mock_proc.kill = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
        result = await tool.invoke("run_tests", timeout=1)

    assert not result.success
    assert "timed out" in result.message
    mock_proc.kill.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd api && python3.12 -m pytest tests/domain/services/tools/test_test_runner.py -v --noconftest
```
Expected: `ImportError` — file doesn't exist yet.

- [ ] **Step 3: Implement `TestRunnerTool`**

```python
# api/app/domain/services/tools/test_runner.py
from __future__ import annotations
import asyncio
import re
from typing import Optional

from app.domain.models.tool_result import ToolResult
from .base import BaseTool, tool


class TestRunnerTool(BaseTool):
    """测试运行工具 —— 直接调用 pytest，返回结构化结果。"""
    name: str = "test_runner"

    def __init__(self, cwd: str = ".") -> None:
        super().__init__()
        self._cwd = cwd

    @tool(
        name="run_tests",
        description=(
            "Run pytest and return structured results: passed, failed, errors, skipped, failure_details. "
            "Use after making code changes to verify correctness. "
            "Check failure_details to understand what broke and fix it."
        ),
        parameters={
            "path": {
                "type": "string",
                "description": "Directory or test file to run (default: '.' runs all tests)",
            },
            "pattern": {
                "type": "string",
                "description": "Filter tests by name substring (passed to pytest -k)",
            },
            "timeout": {
                "type": "integer",
                "description": "Max seconds to wait before killing pytest (default: 60)",
            },
            "verbose": {
                "type": "boolean",
                "description": "Include full pytest output in result (default: false)",
            },
        },
        required=[],
    )
    async def run_tests(
        self,
        path: str = ".",
        pattern: Optional[str] = None,
        timeout: int = 60,
        verbose: bool = False,
    ) -> ToolResult:
        cmd = ["python", "-m", "pytest", path, "-v", "--tb=short", "--no-header"]
        if pattern:
            cmd.extend(["-k", pattern])

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self._cwd,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = stdout.decode(errors="replace")
            parsed = _parse_pytest_output(output)
            if verbose:
                parsed["full_output"] = output[:5000]
            return ToolResult(
                success=proc.returncode == 0,
                data=parsed,
                message=f"exit code {proc.returncode}",
            )
        except asyncio.TimeoutError:
            if proc is not None:
                try:
                    proc.kill()
                    await proc.communicate()
                except Exception:
                    pass
            return ToolResult(success=False, message=f"Tests timed out after {timeout}s")
        except Exception as e:
            return ToolResult(success=False, message=str(e))


def _parse_pytest_output(output: str) -> dict:
    """从 pytest 输出中提取结构化数据。"""
    result = {
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "failure_details": "",
    }
    for pattern, key in [
        (r"(\d+) passed", "passed"),
        (r"(\d+) failed", "failed"),
        (r"(\d+) error", "errors"),
        (r"(\d+) skipped", "skipped"),
    ]:
        m = re.search(pattern, output)
        if m:
            result[key] = int(m.group(1))
    fail_match = re.search(
        r"={5,} FAILURES ={5,}\n(.*?)(?=\n={5,}|\Z)", output, re.DOTALL
    )
    if fail_match:
        result["failure_details"] = fail_match.group(1)[:3000]
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd api && python3.12 -m pytest tests/domain/services/tools/test_test_runner.py -v --noconftest
```
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add api/app/domain/services/tools/test_runner.py \
        api/tests/domain/services/tools/test_test_runner.py
git commit -m "feat: add TestRunnerTool with structured pytest output parsing"
```

---

### Task 4: patch_file batch replacement

**Files:**
- Modify: `api/app/domain/services/tools/file_edit.py`
- Test: `api/tests/domain/services/tools/test_file_edit_batch.py`

**Interfaces:**
- Consumes: existing `FileEditTool.patch_file(filepath, old_str, new_str)` at `file_edit.py` — must remain callable with those exact kwargs.
- Produces: extended `patch_file` that also accepts `replacements: list[{old_str, new_str}]`; returns `ToolResult(data={"replacements_applied": N})` on success.

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/domain/services/tools/test_file_edit_batch.py
import pytest
from pathlib import Path
from app.domain.services.tools.file_edit import FileEditTool


@pytest.fixture
def tmp_file(tmp_path):
    f = tmp_path / "sample.py"
    f.write_text("foo = 1\nbar = 2\nbaz = 3\n")
    return str(f)


@pytest.mark.asyncio
async def test_batch_applies_all_replacements(tmp_file):
    tool = FileEditTool()
    result = await tool.invoke("patch_file", filepath=tmp_file, replacements=[
        {"old_str": "foo = 1", "new_str": "foo = 10"},
        {"old_str": "bar = 2", "new_str": "bar = 20"},
    ])
    assert result.success
    assert result.data["replacements_applied"] == 2
    content = Path(tmp_file).read_text()
    assert "foo = 10" in content
    assert "bar = 20" in content


@pytest.mark.asyncio
async def test_batch_fails_fast_and_leaves_file_unchanged(tmp_file):
    tool = FileEditTool()
    result = await tool.invoke("patch_file", filepath=tmp_file, replacements=[
        {"old_str": "NOT_THERE", "new_str": "x"},
        {"old_str": "foo = 1", "new_str": "foo = 99"},
    ])
    assert not result.success
    assert "not found" in result.message
    # File must be unchanged because fail-fast happens before writing
    assert "foo = 1" in Path(tmp_file).read_text()


@pytest.mark.asyncio
async def test_backward_compat_old_str_new_str(tmp_file):
    tool = FileEditTool()
    result = await tool.invoke("patch_file", filepath=tmp_file,
                               old_str="baz = 3", new_str="baz = 30")
    assert result.success
    assert "baz = 30" in Path(tmp_file).read_text()


@pytest.mark.asyncio
async def test_batch_error_message_includes_index(tmp_file):
    tool = FileEditTool()
    result = await tool.invoke("patch_file", filepath=tmp_file, replacements=[
        {"old_str": "foo = 1", "new_str": "foo = 10"},
        {"old_str": "MISSING", "new_str": "y"},
    ])
    assert not result.success
    assert "2" in result.message  # "Replacement 2: ..."
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd api && python3.12 -m pytest tests/domain/services/tools/test_file_edit_batch.py -v --noconftest
```
Expected: test for batch fails — `patch_file` doesn't accept `replacements` yet.

- [ ] **Step 3: Update `patch_file` in `file_edit.py`**

Replace the existing `patch_file` method and its `@tool` decorator entirely:

```python
    @tool(
        name="patch_file",
        description=(
            "Replace text in a file. Provide either:\n"
            "  - old_str + new_str for a single replacement, OR\n"
            "  - replacements: [{old_str, new_str}, ...] for multiple replacements in one call.\n"
            "Each old_str must appear exactly once. All replacements are validated before writing "
            "(fail-fast — file is unchanged if any replacement fails)."
        ),
        parameters={
            "filepath": {"type": "string", "description": "Path to the file to patch"},
            "old_str": {"type": "string", "description": "Text to replace (single replacement)"},
            "new_str": {"type": "string", "description": "Replacement text (single replacement)"},
            "replacements": {
                "type": "array",
                "description": "Multiple replacements applied in order",
                "items": {
                    "type": "object",
                    "properties": {
                        "old_str": {"type": "string"},
                        "new_str": {"type": "string"},
                    },
                    "required": ["old_str", "new_str"],
                },
            },
        },
        required=["filepath"],
    )
    async def patch_file(
        self,
        filepath: str,
        old_str: Optional[str] = None,
        new_str: Optional[str] = None,
        replacements: Optional[list] = None,
    ) -> ToolResult:
        try:
            # Normalise to replacements list
            if replacements is None:
                if old_str is None:
                    return ToolResult(
                        success=False,
                        message="Provide either old_str/new_str or replacements",
                    )
                replacements = [{"old_str": old_str, "new_str": new_str or ""}]

            content = Path(filepath).read_text(encoding="utf-8")

            # Validate ALL replacements before writing (fail-fast)
            for i, rep in enumerate(replacements, 1):
                o = rep.get("old_str", "")
                count = content.count(o)
                if count == 0:
                    return ToolResult(
                        success=False,
                        message=f"Replacement {i}: old_str not found in {filepath}",
                    )
                if count > 1:
                    return ToolResult(
                        success=False,
                        message=f"Replacement {i}: old_str appears {count} times — must be unique",
                    )

            # Apply all replacements
            for rep in replacements:
                content = content.replace(rep["old_str"], rep.get("new_str", ""), 1)

            Path(filepath).write_text(content, encoding="utf-8")
            return ToolResult(success=True, data={"replacements_applied": len(replacements)})
        except Exception as e:
            return ToolResult(success=False, message=str(e))
```

- [ ] **Step 4: Run tests — both batch tests and existing patch_file tests**

```bash
cd api && python3.12 -m pytest tests/domain/services/tools/test_file_edit_batch.py -v --noconftest
```
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add api/app/domain/services/tools/file_edit.py \
        api/tests/domain/services/tools/test_file_edit_batch.py
git commit -m "feat: extend patch_file to support multiple replacements in one call"
```

---

### Task 5: ToolExecutor.execute_batch()

**Files:**
- Modify: `api/app/domain/services/runtime/tool_executor.py`
- Test: `api/tests/domain/services/runtime/test_tool_executor_batch.py`

**Interfaces:**
- Consumes: existing `ToolExecutor.execute(tool_name, arguments) -> (ToolResult, ToolResultKind)` — `execute_batch` delegates to it.
- Produces: `ToolExecutor.execute_batch(calls: list[dict]) -> list[tuple[ToolResult, ToolResultKind]]` where `calls` items are `{"tool_name": str, "arguments": dict}` — consumed by Task 6.

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/domain/services/runtime/test_tool_executor_batch.py
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock
from app.domain.services.runtime.tool_executor import ToolExecutor, ToolResultKind
from app.domain.models.tool_result import ToolResult


@pytest.fixture
def executor():
    mock_tool = MagicMock()
    mock_tool.has_tool = MagicMock(return_value=True)
    mock_tool.invoke = AsyncMock(return_value=ToolResult(success=True, data="ok"))
    mock_engine = MagicMock()
    return ToolExecutor(tools=[mock_tool], context_engine=mock_engine)


@pytest.mark.asyncio
async def test_execute_batch_empty_returns_empty(executor):
    results = await executor.execute_batch([])
    assert results == []


@pytest.mark.asyncio
async def test_execute_batch_single_call_returns_list_of_one(executor):
    results = await executor.execute_batch([
        {"tool_name": "grep_files", "arguments": {"pattern": "foo", "path": "."}}
    ])
    assert len(results) == 1
    result, kind = results[0]
    assert isinstance(result, ToolResult)
    assert kind == ToolResultKind.SEARCH


@pytest.mark.asyncio
async def test_execute_batch_preserves_result_order(executor):
    calls = [
        {"tool_name": "grep_files", "arguments": {}},
        {"tool_name": "list_dir", "arguments": {}},
        {"tool_name": "grep_files", "arguments": {}},
    ]
    results = await executor.execute_batch(calls)
    assert len(results) == 3
    # All three are SEARCH kind
    for _, kind in results:
        assert kind == ToolResultKind.SEARCH


@pytest.mark.asyncio
async def test_execute_batch_mixed_calls_completes(executor):
    calls = [
        {"tool_name": "grep_files", "arguments": {}},    # SEARCH — concurrent
        {"tool_name": "shell_execute", "arguments": {}}, # TERMINAL — serial
    ]
    results = await executor.execute_batch(calls)
    assert len(results) == 2
    _, kind0 = results[0]
    _, kind1 = results[1]
    assert kind0 == ToolResultKind.SEARCH
    assert kind1 == ToolResultKind.TERMINAL
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd api && python3.12 -m pytest tests/domain/services/runtime/test_tool_executor_batch.py -v --noconftest
```
Expected: `AttributeError` — `execute_batch` doesn't exist yet.

- [ ] **Step 3: Add `execute_batch` and `asyncio` import to `tool_executor.py`**

Add `import asyncio` at the top of the imports block.

Also add `"run_tests"` to `_TERMINAL_TOOLS` so the new tool is classified correctly:
```python
_TERMINAL_TOOLS = frozenset({"shell_execute", "shell_read_output", "shell_wait_process", "run_tests"})
```

Then add `execute_batch` as a new method to `ToolExecutor` after the existing `execute` method:

```python
    async def execute_batch(
        self, calls: list[dict]
    ) -> list[tuple[ToolResult, ToolResultKind]]:
        """Execute multiple tool calls with safe concurrency.

        SEARCH tools (grep_files, list_dir, etc.) run concurrently via asyncio.gather.
        FILE_MUTATION and TERMINAL tools run serially in original order.
        Results are returned in original call order regardless of execution order.
        """
        if not calls:
            return []
        if len(calls) == 1:
            return [await self.execute(calls[0]["tool_name"], calls[0]["arguments"])]

        # Partition calls by kind (pre-flight classify, no execution yet)
        concurrent_indices: list[int] = []
        serial_indices: list[int] = []
        for i, call in enumerate(calls):
            pre_kind = classify_tool_result(call["tool_name"], ToolResult(success=True))
            if pre_kind == ToolResultKind.SEARCH:
                concurrent_indices.append(i)
            else:
                serial_indices.append(i)

        results: list[tuple[ToolResult, ToolResultKind] | None] = [None] * len(calls)

        # Concurrent phase: SEARCH tools run in parallel
        if concurrent_indices:
            concurrent_results = await asyncio.gather(*[
                self.execute(calls[i]["tool_name"], calls[i]["arguments"])
                for i in concurrent_indices
            ])
            for i, res in zip(concurrent_indices, concurrent_results):
                results[i] = res

        # Serial phase: FILE_MUTATION, TERMINAL, OTHER run in original order
        for i in serial_indices:
            results[i] = await self.execute(calls[i]["tool_name"], calls[i]["arguments"])

        return results  # type: ignore[return-value]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd api && python3.12 -m pytest tests/domain/services/runtime/test_tool_executor_batch.py -v --noconftest
```
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add api/app/domain/services/runtime/tool_executor.py \
        api/tests/domain/services/runtime/test_tool_executor_batch.py
git commit -m "feat: add ToolExecutor.execute_batch() with concurrent SEARCH and serial write execution"
```

---

### Task 6: BaseAgent batch dispatch + full wiring

**Files:**
- Modify: `api/app/domain/services/agents/base.py`
- Modify: `api/app/domain/services/agents/coding_agent.py`
- Modify: `api/app/infrastructure/external/llm/openai_llm.py`
- Test: run existing `tests/domain/services/agents/test_base_agent.py` — must still pass

**Interfaces:**
- Consumes: `TodoStore` (Task 1), `TodoTool` (Task 2), `TestRunnerTool` (Task 3), `ToolExecutor.execute_batch` (Task 5), `TurnOrchestrator(todo_store=...)` (Task 2).
- Produces: a fully wired `build_coding_agent()` that gives the agent `run_tests`, `todo_write`, `todo_read`, batch file patching, and parallel tool dispatch.

- [ ] **Step 1: Remove `parallel_tool_calls=False` from `openai_llm.py`**

In `api/app/infrastructure/external/llm/openai_llm.py` at line 63, remove the `parallel_tool_calls=False` keyword argument entirely. The DeepSeek API silently ignores unknown kwargs but explicitly passing `False` prevents the model from ever returning multiple tool calls on OpenAI-compatible endpoints.

The `chat.completions.create` call with tools becomes:

```python
response = await self._client.chat.completions.create(
    model=self._model_name,
    temperature=self._temperature,
    max_tokens=self._max_tokens,
    messages=messages,
    response_format=response_format,
    tools=tools,
    tool_choice=tool_choice,
    timeout=self._timeout,
)
```

- [ ] **Step 2: Update `_react_loop` in `base.py` to process all tool calls via `execute_batch`**

Replace the entire `for tool_call in tool_calls[:1]:` block with the version below. The key changes are: (a) collect all tool calls, (b) emit CALLING events for all before executing, (c) pre-flight checkpoint for all FILE_MUTATION tools, (d) execute via `execute_batch`, (e) emit CALLED events and append all tool messages.

```python
            # 处理工具调用（支持批量并发）
            parsed_calls: list[tuple[str, str, str, dict]] = []  # (tc_id, tool_name, raw_args, arguments)
            for tool_call in tool_calls:
                tc_id = tool_call.get("id") or str(uuid.uuid4())
                func = tool_call.get("function", {})
                tool_name = func.get("name", "")
                raw_args = func.get("arguments", "{}")
                arguments = await self._json_parser.invoke(raw_args)
                parsed_calls.append((tc_id, tool_name, raw_args, arguments))

                yield ToolEvent(
                    tool_call_id=tc_id,
                    tool_name=tool_name,
                    function_name=tool_name,
                    function_args=arguments,
                    status=ToolEventStatus.CALLING,
                )

                # FILE_MUTATION 前做 checkpoint（按工具名分类，而非 argument key）
                pre_kind = classify_tool_result(tool_name, ToolResult(success=True))
                if pre_kind == ToolResultKind.FILE_MUTATION:
                    filepath = (
                        arguments.get("filepath") or arguments.get("path") or
                        arguments.get("filename") or arguments.get("target") or ""
                    )
                    if filepath:
                        await self._checkpoint_service.snapshot(filepath, turn_id=turn_ctx.turn_id)

            # 批量执行（SEARCH 并发，FILE_MUTATION/TERMINAL 串行）
            batch_calls = [
                {"tool_name": tn, "arguments": args}
                for _, tn, _, args in parsed_calls
            ]
            batch_results = await self._tool_executor.execute_batch(batch_calls)

            tool_messages = []
            for (tc_id, tool_name, _, arguments), (result, kind) in zip(parsed_calls, batch_results):
                yield ToolEvent(
                    tool_call_id=tc_id,
                    tool_name=tool_name,
                    function_name=tool_name,
                    function_args=arguments,
                    function_result=result,
                    status=ToolEventStatus.CALLED,
                )
                tool_messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "function_name": tool_name,
                    "content": result.model_dump_json(),
                })

            messages.extend(tool_messages)
```

- [ ] **Step 3: Verify existing base agent tests still pass**

```bash
cd api && python3.12 -m pytest tests/domain/services/agents/test_base_agent.py -v --noconftest
```
Expected: `4 passed` — no regression.

- [ ] **Step 4: Wire new tools in `coding_agent.py`**

Replace `coding_agent.py` entirely:

```python
# api/app/domain/services/agents/coding_agent.py
from __future__ import annotations

from typing import Callable

from app.domain.external.llm import LLM
from app.domain.external.json_parser import JSONParser
from app.domain.models.app_config import AgentConfig
from app.domain.repositories.uow import IUnitOfWork
from app.domain.services.agents.base import BaseAgent
from app.domain.services.runtime.context_engine import DefaultContextEngine, ContextEngineConfig
from app.domain.services.runtime.memory import ThreeLayerMemory
from app.domain.services.runtime.todo_store import TodoStore
from app.domain.services.runtime.turn import TurnOrchestrator
from app.domain.services.runtime.tool_executor import ToolExecutor
from app.domain.services.runtime.checkpoint import CheckpointService
from app.domain.services.runtime.workspace import WorkspaceContext
from app.domain.services.tools.shell import ShellTool
from app.domain.services.tools.file_edit import FileEditTool
from app.domain.services.tools.code_search import CodeSearchTool
from app.domain.services.tools.git import GitTool
from app.domain.services.tools.test_runner import TestRunnerTool
from app.domain.services.tools.todo import TodoTool
from app.domain.services.prompts.coding import CODING_AGENT_SYSTEM_PROMPT


def build_coding_agent(
    llm: LLM,
    json_parser: JSONParser,
    agent_config: AgentConfig,
    uow_factory: Callable[[], IUnitOfWork],
    session_id: str,
    workspace: WorkspaceContext,
) -> BaseAgent:
    """工厂函数：组装完整的 CodingAgent（所有 runtime 组件连线）。"""
    cwd = workspace.cwd

    todo_store = TodoStore()

    tools = [
        ShellTool(cwd=cwd),
        FileEditTool(),
        CodeSearchTool(cwd=cwd),
        GitTool(cwd=cwd),
        TestRunnerTool(cwd=cwd),
        TodoTool(todo_store=todo_store),
    ]

    context_length = agent_config.context_length

    context_engine = DefaultContextEngine(
        config=ContextEngineConfig(
            context_length=context_length,
            threshold_percent=0.75,
        ),
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
    tool_executor = ToolExecutor(tools=tools, context_engine=context_engine)
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

- [ ] **Step 5: Run the full test suite**

```bash
cd api && python3.12 -m pytest tests/ -v --noconftest -q 2>&1 | tail -10
```
Expected: `35 passed` (existing) + new tests — no regressions.

- [ ] **Step 6: Commit**

```bash
git add api/app/domain/services/agents/base.py \
        api/app/domain/services/agents/coding_agent.py \
        api/app/infrastructure/external/llm/openai_llm.py
git commit -m "feat: batch tool dispatch in BaseAgent, wire TestRunnerTool and TodoTool into CodingAgent"
```
