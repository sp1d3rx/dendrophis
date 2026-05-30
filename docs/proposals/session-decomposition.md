# Proposal: Decompose Session into Focused Collaborators

**Status:** Complete
**Date:** 2026-05-17
**Archived:** 2026-05-17

## Summary

All three extraction steps completed successfully. Session reduced from ~866 lines to ~511 lines. Application starts and runs correctly.

## What Was Done

### Step 1: ChatOrchestrator
- **Created:** `dendrophis/session/chat.py` (~500 lines)
- **Extracted:** `send_message()`, `_run_completion_loop()`, `_emit_stats_periodically()`, `compact()`
- **Session change:** Now delegates to `self._chat.send_message()` etc.
- **Factory change:** Constructs `ChatOrchestrator` before Session, wires shared references

### Step 2: SubagentBootstrapper
- **Created:** `dendrophis/session/subagents.py` (~111 lines)
- **Extracted:** `_initialize_subagents()` method, `invoke_subagent()` logic
- **Session change:** Removed `_initialize_subagents()` method, delegates `invoke_subagent()` to bootstrapper
- **Factory change:** Creates `SubagentBootstrapper`, calls `initialize()`, passes to Session

### Step 3: MemoryAssociationGenerator Cleanup
- **Modified:** `dendrophis/session/chat.py`
- **Cleaned:** Moved import to top-level, removed `[ASSOC-DEBUG]` logging, improved type annotation
- **Result:** Cleaner code, proper typing, ~6 lines removed

## Final Metrics

| Metric | Before | After |
|--------|--------|-------|
| Session lines | ~866 | ~511 (-41%) |
| `__init__` params | 15 | 15 (swapped subagent_bootstrapper for _subagent_executor) |
| Session methods | ~25 | ~22 (-3) |
| New collaborators | 0 | 2 |
| ruff check | clean | clean |

## Architecture After

```
Factory
  │
  ├── creates: ChatOrchestrator (turn loop)
  ├── creates: SubagentBootstrapper (subagent init)
  ├── creates: Session (facade)
  │
  └── wires: shared references (models, session_id, locks, flags)

Session (facade)
  ├── delegates: send_message() → ChatOrchestrator
  ├── delegates: invoke_subagent() → SubagentBootstrapper
  └── owns: lifecycle, configuration, model switching
```

## Files Changed

- `dendrophis/session/chat.py` (new)
- `dendrophis/session/subagents.py` (new)
- `dendrophis/session/session.py` (modified)
- `dendrophis/session/factory/__init__.py` (modified)

## See Also

- `step1_refactor.md` — detailed change log for ChatOrchestrator
- `step2_refactor.md` — detailed change log for SubagentBootstrapper
- `step3_refactor.md` — detailed change log for cleanup

---

*Original proposal follows:*

## Current State

`Session.__init__` takes 15 parameters (1 required + 14 optional DI). After
construction, the Factory reaches into Session to set `_event_handler` and
`_tool_executor_session` — temporal coupling. The `__init__` body also
unconditionally initializes subagents (6 handler registrations) and
conditionally creates a memory association generator.

The class docstring claims 5 responsibilities but the code reveals ~18.

## Root Cause

Session is the "composition root" but also the "does everything" object. The
extracted classes (`SessionPersister`, `PrimerManager`, `SessionEventHandler`,
`SessionToolExecutor`) are good moves, but they're still wired *through*
Session rather than *alongside* it. Session remains the bottleneck.

## Proposed Changes

Three new collaborators, extracted from Session:

### 1. `ChatOrchestrator` — owns the turn loop

Extract `send_message()` and `_run_completion_loop()` into a new class. This
is the core "stream → tools → repeat" logic. It depends on `ContextManager`,
`LLMClient`, `SessionToolExecutor`, `SessionStats`, and the event bus — all
things the Factory already has.

```python
@dataclass
class ChatOrchestrator:
    context: ContextManager
    llm: LLMClient
    tool_executor: SessionToolExecutor
    tool_registry: ToolRegistry
    stats: SessionStats
    event_bus: EventBus
    config: DendrophisConfig
    understanding_detector: UnderstandingPhaseDetector
    association_generator: MemoryAssociationGenerator | None
    compactor: Callable
    cancel_flag: threading.Event
    stream_lock: threading.Lock
    debug_logger: Callable | None

    async def send_message(self, text: str) -> None: ...
    async def _run_completion_loop(self) -> None: ...
```

Session delegates `send_message()` to it. Session's `__init__` drops ~400
lines and 6 dependencies.

### 2. `SubagentBootstrapper` — one-time setup, not a Session concern

Move `_initialize_subagents()` out of Session entirely. The Factory calls it
once and passes the executor to whoever needs it.

```python
class SubagentBootstrapper:
    @staticmethod
    def bootstrap(memory_store: MemoryStore | None) -> SubagentExecutor:
        executor = SubagentExecutor()
        registry = get_registry()
        registry.register_handler("researcher", ResearcherHandler(memory_store).execute)
        registry.register_handler("code-writer", CodeWriterHandler().execute)
        # ... etc
        set_session_executor(executor)
        return executor
```

Session drops `_initialize_subagents()`, `_subagent_executor`, and 6 imports.

### 3. `MemoryAssociationGenerator` — already exists, wire externally

Currently Session creates this conditionally in `__init__`. The Factory
already has `memory_store` — it can create the generator and pass it to
`ChatOrchestrator`. Session drops `_association_generator` and its
conditional import.

## What Session Keeps

After extraction, Session becomes a thin facade:

```python
class Session:
    def __init__(
        self,
        config_loader: ConfigLoader,
        chat: ChatOrchestrator,
        event_handler: SessionEventHandler,
        persister: SessionPersister,
        primer_manager: PrimerManager,
        event_bus: EventBus | None = None,
        debug_logger: Callable | None = None,
    ) -> None: ...
```

## Before/After

| Metric | Before | After |
|--------|--------|-------|
| `__init__` params | 15 | 7 |
| `__init__` body lines | ~90 | ~25 |
| Temporal coupling (post-construction setters) | 2 | 0 |
| Unconditional init in `__init__` | Subagents, memory assoc | None |
| Session responsibilities | ~18 | ~6 (facade + lifecycle) |

## Factory Impact

The Factory grows slightly — it now constructs `ChatOrchestrator` and calls
`SubagentBootstrapper.bootstrap()`. But the wiring is explicit and linear, not
"construct Session, then poke its internals."

## Risk Assessment

- **Low risk**: `ChatOrchestrator` is a pure extraction — same logic, new home.
  The turn loop is self-contained.
- **Low risk**: `SubagentBootstrapper` is a static method — no state, just
  registration.
- **Medium risk**: `SessionEventHandler` currently holds references to
  `session._stream_lock`, `session._cancel_flag`, etc. These move to
  `ChatOrchestrator` and the handler references them through the chat object
  instead. Same references, different path.
- **Mitigation**: Existing tests that mock Session will need updating, but the
  public API (`.send_message()`, `.cancel_streaming()`, etc.) remains identical.

## Migration Path

1. Extract `ChatOrchestrator` — move `send_message()` and `_run_completion_loop()` verbatim
2. Extract `SubagentBootstrapper` — move `_initialize_subagents()` to static method
3. Move `_association_generator` creation to Factory
4. Slim down `Session.__init__` to facade-only
5. Update `SessionEventHandler` to reference `ChatOrchestrator` instead of `Session` internals
6. Update tests

Each step is independently testable and reversible.
