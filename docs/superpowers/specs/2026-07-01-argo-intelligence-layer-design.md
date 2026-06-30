# Argo Intelligence Layer — Design Spec

> **For agentic workers:** Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Upgrade Argo's intelligence and UX layer with three independent improvements: streaming LLM output (tokens appear immediately in the TUI), project startup context (agent knows the project before the first message), and enhanced code search (symbol-level lookup and line-range file reads).

**Architecture:** Three self-contained components sharing no runtime state. Each is independently testable. No external dependencies added.

**Tech Stack:** Python 3.12, asyncio, OpenAI SDK (already installed), stdlib only for project context and code search.

---

## Global Constraints

- No new pip dependencies — OpenAI SDK already installed, stdlib only for new features.
- All existing public interfaces remain backward-compatible (`text_callback=None` default, `project_context=""` default).
- Streaming must work with DeepSeek's OpenAI-compatible API (uses `stream=True` in OpenAI SDK).
- Tests live in `api/tests/` (domain layer) and run from `api/` directory: `cd api && pytest tests/... -v`.
- Python 3.12 asyncio only — no threads.

---

## Architecture

```
Component 1: Streaming Output
  main.py (stream_cb closure)
    → build_coding_agent(stream_callback=)
      → BaseAgent(stream_callback=)   # stored as self._stream_callback
        → base.py _react_loop resets prefix flag, calls:
          → LLM.invoke(text_callback=self._stream_callback)
            → OpenAILLM: stream=True, yields delta chunks

Component 2: Project Startup Context
  main.py
    → load_project_context(cwd) → str
      → injected into build_coding_agent(project_context=)
        → prepended to CODING_AGENT_SYSTEM_PROMPT

Component 3: Code Search Enhancement
  CodeSearchTool.find_symbol(name)       → grep def/class definitions
  CodeSearchTool.read_file_range(fp, s, e) → read specific line range
```

---

## Component 1 — Streaming Output

### Files

| File | Change |
|------|--------|
| `api/app/domain/external/llm.py` | Add `text_callback` param to `invoke()` Protocol |
| `api/app/infrastructure/external/llm/openai_llm.py` | Implement streaming with chunk assembly |
| `api/app/domain/models/event.py` | Add `streamed: bool = False` field to `MessageEvent` |
| `api/app/domain/services/agents/base.py` | Store `stream_callback` + `stream_reset`; thread `text_callback` to `llm.invoke()`; reset prefix at turn start |
| `argo/renderer.py` | Skip rendering `MessageEvent` when `event.streamed is True` |
| `argo/main.py` | Define `stream_cb` + `stream_reset` closures, pass via `build_coding_agent` |
| `api/app/domain/services/agents/coding_agent.py` | Accept + pass `stream_callback` param |

### Interface Changes

**`api/app/domain/external/llm.py`** — `invoke()` gains one optional param:
```python
async def invoke(
    self,
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]] = None,
    response_format: Dict[str, Any] = None,
    tool_choice: str = None,
    text_callback: Callable[[str], None] | None = None,   # NEW
) -> Dict[str, Any]: ...
```

**`api/app/domain/models/event.py`** — `MessageEvent` gains:
```python
@dataclass
class MessageEvent(BaseEvent):
    role: str
    message: str
    streamed: bool = False   # NEW — renderer skips display when True
```

**`api/app/domain/services/agents/base.py`** — `BaseAgent.__init__()` gains:
```python
def __init__(
    self,
    ...,
    stream_callback: Callable[[str], None] | None = None,  # NEW
) -> None:
    self._stream_callback = stream_callback
```

At the start of `_react_loop`, reset the prefix flag so each turn shows a fresh `argo›`:
```python
# Reset prefix-written state at turn boundary
if self._stream_callback is not None:
    self._stream_reset()   # called via a reset_fn also passed at construction
```

**`api/app/domain/services/agents/coding_agent.py`** — `build_coding_agent()` gains:
```python
def build_coding_agent(
    ...,
    stream_callback: Callable[[str], None] | None = None,  # NEW
    stream_reset: Callable[[], None] | None = None,        # NEW
) -> BaseAgent:
    return BaseAgent(
        ...,
        stream_callback=stream_callback,
        stream_reset=stream_reset,
    )
```

### OpenAILLM Streaming Implementation

When `text_callback` is not None, use `stream=True`:

```python
async def invoke(self, messages, tools=None, ..., text_callback=None) -> Dict[str, Any]:
    if text_callback is None:
        # existing non-streaming path — unchanged
        ...
        return message_dict

    # Streaming path
    stream = await self._client.chat.completions.create(
        model=self._model_name,
        temperature=self._temperature,
        max_tokens=self._max_tokens,
        messages=messages,
        tools=tools or [],
        stream=True,
        timeout=self._timeout,
    )

    full_content = ""
    tool_calls_acc: dict[int, dict] = {}   # index → accumulated tool call

    async for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta is None:
            continue

        # Text delta
        if delta.content:
            full_content += delta.content
            text_callback(delta.content)

        # Tool call delta — accumulate by index
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
```

### base.py — Thread callback through _react_loop

In `base.py`, `_react_loop` receives `stream_callback` from `TurnOrchestrator`. When calling `llm.invoke()`:

```python
response = await self._llm.invoke(
    messages=full_messages,
    tools=tools_schema,
    text_callback=self._stream_callback,   # NEW
)
```

When yielding the `MessageEvent`, mark it as streamed if the callback was active and content was produced:
```python
if content:
    streamed = self._stream_callback is not None
    yield MessageEvent(role="assistant", message=content, streamed=streamed)
```

### renderer.py — Skip already-streamed messages

```python
def render_event(event: BaseEvent) -> str | None:
    if isinstance(event, MessageEvent):
        if event.streamed:
            return None   # already displayed chunk-by-chunk
        # existing rendering...
```

### main.py — stream_cb closure

Defined after `shell_session` creation, before `build_coding_agent`:

```python
_stream_prefix_written = False

def stream_cb(chunk: str) -> None:
    nonlocal _stream_prefix_written
    if not _stream_prefix_written:
        _write(f"  {_TEAL}argo{_RESET}{_GRAY}›{_RESET} ")
        _stream_prefix_written = True
    _write(chunk)
    sys.stdout.flush()

# Reset flag at each turn start (handled by wrapping in a factory below)
```

Because `stream_cb` is stateful (tracks whether the prefix has been written), it must be **reset between turns**. The cleanest approach: make `stream_cb` a closure created fresh per turn inside `ArgoApp._run_agent_turn()`.

`ArgoApp` gains a `stream_cb_factory: Callable[[], Callable[[str], None]] | None` attribute. Each call to `_run_agent_turn` creates a fresh callback and passes it to the agent's turn (by rebuilding the agent's stream_callback reference per turn via a wrapper on `TurnOrchestrator`).

Actually, the simplest approach: `TurnOrchestrator` exposes a `reset_stream_state()` method that `base.py` calls before each LLM invoke. The `stream_cb` closure itself uses a mutable container to reset the prefix-written flag.

**Simplest concrete implementation:**

```python
# In main.py, after session picker
_prefix_state = {"written": False}

def stream_cb(chunk: str) -> None:
    if not _prefix_state["written"]:
        _write(f"\n  {_TEAL}argo{_RESET}{_GRAY}›{_RESET} ")
        _prefix_state["written"] = True
    _write(chunk)
    sys.stdout.flush()

def reset_stream_prefix() -> None:
    _prefix_state["written"] = False
```

`TurnOrchestrator` also accepts `stream_reset: Callable[[], None] | None = None`. `base.py` calls `self._stream_reset()` at the start of each LLM invoke (before the prefix would be needed).

---

## Component 2 — Project Startup Context

### Files

| File | Change |
|------|--------|
| `api/app/domain/services/runtime/project_context.py` | **New** — `load_project_context(cwd)` |
| `api/app/domain/services/agents/coding_agent.py` | Accept `project_context: str = ""` param |
| `argo/main.py` | Call `load_project_context(cwd)`, pass to `build_coding_agent` |

### `load_project_context(cwd: str) -> str`

```python
async def load_project_context(cwd: str) -> str:
    """Read README, git log, and file structure for context injection."""
    sections: list[str] = []

    # 1. README
    for name in ("README.md", "README.rst", "README.txt", "README"):
        p = Path(cwd) / name
        if p.exists():
            text = p.read_text(errors="replace")[:3000]
            sections.append(f"## README\n{text}")
            break

    # 2. Recent git log
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

    # 3. Python files structure (top 2 levels)
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

### System Prompt Injection

In `coding_agent.py`, `build_coding_agent` receives `project_context: str = ""`. The system prompt becomes:

```python
active_system_prompt = project_context + CODING_AGENT_SYSTEM_PROMPT
```

This is injected via `TurnOrchestrator` — it already stores `guidance` (the system prompt string). Passing `project_context + guidance` as the guidance string is the minimal change.

### main.py call site

```python
# After shell_session.start(), before build_coding_agent
project_context = await load_project_context(cwd)

agent = build_coding_agent(
    ...,
    project_context=project_context,
    stream_callback=stream_cb,
)
```

---

## Component 3 — Code Search Enhancement

### Files

| File | Change |
|------|--------|
| `api/app/domain/services/tools/code_search.py` | Add `find_symbol` and `read_file_range` tools |

### `find_symbol(name: str, path: str = ".")`

```python
@tool(
    name="find_symbol",
    description="按名称查找函数或类定义的位置。返回 file:line 列表。",
    parameters={
        "name": {"type": "string", "description": "函数名或类名"},
        "path": {"type": "string", "description": "搜索目录（默认项目根目录）"},
    },
    required=["name"],
)
async def find_symbol(self, name: str, path: str = ".") -> ToolResult:
    pattern = rf"(def|class)\s+{re.escape(name)}\b"
    cmd = ["grep", "-rn", "-E", pattern, path]
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
        return ToolResult(success=False, message="Search timed out")
    except Exception as e:
        return ToolResult(success=False, message=str(e))
```

### `read_file_range(filepath: str, start_line: int, end_line: int)`

```python
@tool(
    name="read_file_range",
    description="读取文件指定行范围（包含行号）。用于查看大文件的特定函数或片段，避免读取整个文件。",
    parameters={
        "filepath": {"type": "string", "description": "文件路径"},
        "start_line": {"type": "integer", "description": "起始行（从 1 开始）"},
        "end_line": {"type": "integer", "description": "结束行（含）"},
    },
    required=["filepath", "start_line", "end_line"],
)
async def read_file_range(self, filepath: str, start_line: int, end_line: int) -> ToolResult:
    try:
        p = Path(self._cwd) / filepath if not Path(filepath).is_absolute() else Path(filepath)
        lines = p.read_text(errors="replace").splitlines()
        # Convert to 0-based, clamp to file length
        s = max(0, start_line - 1)
        e = min(len(lines), end_line)
        if s >= len(lines):
            return ToolResult(success=False, message=f"start_line {start_line} exceeds file length {len(lines)}")
        selected = lines[s:e]
        # Prepend line numbers
        numbered = "\n".join(f"{s + i + 1:4d}  {l}" for i, l in enumerate(selected))
        return ToolResult(success=True, data=numbered)
    except FileNotFoundError:
        return ToolResult(success=False, message=f"File not found: {filepath}")
    except Exception as e:
        return ToolResult(success=False, message=str(e))
```

---

## Error Handling

| Scenario | Behaviour |
|----------|-----------|
| DeepSeek stream connection drops mid-response | `async for` raises → caught by `base.py` try/except → `ErrorEvent` returned to user |
| README not found | `load_project_context` skips that section silently |
| `git log` times out (no git repo) | Caught, section skipped, rest of context still injected |
| `find_symbol` finds no results | `success=False` with clear message |
| `read_file_range` start > file length | `success=False` with file length in message |
| `text_callback=None` | Non-streaming path used, existing behavior unchanged |

---

## Testing

**`api/tests/infrastructure/external/llm/test_openai_llm_streaming.py`** (new)
- Mock `AsyncOpenAI` client to return a stream of fake chunks
- Assert `text_callback` called once per text chunk
- Assert tool call chunks are correctly accumulated
- Assert returned dict has correct `content` and `tool_calls`

**`api/tests/domain/services/runtime/test_project_context.py`** (new)
- `load_project_context` returns empty string for directory with no README and no git
- Returns README content when present (mocked file)
- Skips git section when git not available (mock subprocess fail)
- Output includes "# Project Context" header

**`api/tests/domain/services/tools/test_code_search_enhanced.py`** (new)
- `find_symbol("MyClass")` returns file:line when class exists in tmp dir
- `find_symbol("missing")` returns `success=False`
- `read_file_range(filepath, 2, 4)` returns correct lines with line numbers
- `read_file_range(filepath, 999, 1000)` returns `success=False` with helpful message

---

## Files Changed Summary

| File | Action |
|------|--------|
| `api/app/domain/external/llm.py` | Modify — add `text_callback` param |
| `api/app/infrastructure/external/llm/openai_llm.py` | Modify — streaming path |
| `api/app/domain/models/event.py` | Modify — `MessageEvent.streamed` field |
| `api/app/domain/services/agents/base.py` | Modify — thread callback, mark events |
| `api/app/domain/services/agents/base.py` | Modify — `stream_callback` + `stream_reset` params stored in BaseAgent |
| `argo/renderer.py` | Modify — skip streamed events |
| `api/app/domain/services/agents/coding_agent.py` | Modify — `stream_callback` + `project_context` params |
| `api/app/domain/services/runtime/project_context.py` | **New** |
| `argo/main.py` | Modify — `stream_cb` + `load_project_context` |
| `api/app/domain/services/tools/code_search.py` | Modify — 2 new tools |
| `api/tests/infrastructure/external/llm/test_openai_llm_streaming.py` | **New** |
| `api/tests/domain/services/runtime/test_project_context.py` | **New** |
| `api/tests/domain/services/tools/test_code_search_enhanced.py` | **New** |
