# Argo Intelligence Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add streaming LLM output, project startup context injection, and two enhanced code-search tools to Argo.

**Architecture:** Three independent tasks in dependency order: Task 1 (code search — isolated tool addition), Task 2 (project context — new async loader wired into the agent factory), Task 3 (streaming — threads a callback from `main.py` through `BaseAgent` to `OpenAILLM`). Tasks 1 and 2 share no state; Task 3 adds params to `build_coding_agent` that Tasks 1 and 2 don't use.

**Tech Stack:** Python 3.12, asyncio, OpenAI SDK (>=1.107.2, already installed), pytest-asyncio 0.24.0, stdlib only.

## Global Constraints

- No new pip dependencies — OpenAI SDK and stdlib only.
- All existing public interfaces remain backward-compatible (`text_callback=None`, `project_context=""`, `stream_callback=None`, `stream_reset=None` defaults).
- Domain layer tests (`api/tests/`) run from `api/` directory: `cd api && pytest tests/... -v`.
- Argo package tests (`argo/tests/`) run from repo root: `pytest argo/tests/... -v`.
- Tests use `@pytest.mark.asyncio` and `pytest-asyncio` (v0.24.0 already installed).
- Use `@pytest_asyncio.fixture` for async fixtures.
- `api/config.yaml` is gitignored — never commit it.

---

## File Map

| File | Task | Action |
|------|------|--------|
| `api/app/domain/services/tools/code_search.py` | 1 | Modify — add `find_symbol`, `read_file_range` |
| `api/tests/domain/services/tools/test_code_search_enhanced.py` | 1 | Create |
| `api/app/domain/services/runtime/project_context.py` | 2 | Create |
| `api/app/domain/services/agents/coding_agent.py` | 2, 3 | Modify — add `project_context`, then `stream_callback`/`stream_reset` |
| `argo/main.py` | 2, 3 | Modify — call `load_project_context`, add streaming closures |
| `api/tests/domain/services/runtime/test_project_context.py` | 2 | Create |
| `api/app/domain/external/llm.py` | 3 | Modify — add `text_callback` to Protocol |
| `api/app/infrastructure/external/llm/openai_llm.py` | 3 | Modify — add streaming path |
| `api/app/domain/models/event.py` | 3 | Modify — add `streamed` field to `MessageEvent` |
| `api/app/domain/services/agents/base.py` | 3 | Modify — add `stream_callback`/`stream_reset`, thread into `_react_loop` |
| `argo/renderer.py` | 3 | Modify — skip streamed `MessageEvent` |
| `argo/tests/test_renderer.py` | 3 | Modify — add `streamed` rendering tests |
| `api/tests/domain/services/runtime/test_llm_streaming.py` | 3 | Create |

---

## Task 1: Code Search Enhancement

**Files:**
- Modify: `api/app/domain/services/tools/code_search.py`
- Create: `api/tests/domain/services/tools/test_code_search_enhanced.py`

**Interfaces:**
- Produces: `CodeSearchTool.find_symbol(name: str, path: str = ".") -> ToolResult` and `CodeSearchTool.read_file_range(filepath: str, start_line: int, end_line: int) -> ToolResult`

---

- [ ] **Step 1: Write the failing tests**

Create `api/tests/domain/services/tools/test_code_search_enhanced.py`:

```python
from __future__ import annotations
import pytest
import pytest_asyncio
from app.domain.services.tools.code_search import CodeSearchTool


@pytest_asyncio.fixture
async def search_tool(tmp_path):
    (tmp_path / "sample.py").write_text(
        "class MyClass:\n"
        "    pass\n"
        "\n"
        "def my_function():\n"
        "    return 42\n"
    )
    return CodeSearchTool(cwd=str(tmp_path))


@pytest.mark.asyncio
async def test_find_symbol_class(search_tool):
    result = await search_tool.find_symbol("MyClass")
    assert result.success
    assert "MyClass" in result.data
    assert "sample.py" in result.data


@pytest.mark.asyncio
async def test_find_symbol_function(search_tool):
    result = await search_tool.find_symbol("my_function")
    assert result.success
    assert "my_function" in result.data


@pytest.mark.asyncio
async def test_find_symbol_not_found(search_tool):
    result = await search_tool.find_symbol("NonExistent")
    assert not result.success
    assert "not found" in result.message


@pytest.mark.asyncio
async def test_read_file_range_returns_correct_lines(search_tool):
    result = await search_tool.read_file_range("sample.py", 1, 2)
    assert result.success
    assert "class MyClass:" in result.data
    # Line numbers must appear
    assert "1" in result.data
    assert "2" in result.data


@pytest.mark.asyncio
async def test_read_file_range_start_beyond_file(search_tool):
    result = await search_tool.read_file_range("sample.py", 999, 1000)
    assert not result.success
    assert "999" in result.message


@pytest.mark.asyncio
async def test_read_file_range_file_not_found(search_tool):
    result = await search_tool.read_file_range("nonexistent.py", 1, 5)
    assert not result.success
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd api && pytest tests/domain/services/tools/test_code_search_enhanced.py -v
```

Expected: all tests FAIL with `AttributeError: 'CodeSearchTool' object has no attribute 'find_symbol'`

- [ ] **Step 3: Add `import re` and two new tool methods to `CodeSearchTool`**

In `api/app/domain/services/tools/code_search.py`, add `import re` at the top (after `from __future__ import annotations`), then append the two methods inside the `CodeSearchTool` class, after `list_dir`:

```python
import re  # add at top of file after existing imports

# Inside CodeSearchTool class, after list_dir method:

    @tool(
        name="find_symbol",
        description="按名称查找函数或类定义的位置。返回文件路径和行号。用于快速定位代码定义，避免搜索整个目录。",
        parameters={
            "name": {"type": "string", "description": "函数名或类名"},
            "path": {"type": "string", "description": "搜索目录（默认 '.'）"},
        },
        required=["name"],
    )
    async def find_symbol(self, name: str, path: str = ".") -> ToolResult:
        pattern = rf"(def|class)\s+{re.escape(name)}\b"
        cmd = ["grep", "-rn", "-E", pattern, path]
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            result = stdout.decode(errors="replace").strip()
            if not result:
                return ToolResult(success=False, message=f"Symbol '{name}' not found")
            return ToolResult(success=True, data=result)
        except asyncio.TimeoutError:
            if proc:
                try:
                    proc.kill()
                    await proc.communicate()
                except Exception:
                    pass
            return ToolResult(success=False, message="Search timed out")
        except Exception as e:
            return ToolResult(success=False, message=str(e))

    @tool(
        name="read_file_range",
        description="读取文件指定行范围（含行号）。用于查看大文件的特定函数或片段，避免读取整个文件。",
        parameters={
            "filepath": {"type": "string", "description": "文件路径（相对于项目根目录或绝对路径）"},
            "start_line": {"type": "integer", "description": "起始行（从 1 开始）"},
            "end_line": {"type": "integer", "description": "结束行（含）"},
        },
        required=["filepath", "start_line", "end_line"],
    )
    async def read_file_range(self, filepath: str, start_line: int, end_line: int) -> ToolResult:
        try:
            p = (
                Path(filepath)
                if Path(filepath).is_absolute()
                else Path(self._cwd) / filepath
            )
            lines = p.read_text(errors="replace").splitlines()
            s = max(0, start_line - 1)
            e = min(len(lines), end_line)
            if s >= len(lines):
                return ToolResult(
                    success=False,
                    message=f"start_line {start_line} exceeds file length {len(lines)}",
                )
            selected = lines[s:e]
            numbered = "\n".join(
                f"{s + i + 1:4d}  {line}" for i, line in enumerate(selected)
            )
            return ToolResult(success=True, data=numbered)
        except FileNotFoundError:
            return ToolResult(success=False, message=f"File not found: {filepath}")
        except Exception as e:
            return ToolResult(success=False, message=str(e))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd api && pytest tests/domain/services/tools/test_code_search_enhanced.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add api/app/domain/services/tools/code_search.py \
        api/tests/domain/services/tools/test_code_search_enhanced.py
git commit -m "feat: add find_symbol and read_file_range to CodeSearchTool"
```

---

## Task 2: Project Context Loader

**Files:**
- Create: `api/app/domain/services/runtime/project_context.py`
- Modify: `api/app/domain/services/agents/coding_agent.py` (add `project_context` param)
- Modify: `argo/main.py` (call `load_project_context`, pass to `build_coding_agent`)
- Create: `api/tests/domain/services/runtime/test_project_context.py`

**Interfaces:**
- Consumes: nothing from Task 1
- Produces: `load_project_context(cwd: str) -> str` (async coroutine); `build_coding_agent(..., project_context: str = "")` gains one new optional param

---

- [ ] **Step 1: Write the failing tests**

Create `api/tests/domain/services/runtime/test_project_context.py`:

```python
from __future__ import annotations
import pytest
from pathlib import Path
from app.domain.services.runtime.project_context import load_project_context


@pytest.mark.asyncio
async def test_empty_directory_returns_string(tmp_path):
    result = await load_project_context(str(tmp_path))
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_readme_is_included(tmp_path):
    (tmp_path / "README.md").write_text("# My Project\nThis is a test project.")
    result = await load_project_context(str(tmp_path))
    assert "# Project Context" in result
    assert "My Project" in result


@pytest.mark.asyncio
async def test_readme_truncated_at_3000_chars(tmp_path):
    (tmp_path / "README.md").write_text("x" * 5000)
    result = await load_project_context(str(tmp_path))
    assert result.count("x") == 3000


@pytest.mark.asyncio
async def test_result_ends_with_separator_when_content_found(tmp_path):
    (tmp_path / "README.md").write_text("# Test")
    result = await load_project_context(str(tmp_path))
    assert result.endswith("---\n\n")


@pytest.mark.asyncio
async def test_rst_readme_is_found(tmp_path):
    (tmp_path / "README.rst").write_text("My rst readme")
    result = await load_project_context(str(tmp_path))
    assert "My rst readme" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd api && pytest tests/domain/services/runtime/test_project_context.py -v
```

Expected: all tests FAIL with `ModuleNotFoundError: No module named 'app.domain.services.runtime.project_context'`

- [ ] **Step 3: Create `project_context.py`**

Create `api/app/domain/services/runtime/project_context.py` with this exact content:

```python
from __future__ import annotations
import asyncio
from pathlib import Path


async def load_project_context(cwd: str) -> str:
    """Read README, recent git log, and key source files; return as a context string."""
    sections: list[str] = []

    # README — first 3000 chars, try common names in order
    for name in ("README.md", "README.rst", "README.txt", "README"):
        p = Path(cwd) / name
        if p.exists():
            text = p.read_text(errors="replace")[:3000]
            sections.append(f"## README\n{text}")
            break

    # Recent git log — 15 commits, 5 s timeout, silently skipped if not a git repo
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "log", "--oneline", "-15",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=cwd,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        log = stdout.decode(errors="replace").strip()
        if log:
            sections.append(f"## Recent commits\n{log}")
    except (asyncio.TimeoutError, Exception):
        pass

    # Python file structure — top 3 levels, no __pycache__, first 50 matches
    try:
        proc = await asyncio.create_subprocess_shell(
            "find . -maxdepth 3 -name '*.py' | grep -v __pycache__ | sort | head -50",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=cwd,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        files = stdout.decode(errors="replace").strip()
        if files:
            sections.append(f"## Key source files\n{files}")
    except (asyncio.TimeoutError, Exception):
        pass

    if not sections:
        return ""
    return "# Project Context\n\n" + "\n\n".join(sections) + "\n\n---\n\n"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd api && pytest tests/domain/services/runtime/test_project_context.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Wire `project_context` into `build_coding_agent`**

In `api/app/domain/services/agents/coding_agent.py`, add `project_context: str = ""` as a new parameter and use it to prepend to `CODING_AGENT_SYSTEM_PROMPT`:

```python
def build_coding_agent(
    llm: LLM,
    json_parser: JSONParser,
    agent_config: AgentConfig,
    uow_factory: Callable[[], IUnitOfWork],
    session_id: str,
    workspace: WorkspaceContext,
    shell_session: PersistentShellSession | None = None,
    pre_execute_hook=None,
    project_context: str = "",          # NEW
) -> BaseAgent:
    """工厂函数：组装完整的 CodingAgent（所有 runtime 组件连线）。"""
    cwd = workspace.cwd

    todo_store = TodoStore()
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
        config=ContextEngineConfig(
            context_length=context_length,
            threshold_percent=0.75,
        ),
        llm=llm,
        protect_first_n=3,
        protect_last_n=6,
    )
    memory = ThreeLayerMemory(session_id=session_id, uow_factory=uow_factory)

    active_guidance = project_context + CODING_AGENT_SYSTEM_PROMPT  # NEW

    turn_orchestrator = TurnOrchestrator(
        workspace=workspace,
        memory=memory,
        context_engine=context_engine,
        max_iterations=agent_config.max_iterations,
        guidance=active_guidance,       # was: CODING_AGENT_SYSTEM_PROMPT
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

- [ ] **Step 6: Call `load_project_context` in `main.py` and pass it to `build_coding_agent`**

In `argo/main.py`, add the import and the call right after `await shell_session.start()`:

```python
# After: await shell_session.start()
# Add these two lines:
from app.domain.services.runtime.project_context import load_project_context
project_context = await load_project_context(cwd)
```

Then in the `build_coding_agent(...)` call, add `project_context=project_context` as a new argument:

```python
agent = build_coding_agent(
    llm=llm,
    json_parser=json_parser,
    agent_config=agent_cfg,
    uow_factory=uow_factory,
    session_id=argo_session.session_id,
    workspace=workspace,
    shell_session=shell_session,
    pre_execute_hook=gateway.check,
    project_context=project_context,   # NEW
)
```

- [ ] **Step 7: Run all tests to check nothing regressed**

```bash
cd api && pytest tests/domain/services/runtime/test_project_context.py \
               tests/domain/services/agents/ -v
```

Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add api/app/domain/services/runtime/project_context.py \
        api/app/domain/services/agents/coding_agent.py \
        api/tests/domain/services/runtime/test_project_context.py \
        argo/main.py
git commit -m "feat: inject project context (README + git log + file structure) into agent system prompt"
```

---

## Task 3: Streaming LLM Output

**Files:**
- Modify: `api/app/domain/external/llm.py`
- Modify: `api/app/infrastructure/external/llm/openai_llm.py`
- Modify: `api/app/domain/models/event.py`
- Modify: `api/app/domain/services/agents/base.py`
- Modify: `api/app/domain/services/agents/coding_agent.py`
- Modify: `argo/renderer.py`
- Modify: `argo/tests/test_renderer.py` (add 2 new tests)
- Modify: `argo/main.py`
- Create: `api/tests/domain/services/runtime/test_llm_streaming.py`

**Interfaces:**
- Consumes: `build_coding_agent` and `BaseAgent` from Task 2 (already has `project_context`)
- Produces:
  - `OpenAILLM.invoke(..., text_callback: Callable[[str], None] | None = None) -> Dict[str, Any]`
  - `MessageEvent.streamed: bool` field (defaults `False`)
  - `BaseAgent.__init__(..., stream_callback: Callable[[str], None] | None = None, stream_reset: Callable[[], None] | None = None)`
  - `build_coding_agent(..., stream_callback=None, stream_reset=None)`

---

- [ ] **Step 1: Write the failing tests for the streaming path**

Create `api/tests/domain/services/runtime/test_llm_streaming.py`:

```python
from __future__ import annotations
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from app.domain.models.app_config import LLMConfig
from app.infrastructure.external.llm.openai_llm import OpenAILLM
from app.domain.models.event import MessageEvent


def _cfg() -> LLMConfig:
    return LLMConfig(
        base_url="http://localhost",
        api_key="test-key",
        model_name="test-model",
    )


class _FakeDelta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, content=None, tool_calls=None):
        self.delta = _FakeDelta(content, tool_calls)


class _FakeChunk:
    def __init__(self, content=None, tool_calls=None):
        self.choices = [_FakeChoice(content, tool_calls)]


@pytest.mark.asyncio
async def test_text_callback_called_for_each_chunk():
    llm = OpenAILLM(_cfg())
    received = []

    async def fake_stream():
        for chunk in [_FakeChunk("Hello"), _FakeChunk(" world"), _FakeChunk("!")]:
            yield chunk

    with patch.object(
        llm._client.chat.completions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = fake_stream()
        await llm.invoke(
            messages=[{"role": "user", "content": "hi"}],
            text_callback=received.append,
        )

    assert received == ["Hello", " world", "!"]


@pytest.mark.asyncio
async def test_accumulated_content_returned():
    llm = OpenAILLM(_cfg())

    async def fake_stream():
        for chunk in [_FakeChunk("foo"), _FakeChunk("bar")]:
            yield chunk

    with patch.object(
        llm._client.chat.completions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = fake_stream()
        result = await llm.invoke(
            messages=[{"role": "user", "content": "hi"}],
            text_callback=lambda x: None,
        )

    assert result["content"] == "foobar"
    assert result["tool_calls"] is None


@pytest.mark.asyncio
async def test_no_callback_uses_non_streaming_path():
    """When text_callback is None, stream=True must NOT be passed to create()."""
    llm = OpenAILLM(_cfg())

    fake_msg = MagicMock()
    fake_msg.model_dump.return_value = {
        "role": "assistant", "content": "hi", "tool_calls": None
    }
    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=fake_msg)]
    fake_response.usage = None

    with patch.object(
        llm._client.chat.completions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = fake_response
        result = await llm.invoke(messages=[{"role": "user", "content": "hello"}])
        assert mock_create.call_args.kwargs.get("stream", False) is False

    assert result["content"] == "hi"


def test_message_event_streamed_defaults_false():
    event = MessageEvent(role="assistant", message="hello")
    assert event.streamed is False


def test_message_event_streamed_field_can_be_set():
    event = MessageEvent(role="assistant", message="hello", streamed=True)
    assert event.streamed is True
```

- [ ] **Step 2: Add renderer tests for `streamed` field to `argo/tests/test_renderer.py`**

Append these two tests at the end of `argo/tests/test_renderer.py`:

```python
def test_streamed_message_event_renders_none():
    from argo.renderer import render_event
    event = MessageEvent(role="assistant", message="hello world", streamed=True)
    assert render_event(event) is None


def test_non_streamed_message_event_renders_text():
    from argo.renderer import render_event
    event = MessageEvent(role="assistant", message="hello world", streamed=False)
    result = render_event(event)
    assert result is not None
    assert "hello world" in result
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd api && pytest tests/domain/services/runtime/test_llm_streaming.py -v
pytest argo/tests/test_renderer.py::test_streamed_message_event_renders_none \
       argo/tests/test_renderer.py::test_non_streamed_message_event_renders_text -v
```

Expected: `test_llm_streaming.py` tests FAIL (no streaming path yet); the two new renderer tests FAIL (`MessageEvent` has no `streamed` field).

- [ ] **Step 4: Add `streamed` field to `MessageEvent`**

In `api/app/domain/models/event.py`, modify the `MessageEvent` class:

```python
class MessageEvent(BaseEvent):
    """消息事件，包含人类消息和AI消息"""
    type: Literal["message"] = "message"
    role: Literal["user", "assistant"] = "assistant"  # 消息角色
    message: str = ""  # 消息本身
    attachments: List[File] = Field(default_factory=list)  # 附件列表信息
    streamed: bool = False  # True means text was already written to stdout via stream_callback
```

- [ ] **Step 5: Add `text_callback` parameter to `LLM` Protocol**

In `api/app/domain/external/llm.py`, add `Callable` to imports and the new param:

```python
from typing import Callable, Protocol, List, Dict, Any


class LLM(Protocol):
    """用于Agent应用与LLM进行交互的接口协议"""

    async def invoke(
            self,
            messages: List[Dict[str, Any]],
            tools: List[Dict[str, Any]] = None,
            response_format: Dict[str, Any] = None,
            tool_choice: str = None,
            text_callback: Callable[[str], None] | None = None,   # NEW
    ) -> Dict[str, Any]:
        """传递消息列表、工具列表、响应格式、工具选择策略调用LLM接口"""
        ...

    @property
    def model_name(self) -> str:
        ...

    @property
    def temperature(self) -> float:
        ...

    @property
    def max_tokens(self) -> int:
        ...
```

- [ ] **Step 6: Implement the streaming path in `OpenAILLM`**

Replace the entire `api/app/infrastructure/external/llm/openai_llm.py` with:

```python
import logging
from typing import Callable, List, Dict, Any

from openai import AsyncOpenAI

from app.application.errors.exceptions import ServerRequestsError
from app.domain.external.llm import LLM
from app.domain.models.app_config import LLMConfig

logger = logging.getLogger(__name__)


class OpenAILLM(LLM):
    """基于OpenAI SDK/兼容OpenAI格式的LLM调用类"""

    def __init__(self, llm_config: LLMConfig, **kwargs) -> None:
        self._client = AsyncOpenAI(
            base_url=str(llm_config.base_url),
            api_key=llm_config.api_key,
            **kwargs,
        )
        self._model_name = llm_config.model_name
        self._temperature = llm_config.temperature
        self._max_tokens = llm_config.max_tokens
        self._timeout = 3600

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def temperature(self) -> float:
        return self._temperature

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    async def invoke(
            self,
            messages: List[Dict[str, Any]],
            tools: List[Dict[str, Any]] = None,
            response_format: Dict[str, Any] = None,
            tool_choice: str = None,
            text_callback: Callable[[str], None] | None = None,
    ) -> Dict[str, Any]:
        """调用LLM，text_callback 不为 None 时使用流式响应。"""
        try:
            if text_callback is not None:
                return await self._invoke_streaming(
                    messages, tools, response_format, tool_choice, text_callback
                )

            # Non-streaming path (unchanged)
            if tools:
                logger.info(f"调用OpenAI客户端向LLM发起请求并携带工具信息: {self._model_name}")
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
            else:
                logger.info(f"调用OpenAI客户端向LLM发起请求未携带: {self._model_name}")
                response = await self._client.chat.completions.create(
                    model=self._model_name,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                    messages=messages,
                    response_format=response_format,
                    timeout=self._timeout,
                )

            logger.info(f"OpenAI客户端返回内容: {response.model_dump()}")
            message_dict = response.choices[0].message.model_dump()
            if response.usage:
                message_dict["usage"] = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                }
            return message_dict

        except Exception as e:
            logger.error(f"调用OpenAI客户端发生错误: {str(e)}")
            raise ServerRequestsError("调用OpenAI客户端向LLM发起请求出错")

    async def _invoke_streaming(
            self,
            messages: List[Dict[str, Any]],
            tools: List[Dict[str, Any]] | None,
            response_format: Dict[str, Any] | None,
            tool_choice: str | None,
            text_callback: Callable[[str], None],
    ) -> Dict[str, Any]:
        """流式路径：每个文字 delta 立即调用 text_callback，工具调用 chunk 内部累积。"""
        kwargs: Dict[str, Any] = dict(
            model=self._model_name,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            messages=messages,
            stream=True,
            timeout=self._timeout,
        )
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        if response_format:
            kwargs["response_format"] = response_format

        stream = await self._client.chat.completions.create(**kwargs)

        full_content = ""
        tool_calls_acc: dict[int, dict] = {}

        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            if delta.content:
                full_content += delta.content
                text_callback(delta.content)

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": tc.id or "",
                            "type": "function",
                            "function": {"name": tc.function.name or "", "arguments": ""},
                        }
                    if tc.id:
                        tool_calls_acc[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_calls_acc[idx]["function"]["name"] += tc.function.name
                        if tc.function.arguments:
                            tool_calls_acc[idx]["function"]["arguments"] += tc.function.arguments

        tool_calls = list(tool_calls_acc.values()) if tool_calls_acc else None
        return {
            "role": "assistant",
            "content": full_content or None,
            "tool_calls": tool_calls,
        }


if __name__ == "__main__":
    import asyncio

    async def main():
        llm = OpenAILLM(LLMConfig(
            base_url="https://api.deepseek.com",
            api_key="",
            model_name="deepseek-chat",
        ))
        response = await llm.invoke([{"role": "user", "content": "Hi"}])
        print(response)

    asyncio.run(main())
```

- [ ] **Step 7: Run streaming tests to verify they pass**

```bash
cd api && pytest tests/domain/services/runtime/test_llm_streaming.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 8: Update `renderer.py` to skip streamed `MessageEvent`**

In `argo/renderer.py`, modify the `MessageEvent` branch of `render_event`:

```python
    if isinstance(event, MessageEvent):
        if event.streamed:
            return None   # already written to stdout chunk-by-chunk
        return f"{TEAL}{event.message}{RESET}"
```

- [ ] **Step 9: Run renderer tests**

```bash
pytest argo/tests/test_renderer.py -v
```

Expected: all tests PASS (including the two new ones added in Step 2).

- [ ] **Step 10: Add `stream_callback` and `stream_reset` to `BaseAgent`**

In `api/app/domain/services/agents/base.py`, modify `__init__` to accept two new optional parameters. Add them after `session_id`:

```python
    def __init__(
        self,
        llm: LLM,
        json_parser: JSONParser,
        agent_config: AgentConfig,
        tools: List[BaseTool],
        turn_orchestrator: TurnOrchestrator,
        tool_executor: ToolExecutor,
        checkpoint_service: CheckpointService,
        context_engine: ContextEngine,
        memory: ThreeLayerMemory,
        uow_factory: Callable[[], IUnitOfWork],
        session_id: str,
        stream_callback: Callable[[str], None] | None = None,   # NEW
        stream_reset: Callable[[], None] | None = None,         # NEW
    ) -> None:
        self._llm = llm
        self._json_parser = json_parser
        self._agent_config = agent_config
        self._tools = tools
        self._turn_orchestrator = turn_orchestrator
        self._tool_executor = tool_executor
        self._checkpoint_service = checkpoint_service
        self._context_engine = context_engine
        self._memory = memory
        self._uow_factory = uow_factory
        self._session_id = session_id
        self._stream_callback = stream_callback   # NEW
        self._stream_reset = stream_reset         # NEW
```

Then modify `_react_loop` to reset the prefix at turn start, pass `text_callback`, and end the streamed line:

```python
    async def _react_loop(
        self, messages: List[Dict[str, Any]], turn_ctx: "TurnContext"
    ) -> AsyncGenerator[BaseEvent, None]:
        """ReAct 循环：LLM 调用 → 工具执行 → 再次 LLM 调用，直到无工具调用或耗尽配额。"""
        # Reset stream prefix flag at the start of each agent turn
        if self._stream_reset is not None:
            self._stream_reset()

        while True:
            if not turn_ctx.iteration_budget.consume():
                yield ErrorEvent(error=f"Iteration limit reached ({self._agent_config.max_iterations})")
                return

            # LLM 调用 — pass stream callback for token-by-token output
            response = await self._llm.invoke(
                messages=messages,
                tools=self._get_tool_schemas(),
                text_callback=self._stream_callback,   # NEW
            )

            # 追踪 token 用量（用于 should_compress 决策）
            if "usage" in response:
                self._context_engine.update_from_response(response["usage"])

            tool_calls = response.get("tool_calls", [])
            content = response.get("content")

            # 将 assistant 消息加入列表
            msg: Dict[str, Any] = {"role": "assistant", "content": content}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            messages.append(msg)

            if not tool_calls:
                # 无工具调用 → 最终回答
                if content:
                    # If streaming was active, end the line and mark event as already displayed
                    if self._stream_callback is not None:
                        self._stream_callback("\n")
                    yield MessageEvent(
                        role="assistant",
                        message=content,
                        streamed=self._stream_callback is not None,   # NEW
                    )
                return

            # 处理工具调用（支持批量并发）
            parsed_calls: list[tuple[str, str, str, dict]] = []
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

                pre_kind = classify_tool_result(tool_name, ToolResult(success=True))
                if pre_kind == ToolResultKind.FILE_MUTATION:
                    filepath = (
                        arguments.get("filepath") or arguments.get("path") or
                        arguments.get("filename") or arguments.get("target") or ""
                    )
                    if filepath:
                        await self._checkpoint_service.snapshot(filepath, turn_id=turn_ctx.turn_id)

            batch_calls = [
                {"tool_name": tn, "arguments": args}
                for _, tn, _, args in parsed_calls
            ]
            batch_results = await self._tool_executor.execute_batch(batch_calls)

            tool_messages = []
            for (tc_id, tool_name, _, arguments), (result, _) in zip(parsed_calls, batch_results):
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

- [ ] **Step 11: Wire `stream_callback` and `stream_reset` through `build_coding_agent`**

In `api/app/domain/services/agents/coding_agent.py`, add the two new params and pass them to `BaseAgent`. The function now has all four new params from Tasks 2 and 3:

```python
def build_coding_agent(
    llm: LLM,
    json_parser: JSONParser,
    agent_config: AgentConfig,
    uow_factory: Callable[[], IUnitOfWork],
    session_id: str,
    workspace: WorkspaceContext,
    shell_session: PersistentShellSession | None = None,
    pre_execute_hook=None,
    project_context: str = "",
    stream_callback: Callable[[str], None] | None = None,   # NEW
    stream_reset: Callable[[], None] | None = None,         # NEW
) -> BaseAgent:
    """工厂函数：组装完整的 CodingAgent（所有 runtime 组件连线）。"""
    cwd = workspace.cwd
    todo_store = TodoStore()
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
        config=ContextEngineConfig(
            context_length=context_length,
            threshold_percent=0.75,
        ),
        llm=llm,
        protect_first_n=3,
        protect_last_n=6,
    )
    memory = ThreeLayerMemory(session_id=session_id, uow_factory=uow_factory)
    active_guidance = project_context + CODING_AGENT_SYSTEM_PROMPT
    turn_orchestrator = TurnOrchestrator(
        workspace=workspace,
        memory=memory,
        context_engine=context_engine,
        max_iterations=agent_config.max_iterations,
        guidance=active_guidance,
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
        stream_callback=stream_callback,   # NEW
        stream_reset=stream_reset,         # NEW
    )
```

- [ ] **Step 12: Add streaming closures to `main.py` and wire them**

In `argo/main.py`, after the `project_context = await load_project_context(cwd)` line (added in Task 2) and before the `build_coding_agent(...)` call, add:

```python
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
```

Then add the two new args to `build_coding_agent`:

```python
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
```

- [ ] **Step 13: Run all tests**

```bash
cd api && pytest tests/domain/services/runtime/test_llm_streaming.py \
               tests/domain/services/runtime/test_project_context.py \
               tests/domain/services/tools/test_code_search_enhanced.py \
               tests/domain/services/agents/ -v
pytest argo/tests/test_renderer.py -v
```

Expected: all tests PASS.

- [ ] **Step 14: Commit**

```bash
git add api/app/domain/external/llm.py \
        api/app/infrastructure/external/llm/openai_llm.py \
        api/app/domain/models/event.py \
        api/app/domain/services/agents/base.py \
        api/app/domain/services/agents/coding_agent.py \
        api/tests/domain/services/runtime/test_llm_streaming.py \
        argo/renderer.py \
        argo/tests/test_renderer.py \
        argo/main.py
git commit -m "feat: streaming LLM output — tokens appear in TUI as they arrive"
```
