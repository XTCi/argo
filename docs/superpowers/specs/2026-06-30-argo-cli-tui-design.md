# Argo CLI TUI — Design Spec

**Goal:** Replace the web-based ny-agent frontend with a standalone terminal TUI (called **Argo**) that runs directly on the local machine without Docker, a database, or a running API server. The agent works on the user's real filesystem.

**Architecture:** A new `argo/` top-level directory that imports ny-agent's domain layer directly (`build_coding_agent()`), wires it with in-memory repository adapters (no PostgreSQL/Redis), and drives a prompt_toolkit + ANSI TUI.

**Tech Stack:** Python 3.12, prompt_toolkit 3.0.x (already installed), ANSI escape codes for terminal rendering (same approach as MiniCode-Python), existing ny-agent domain layer (unchanged).

---

## Global Constraints

- No new external dependencies: only prompt_toolkit (already installed) and stdlib.
- `build_coding_agent()` in `api/app/domain/services/agents/coding_agent.py` is not modified.
- All ny-agent domain code lives in `api/app/` and is imported by adding `api/` to `sys.path`.
- Session data stored in `~/.argo/sessions/<session_id>.json` (one file per session).
- Config loaded from `api/config.yaml` (same file the API server uses).
- Command to start: `python -m argo` from the project root (`ny-agent/`).
- Working directory: wherever the user launched the command (`os.getcwd()`).
- The project is renamed **Argo** for the CLI layer; the internal `api/` directory is unchanged.

---

## Directory Structure

```
ny-agent/
  argo/
    __main__.py         ← entry point: python -m argo
    main.py             ← startup orchestration (config → session picker → TUI)
    app.py              ← TUI core: alternate screen, render loop, prompt_toolkit input
    renderer.py         ← maps BaseAgent events → ANSI terminal output
    session.py          ← JSON session load/save in ~/.argo/sessions/
    config.py           ← reads api/config.yaml, builds LLMConfig + AgentConfig
    adapters/
      __init__.py
      uow.py            ← InMemoryUoW (implements IUnitOfWork without a database)
      repos.py          ← InMemorySessionRepo + InMemoryCheckpointRepo + NoopFileRepo
```

---

## Component Responsibilities

### `config.py`
- Adds `<project_root>/api` to `sys.path` before any domain imports.
- Reads `api/config.yaml` using the existing Pydantic models (`LLMConfig`, `AgentConfig`).
- Exposes `load_config() -> tuple[LLMConfig, AgentConfig]`.

### `adapters/repos.py`
Three in-memory implementations of the domain repository protocols:

**`InMemorySessionRepo`**
- Stores LLM message history (`messages: list[dict]`) and checkpoint list in memory.
- `save_memory(session_id, agent_name, memory)` → writes to in-memory dict AND triggers a JSON file save via the session module.
- `get_memory(session_id, agent_name)` → returns from in-memory dict (populated at startup from the loaded session JSON).
- All other `SessionRepository` methods (titles, unread counts, status) are no-ops.

**`InMemoryCheckpointRepo`**
- Stores file snapshots in a `dict[str, list[Checkpoint]]` keyed by `filepath`.
- `save(checkpoint)`, `get_latest(session_id, filepath)`, `get_by_session_and_path(...)` — all in-memory.
- Checkpoints are serialized into the session JSON alongside messages.

**`NoopFileRepo`**
- All methods are async no-ops (file attachment upload feature not needed in CLI).

### `adapters/uow.py`
**`InMemoryUoW`**
- Implements `IUnitOfWork` as an async context manager.
- `commit()` and `rollback()` are no-ops (no database transaction).
- Exposes `session: InMemorySessionRepo`, `checkpoint: InMemoryCheckpointRepo`, `file: NoopFileRepo`.
- `uow_factory` wraps the **same** `session_repo` and `checkpoint_repo` instances (not new ones per call), so in-memory state persists across turns: `uow_factory = lambda: InMemoryUoW(session_repo, checkpoint_repo)` where both repos are created once in `main.py` and captured in the closure.

### `session.py`
Session persistence to `~/.argo/sessions/`:

```python
@dataclass
class ArgoSession:
    session_id: str
    cwd: str
    created_at: float
    updated_at: float
    messages: list[dict]       # LLM conversation history
    checkpoints: list[dict]    # file snapshots (serialized Checkpoint objects)
```

- `save_session(session: ArgoSession) -> None` — writes JSON atomically (write to `.tmp` then rename).
- `load_session(session_id: str) -> ArgoSession` — reads from JSON file.
- `list_sessions(cwd: str) -> list[ArgoSession]` — returns sessions for current CWD, sorted by `updated_at` desc, max 5.
- `new_session(cwd: str) -> ArgoSession` — creates fresh session with new UUID.

### `renderer.py`
Converts `BaseAgent` event stream (`AsyncGenerator[BaseEvent, None]`) to ANSI terminal output:

| Event | Output |
|-------|--------|
| `ToolEvent(CALLING)` | `  ⟳ tool_name args…` (dim gray, args truncated to 60 chars) |
| `ToolEvent(CALLED, success=True)` | `  ✓ tool_name → result summary` (green) |
| `ToolEvent(CALLED, success=False)` | `  ✗ tool_name → error message` (red) |
| `MessageEvent` | Plain text, streamed word-by-word (no buffering) |
| `ErrorEvent` | Red bold `[error] message` |
| `DoneEvent` | Print blank line, re-show input prompt |

- Shell output exceeding 10 lines is truncated; a `[+N more lines]` hint is shown.
- Tool args rendering: `{"filepath": "src/main.py", "old_str": "foo…"}` → `src/main.py`.

### `app.py`
TUI core — manages the alternate screen buffer and the render/input loop:

- **Startup:** enter alternate screen (`\033[?1049h`), hide cursor, show header bar.
- **Header bar:** `argo  <model>  <cwd>  session:<N>` — rendered at top, refreshed each turn.
- **Scrollable history:** conversation transcript stored as list of rendered lines; scroll position tracked.
- **Input area:** `prompt_toolkit.PromptSession` with `>` prompt, multiline disabled, history from `~/.argo/history.txt`.
- **Shutdown:** restore cursor and exit alternate screen on `Ctrl+C` / `/exit`.

Layout (80-column reference):
```
┌────────────────────────────────────────────────────────────────┐
│  argo  deepseek-chat  ~/my-project                  session:3  │
├────────────────────────────────────────────────────────────────┤
│  [scrollable conversation area]                                │
│                                                                │
│  User: 帮我写一个测试                                          │
│  ⟳ read_file src/main.py                                       │
│  ✓ read_file → 42 行                                           │
│  ⟳ run_tests .                                                 │
│  ✓ run_tests → 5 passed, 0 failed                              │
│                                                                │
│  这是针对 main.py 的测试方案...                                │
│                                                                │
├────────────────────────────────────────────────────────────────┤
│  > _                                                           │
└────────────────────────────────────────────────────────────────┘
```

### `main.py`
Startup orchestration:

```
1. load_config() → LLMConfig, AgentConfig
2. detect CWD = os.getcwd()
3. list_sessions(cwd) → show picker (or auto-start new if none)
4. load_session / new_session → ArgoSession
5. build InMemoryUoW, InMemorySessionRepo (pre-loaded with session.messages)
6. build_coding_agent(llm, json_parser, agent_config, uow_factory, session_id, workspace)
7. app.run() → REPL loop
   a. prompt_toolkit input → user_message
   b. handle slash commands (/new, /resume, /clear, /exit, /help)
   c. agent.run(user_message, session_messages) → stream events
   d. renderer renders events to screen
   e. save_session() after each turn
```

---

## Slash Commands

| Command | Action |
|---------|--------|
| `/new` | End current session, start fresh (new session_id) |
| `/resume` | Show session picker for current CWD |
| `/clear` | Clear visible transcript (session kept in memory) |
| `/exit` | Exit TUI, restore terminal |
| `/help` | Print command list inline |

---

## Session Persistence Flow

```
Agent turn completes
  ↓
InMemorySessionRepo.save_memory() called by BaseAgent
  ↓
repo holds updated messages in memory
  ↓
main.py calls save_session(argo_session) after each turn
  ↓
~/.argo/sessions/<session_id>.json updated atomically
```

On next startup with `/resume`: load JSON → populate `InMemorySessionRepo._messages` → pass to `agent.run()` as `session_messages`.

---

## Error Handling

- **LLM call fails:** `ErrorEvent` surfaces in renderer; session still saved; user can retry.
- **Tool execution error:** `ToolResult(success=False)` rendered as `✗`; agent continues loop.
- **Config file missing:** print clear error before entering TUI, suggest `cp api/config.yaml.example api/config.yaml`.
- **Session JSON corrupt:** skip the corrupt session in the picker; log warning to stderr.
- **Ctrl+C during agent turn:** cancel the async generator, print `[interrupted]`, restore prompt.

---

## Testing

- Tests live in `argo/tests/`.
- `test_session.py` — save/load round-trip, list ordering, concurrent CWD filtering.
- `test_renderer.py` — event → ANSI string mappings, truncation at 60 chars, 10-line shell cap.
- `test_adapters.py` — InMemoryUoW context manager, save_memory persists and get_memory recalls.
- `test_config.py` — config.yaml parsing, missing key handling.
- No integration tests against real LLM (mocked via `AsyncMock`).
