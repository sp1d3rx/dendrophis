# Step 1 Refactor: Extract ChatOrchestrator from Session

**Date:** 2026-05-17
**Files changed:** 3 created/modified
**Tests:** Import chain verified, ruff passes, session tests pending (require LLM)

## Summary

Extracted the chat turn loop (`send_message` → stream → tools → repeat) from
`Session` into a new `ChatOrchestrator` dataclass. `Session` now delegates
chat operations instead of owning them. The `SessionFactory` was updated to
construct `ChatOrchestrator` explicitly.

## Files Changed

### NEW: `dendrophis/session/chat.py` (~500 lines)

`ChatOrchestrator` — a `@dataclass` that owns the turn loop. Contains all
logic that was previously in `Session.send_message()` and
`Session._run_completion_loop()`:

- `send_message(text)` — validates input, handles slash commands, appends
  user message to context, runs understanding detection, generates memory
  associations, handles compaction, streams completion, runs tool loop
- `_run_completion_loop()` — the stream→record→execute tools→repeat loop
- `_handle_slash_command(text)` — skill activation/deactivation
- `_emit_stats_periodically()` — background stats emission during streaming
- `compact()` — delegates to the compactor function
- `is_caching_enabled()`, `current_model_supports_tools()`,
  `_get_current_model_cost_per_1k()` — capability checks (moved verbatim)
- `is_streaming()` — streaming state check

Also includes two module-level helpers moved from `session.py`:
- `_file_log()` — debug log file writer
- `_tool_log()` — tool execution log writer
- `_synthesize_tool_call_xml()` — extracted from inline `_tc_json` closure

**Fields** (all required by Factory, optional where noted):

```
context: ContextManager
llm: LLMClient
stats: SessionStats
config: DendrophisConfig
event_bus: EventBus
understanding_detector: UnderstandingPhaseDetector
tool_registry: ToolRegistry
tool_executor_session: SessionToolExecutor
skill_manager: SkillManager
compactor: Callable
association_generator: MemoryAssociationGenerator | None  (optional)
debug_logger: Callable | None  (optional)
models: list[ModelInfo]  (shared reference, mutated by Session)
session_id: str  (shared reference, mutated by Session)
stream_lock: threading.Lock  (shared reference)
cancel_flag: threading.Event  (shared reference)
```

### MODIFIED: `dendrophis/session/session.py` (565 lines, was 866)

**`__init__` changes:**
- Added `chat: ChatOrchestrator | None` parameter (2nd position)
- If `chat` is provided: wires shared references (`models`, `session_id`,
  `stream_lock`, `cancel_flag`) from Session into the orchestrator
- If `chat` is None: backward-compat fallback constructs one from individual
  components (same behavior as before)
- Removed `_association_generator` creation (moved to Factory)
- Removed `self._streaming` field (now in `ChatOrchestrator._streaming`)
- All other parameters retained for backward compatibility

**Methods changed:**

| Method | Before | After |
|--------|--------|-------|
| `send_message()` | ~130 lines, full logic | 4 lines, delegates to `self._chat.send_message(text)` |
| `compact()` | ~8 lines, calls compactor | 4 lines, delegates to `self._chat.compact()` |
| `is_streaming()` | returned `self._streaming` | delegates to `self._chat.is_streaming()` |
| `cancel_streaming()` | sets `self._cancel_flag` | unchanged (cancel_flag is shared reference) |

**Methods removed:**
- `_emit_stats_periodically()` — moved to ChatOrchestrator
- `_run_completion_loop()` — moved to ChatOrchestrator

**Imports cleaned:** `asyncio`, `contextlib`, `json` (and several event types)
were unused after extraction and removed via `ruff --fix`.

### MODIFIED: `dendrophis/session/factory/__init__.py`

**Construction order changed:**

1. `SessionToolExecutor` is now constructed *before* `ChatOrchestrator` and
   `Session` (was constructed after Session and poked into it)
2. `MemoryAssociationGenerator` is now constructed in the Factory (was
   constructed in `Session.__init__`)
3. `ChatOrchestrator` is constructed with all its dependencies explicitly
4. `Session` receives `chat=chat` instead of individually wiring everything
5. After Session construction, shared references are wired:
   - `chat.models = session.models`
   - `chat.session_id = session.session_id`
   - `tool_executor_session._pending_confirmations = session._pending_confirmations`
   - `tool_executor_session._confirmation_results = session._confirmation_results`
   - `tool_executor_session._cancel_flag = session._cancel_flag`
   - `tool_executor_session._emit = session._emit`
6. `session._tool_executor_session = tool_executor_session` (was set the same way)
7. `SessionEventHandler` is constructed and linked as before

## Shared References (Critical for Correctness)

These objects are shared between Session and ChatOrchestrator. Both hold
references to the *same* objects:

- **`models`** (`list[ModelInfo]`): Populated by `Session.fetch_models()`.
  Read by `ChatOrchestrator.current_model_supports_tools()` and
  `_get_current_model_cost_per_1k()`. The Factory sets `chat.models =
  session.models` after Session construction.

- **`session_id`** (`str`): Set by `Session.__init__`. Used by
  `ChatOrchestrator._run_completion_loop()` for `_tool_log()`. The Factory
  sets `chat.session_id = session.session_id` after Session construction.

- **`stream_lock`** (`threading.Lock`): Owned by Session, shared with
  ChatOrchestrator. Both use it in `send_message()` to guard against
  concurrent streaming.

- **`cancel_flag`** (`threading.Event`): Owned by Session, shared with
  ChatOrchestrator. Session's `cancel_streaming()` sets it;
  ChatOrchestrator's `_run_completion_loop()` checks it.

## External Dependencies Not Touched

These files access Session attributes directly and were NOT changed:

- `dendrophis/ui/app.py` — accesses `session_id`, `config.llm.model`,
  `config.llm.api_key`
- `dendrophis/ui/screens/main.py` — accesses `config.ui`, `context.messages`
- `dendrophis/ui/screens/settings.py` — accesses `_tool_registry`,
  `config_loader`
- `dendrophis/ui/widgets/panels/*.py` — access `stats`, `config`, `models`
- `dendrophis/cli.py` — accesses `_session.session_id`

All of these remain on `Session` and unchanged.

## Rollback

To revert Step 1:

1. Delete `dendrophis/session/chat.py`
2. Restore `dendrophis/session/session.py` from git
3. Restore `dendrophis/session/factory/__init__.py` from git

## Verification

```bash
# Import chain
python -c "from dendrophis.session.chat import ChatOrchestrator"
python -c "from dendrophis.session.session import Session"
python -c "from dendrophis.session.factory import SessionFactory"

# Lint
ruff check dendrophis/session/
```