# Argo Execution Layer — Design Spec

> **For agentic workers:** Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Upgrade Argo's execution layer with three improvements: a persistent shell session (bash process lives for the entire Argo session), fuzzy file patching (4-level strategy chain with helpful error messages), and a configurable permission gateway (deny/ask/allow rules with TUI confirmation and `--yolo` flag).

**Architecture:** `PersistentShellSession` wraps a long-lived bash process; `ShellTool` exposes three tools on top of it; `FuzzyPatcher` replaces the exact-match logic inside `patch_file`; `PermissionGateway` intercepts `shell_execute` and `shell_background` calls before execution.

**Tech Stack:** Python 3.12, asyncio, `difflib` (stdlib), `prompt_toolkit` (already installed), `config.yaml` for permission rules.

---

## Global Constraints

- No new external dependencies — only stdlib and `prompt_toolkit` (already installed).
- `BaseAgent`, `ToolExecutor`, and `BaseTool` interfaces are not changed in signature — only `ShellTool` and `FileEditTool` implementations change.
- `config.yaml` gains a new top-level `permissions:` key; existing keys are untouched.
- `--yolo` is parsed in `argo/__main__.py` and threaded through to `PermissionGateway`; no other startup code changes.
- All new async code uses `asyncio` primitives only — no threads except where stdlib forces it.
- Tests live in `argo/tests/` (for TUI/config changes) and `api/tests/` (for domain-layer changes).

---

## Architecture

```
PersistentShellSession          (new: api/app/domain/services/tools/shell_session.py)
    ↓ used by
ShellTool                       (rewrite: api/app/domain/services/tools/shell.py)
  ├─ shell_execute(command, timeout)
  ├─ shell_background(command, process_id)
  └─ read_output(process_id, wait_seconds)
    ↓ intercepted by
PermissionGateway               (new: argo/permissions.py)
    ↓ called from
ToolExecutor.execute_batch()    (modify: api/app/domain/services/runtime/tool_executor.py)

FuzzyPatcher                    (new helper inside: api/app/domain/services/tools/file_edit.py)
    ↓ used by
FileEditTool.patch_file()       (modify: api/app/domain/services/tools/file_edit.py)

TUI confirmation UI             (modify: argo/app.py)
--yolo flag                     (modify: argo/__main__.py, argo/main.py)
```

---

## Component 1 — PersistentShellSession

**File:** `api/app/domain/services/tools/shell_session.py` (new)

A single bash process that lives for the entire Argo session. Commands are sent over stdin; a sentinel string marks command completion.

**Sentinel protocol:**
```
stdin  ←  f'{command}\necho "__ARGO_DONE__:$?"\n'
stdout →  <output lines...>
stdout →  "__ARGO_DONE__:0"   ← exit code extracted here
```

**Background process tracking:**
- `shell_background(command, process_id)` spawns a separate `asyncio.subprocess` (not through the persistent bash), captures stdout+stderr into an in-memory ring buffer (max 10 000 lines per process).
- `read_output(process_id, wait_seconds)` waits `wait_seconds` then drains the buffer.
- Ring buffer is a `collections.deque(maxlen=10000)`.

**Lifecycle:**
- `start()` — called once at Argo startup, creates the bash process.
- Auto-restart — if `self._proc.returncode is not None` (bash died), next tool call transparently calls `start()` again and logs a warning.
- `close()` — registered with `atexit`; sends `SIGTERM`, waits 2 s, then `SIGKILL`.

**Interface:**
```python
class PersistentShellSession:
    async def start(self) -> None: ...
    async def run(self, command: str, timeout: int = 30) -> tuple[str, int]:
        """Returns (output, exit_code)."""
    async def run_background(self, command: str, process_id: str) -> None: ...
    async def read_output(self, process_id: str, wait_seconds: float = 2.0) -> str: ...
    async def close(self) -> None: ...
```

---

## Component 2 — ShellTool (rewrite)

**File:** `api/app/domain/services/tools/shell.py` (rewrite)

Wraps `PersistentShellSession`. The session instance is injected at construction time (same instance shared across all tool calls within one Argo session).

**Three tools:**

`shell_execute(command: str, timeout: int = 30) → ToolResult`
- Calls `session.run(command, timeout)`.
- `success = exit_code == 0`.
- `data` = raw stdout string.
- `message` = `f"exit {exit_code}"`.

`shell_background(command: str, process_id: str) → ToolResult`
- Calls `session.run_background(command, process_id)`.
- Returns immediately with `success=True, message=f"started as {process_id}"`.
- `process_id` is chosen by the LLM (e.g. `"dev-server"`).

`read_output(process_id: str, wait_seconds: float = 2.0) → ToolResult`
- Calls `session.read_output(process_id, wait_seconds)`.
- Returns buffered output. If `process_id` unknown: `success=False`.

**Constructor:**
```python
class ShellTool(BaseTool):
    def __init__(self, session: PersistentShellSession, cwd: str) -> None: ...
```

---

## Component 3 — FuzzyPatcher

**File:** `api/app/domain/services/tools/file_edit.py` (modify `patch_file` only)

A private helper `_fuzzy_find_and_replace(content, old_str, new_str)` is added. `patch_file`'s public interface (`filepath`, `old_str`, `new_str`, `replacements`) is unchanged.

**Strategy chain (tried in order, first hit wins):**

| # | Name | Method |
|---|------|--------|
| 1 | `exact` | `content.find(old_str)` |
| 2 | `line_trim` | Strip leading/trailing whitespace from each line before comparing |
| 3 | `whitespace_norm` | Collapse runs of spaces/tabs to a single space |
| 4 | `escape_norm` | Convert `\\n` literals → real newlines, `\\t` → real tabs |

**Match outcome handling:**

| Matches found | Strategy | Action |
|--------------|----------|--------|
| 1 | `exact` | Replace, no message |
| 1 | any fuzzy | Replace; `result.message` notes strategy used |
| 0 | all failed | `success=False`; run `difflib.get_close_matches` on first line of `old_str` against all file lines; return top-3 candidates as hint |
| >1 | any | `success=False`; message: `"Found {n} matches — add more context to old_str to make it unique"` |

**difflib hint example (returned to LLM on 0-match):**
```
patch_file failed: old_str not found in src/main.py

Closest lines in file:
  line 42: def process_data(input: str) -> dict:
  line 57: def process_request(input: str) -> None:

Suggestion: expand old_str to include surrounding lines for a unique match.
```

---

## Component 4 — PermissionGateway

**File:** `argo/permissions.py` (new)

**`config.yaml` schema addition:**
```yaml
permissions:
  mode: ask            # ask | yolo | strict
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
  allow:               # overrides ask (exact substring match)
    - "git status"
    - "git log"
    - "ls"
    - "cat "
```

**Rule evaluation (first match wins):**
```
1. deny list  → ToolResult(success=False, message="Permission denied: <rule>")
2. allow list → execute immediately (bypass ask)
3. ask list   → trigger TUI confirmation
4. fallthrough → mode=yolo: execute | mode=ask: trigger TUI confirmation | mode=strict: deny
```

**`PermissionGateway` interface:**
```python
class PermissionGateway:
    def __init__(self, config: dict, yolo: bool = False,
                 confirm_fn: Callable[[str], Awaitable[str]] | None = None): ...
    async def check(self, tool_name: str, arguments: dict) -> Literal["allow", "deny"]:
        """Returns 'allow' or 'deny'. May call confirm_fn for 'ask' cases."""
```

`confirm_fn` is injected from `ArgoApp` — it renders the TUI prompt and returns `"y"`, `"!"`, or `"n"`. This keeps `PermissionGateway` independent of the TUI.

**TUI confirmation UI (in `argo/app.py`):**
```
  ⚠  argo wants to run:
     rm -rf dist/

  [y] allow once   [!] always allow this session   [n] deny
  Choose›
```
- Rendered with `_OCHRE` color for the warning, same key-hints style as the main input.
- `[!]` adds the command substring to an in-memory `session_allowlist` set (not written to disk).
- If denied: `ToolResult(success=False, message="Denied by user.")` returned to Agent.

**`--yolo` flag:**
- Parsed in `argo/__main__.py`: `python -m argo --yolo`.
- Passed as `yolo=True` to `PermissionGateway(yolo=True)`.
- When `yolo=True`, `check()` always returns `"allow"` regardless of rules.

**Insertion point in `ToolExecutor`:**
- `execute_batch()` calls `gateway.check(tool_name, arguments)` before each tool's `execute()`.
- Only `shell_execute` and `shell_background` are subject to gateway checks.
- Gateway instance is injected into `ToolExecutor` at construction in `main.py`.

---

## Error Handling

| Scenario | Behaviour |
|----------|-----------|
| bash process dies mid-session | Auto-restart on next call; warn in TUI: `⚠ shell restarted` |
| Background process exits with error | `read_output` returns whatever was buffered + exit code in message |
| `patch_file` all strategies fail | Return `success=False` with difflib hint; Agent retries |
| `patch_file` >1 match | Return `success=False` with count + instruction; Agent retries |
| Permission denied by rule | `ToolResult(success=False)`; Agent receives error and can inform user |
| Permission denied by user | Same as above; TUI shows `[denied]` inline |
| `--yolo` + deny rule | deny rules still apply (only `ask` rules are bypassed) |

---

## Testing

**`api/tests/domain/services/tools/test_shell_session.py`** (new)
- `start()` creates a bash process.
- `run("echo hello")` returns `("hello\n", 0)`.
- `run("exit 1 || true; false")` returns exit code 1.
- `run("cd /tmp && pwd")` returns `/tmp\n`.
- Next `run("pwd")` still returns `/tmp\n` (persistence test).
- `run_background` + `read_output` returns buffered output.
- Auto-restart: kill bash process, next `run()` succeeds.

**`api/tests/domain/services/tools/test_file_edit_fuzzy.py`** (new)
- Exact match replaces correctly.
- Line-trim match handles indentation drift.
- Whitespace-norm match handles extra spaces.
- 0-match returns difflib hint containing closest line.
- >1 match returns "not unique" error.
- Multi-replacement fail-fast: first bad replacement aborts, file unchanged.

**`argo/tests/test_permissions.py`** (new)
- Deny rule blocks `rm -rf /`.
- Allow rule bypasses ask for `git log`.
- Ask rule triggers `confirm_fn`.
- `[!]` response adds to session allowlist.
- `yolo=True` skips all ask checks.
- `yolo=True` still blocks deny rules.

---

## Files Changed

| File | Change |
|------|--------|
| `api/app/domain/services/tools/shell_session.py` | **New** — PersistentShellSession |
| `api/app/domain/services/tools/shell.py` | **Rewrite** — 3 tools on top of session |
| `api/app/domain/services/tools/file_edit.py` | **Modify** — add FuzzyPatcher inside patch_file |
| `api/app/domain/services/runtime/tool_executor.py` | **Modify** — inject + call PermissionGateway |
| `api/app/domain/services/agents/coding_agent.py` | **Modify** — pass session + gateway to tools |
| `argo/permissions.py` | **New** — PermissionGateway |
| `argo/app.py` | **Modify** — confirm_fn + `[!]` session allowlist |
| `argo/__main__.py` | **Modify** — parse `--yolo` |
| `argo/main.py` | **Modify** — create session + gateway, pass to app |
| `api/config.yaml.example` | **Modify** — add `permissions:` block |
| `api/tests/domain/services/tools/test_shell_session.py` | **New** |
| `api/tests/domain/services/tools/test_file_edit_fuzzy.py` | **New** |
| `argo/tests/test_permissions.py` | **New** |
