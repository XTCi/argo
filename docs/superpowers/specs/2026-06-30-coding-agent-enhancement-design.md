# Coding Agent Enhancement — Design Spec

**Goal:** Make ny-agent capable of autonomously completing real coding tasks by adding a structured test runner, an in-session todo tracker, batch file patching, and parallel tool execution.

**Architecture:** Two parallel directions, one DDD layer. Direction A adds three new capabilities to the tool layer (test runner, todo, batch patch). Direction B upgrades the runtime execution scheduler to run read-safe tools concurrently. Both directions share the same spec and plan because they touch the same files (`ToolExecutor`, `BaseAgent`, `coding_agent.py`).

**Tech Stack:** Python 3.12, asyncio, pytest (subprocess), Pydantic v2, existing `BaseTool` / `ToolExecutor` / `TurnOrchestrator` patterns.

---

## Global Constraints

- All new code follows ny-agent DDD conventions: domain models in `domain/models/`, tool implementations in `domain/services/tools/`, runtime state in `domain/services/runtime/`, wiring in `domain/services/agents/`.
- All new tools use the existing `@tool` decorator and `BaseTool` base class from `domain/services/tools/base.py`.
- All new domain models use Pydantic v2 `BaseModel`.
- Tests use `pytest-asyncio`, `AsyncMock` for async methods, `MagicMock` for sync methods.
- No new external dependencies beyond the stdlib and packages already in the project.
- `ToolResultKind` classification in `tool_executor.py` must be kept consistent with new tool names.
- `patch_file` change must be backward-compatible: the old `(filepath, old_str, new_str)` signature continues to work.

---

## Direction A — Tool Enhancement

### A1. `run_tests` Tool

**File:** `api/app/domain/services/tools/test_runner.py`

A new `TestRunnerTool(BaseTool)` with a single `run_tests` method.

**Behavior:**
- Accepts: `path: str = "."`, `pattern: Optional[str] = None`, `timeout: int = 60`, `verbose: bool = False`
- Runs `python -m pytest <path> -v --tb=short --no-header [-k pattern]` via `asyncio.create_subprocess_exec` (not shell, no injection risk)
- Parses stdout for structured results: `passed`, `failed`, `errors`, `skipped`, `failure_details` (first 3000 chars of FAILURES section)
- Returns `ToolResult(success=returncode==0, data={"passed": N, "failed": N, "errors": N, "skipped": N, "failure_details": "..."})` 
- On timeout: kills process, returns `ToolResult(success=False, message="Tests timed out after Ns")`
- `ToolResultKind` classification: `TERMINAL` (existing category, already handled for truncation)

**Tool schema name:** `run_tests`

**Why not `shell_execute`:** Structured output matters. The agent needs to read `failed=3` and `failure_details`, not parse raw pytest output. Separate subprocess also allows future additions (coverage flag, JSON reporter).

---

### A2. `todo` Tool

**Files:**
- `api/app/domain/models/todo.py` — `TodoItem` domain model
- `api/app/domain/services/runtime/todo_store.py` — `TodoStore` in-session state
- `api/app/domain/services/tools/todo.py` — `TodoTool(BaseTool)`

**`TodoItem` model:**
```python
class TodoItem(BaseModel):
    id: str
    content: str
    status: Literal["pending", "in_progress", "done"]
```

**`TodoStore`:**
- In-session singleton (one per `CodingAgent` instance, created in `build_coding_agent`)
- `write(todos: list[dict]) -> list[TodoItem]` — replaces the full list (validates each item)
- `read() -> list[TodoItem]` — returns current list
- `format_for_injection() -> str` — formats as a compact markdown block for system prompt injection:
  ```
  ## Current Tasks
  - [ ] id1: content (pending)
  - [→] id2: content (in_progress)
  - [x] id3: content (done)
  ```
- Max 50 items, max 500 chars per content (truncated with `…`)

**`TodoTool`:**
- `todo_write(todos: list)` — calls `TodoStore.write()`, returns the full updated list as JSON
- `todo_read()` — calls `TodoStore.read()`, returns current list as JSON

**System prompt injection:**
- `TurnOrchestrator` already accepts a `guidance: str` param
- `TodoStore.format_for_injection()` is called in `build_turn_context` and appended to the active system prompt each turn
- `TurnOrchestrator.__init__` receives `todo_store: Optional[TodoStore] = None`; if provided, `_build_system_prompt()` appends the todo block after guidance

**`ToolResultKind` classification:** `OTHER` (existing fallthrough category)

---

### A3. `patch_file` Batch Replacement

**File:** `api/app/domain/services/tools/file_edit.py` (modify existing)

**Change:** `patch_file` accepts an optional `replacements: list[dict]` parameter (list of `{old_str, new_str}` objects) in addition to the existing `old_str` / `new_str` params. When `replacements` is provided, it takes priority and applies all replacements sequentially to the file content. Fails fast on the first replacement not found.

**Backward compatibility:** Old call `patch_file(filepath, old_str="...", new_str="...")` continues to work — it's normalized to `replacements=[{old_str, new_str}]` internally.

**Tool schema:** Add `replacements` as an optional array parameter alongside the existing `old_str`/`new_str` optional params.

---

## Direction B — Parallel Tool Execution

### B1. LLM parallel tool calls

**File:** `api/app/infrastructure/llm/openai_llm.py`

Change `parallel_tool_calls=False` to `parallel_tool_calls=True`. This allows the model to return multiple tool calls in a single response.

---

### B2. `ToolExecutor.execute_batch()`

**File:** `api/app/domain/services/runtime/tool_executor.py`

New method:
```python
async def execute_batch(
    self, calls: list[dict]  # [{tool_name, arguments}, ...]
) -> list[tuple[ToolResult, ToolResultKind]]
```

**Scheduling logic:**
- Classify each call using existing `classify_tool_result(tool_name, ...)` pre-flight
- `SEARCH` calls → run concurrently via `asyncio.gather(*[self.execute(name, args) for ...])`
- `FILE_MUTATION` and `TERMINAL` calls → run serially in original order
- Execution order: all SEARCH calls fire first (concurrently), then FILE_MUTATION/TERMINAL in order
- Results reassembled in original call order before returning

**Why SEARCH first:** Read-only calls have no side effects; running them concurrently before writes is safe. FILE_MUTATION calls must remain serial because each may depend on the state left by the previous one.

---

### B3. `BaseAgent._react_loop` update

**File:** `api/app/domain/services/agents/base.py`

- Remove `tool_calls[:1]` slice — process all tool calls returned by the model
- Replace per-tool `self._tool_executor.execute(tool_name, arguments)` with `self._tool_executor.execute_batch(calls)`
- Checkpoint loop: iterate over batch results, snapshot before any FILE_MUTATION call (pre-flight classify still used)
- Append all tool messages to `messages` before next LLM call

---

## Wiring

**File:** `api/app/domain/services/agents/coding_agent.py`

`build_coding_agent()` changes:
- Instantiate `TodoStore()`
- Instantiate `TestRunnerTool(cwd=workspace.cwd)`
- Instantiate `TodoTool(todo_store=todo_store)`
- Pass `todo_store` to `TurnOrchestrator(..., todo_store=todo_store)`
- Add both new tools to the `tools` list alongside existing tools

**File:** `api/app/domain/services/prompts/coding.py`

Extend `CODING_AGENT_SYSTEM_PROMPT` with usage guidance for `run_tests` and `todo_write`/`todo_read`.

---

## Testing

| New file | Tests |
|----------|-------|
| `test_test_runner.py` | run_tests returns structured data; timeout kills process; nonexistent path returns error |
| `test_todo.py` | todo_write replaces list; todo_read returns current; status validation rejects invalid values |
| `test_todo_store.py` | format_for_injection renders correct markdown; max item/content caps enforced |
| `test_tool_executor.py` (extend) | execute_batch runs SEARCH concurrently; FILE_MUTATION runs serially; results in original order |

Existing `test_base_agent.py` must continue passing — no regression.
