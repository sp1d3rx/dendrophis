# Step 2 Refactor: Extract SubagentBootstrapper

## Summary
Extracted subagent initialization and management from `Session` into a new `SubagentBootstrapper` class. This removes the `_initialize_subagents()` method (~37 lines) and the `_subagent_executor` attribute from Session.

## Changes Made

### 1. New File: `dendrophis/session/subagents.py`
Created `SubagentBootstrapper` dataclass with:
- `initialize()`: Registers all subagent handlers (researcher, code-writer, test-runner, planner, code-reviewer, debugger)
- `invoke()`: Direct subagent invocation API (moved from Session.invoke_subagent)
- `executor` property: Access to the underlying SubagentExecutor

Dependencies:
- `_memory_store`: Required for ResearcherHandler registration

### 2. Modified: `dendrophis/session/session.py`

#### `__init__` signature
- **Added parameter:** `subagent_bootstrapper: SubagentBootstrapper | None = None`
- **Removed:** `self._subagent_executor: Any = None` attribute
- **Removed:** `self._initialize_subagents()` call
- **Added:** `self._subagent_bootstrapper = subagent_bootstrapper`

#### `invoke_subagent()` method
**Before:**
```python
async def invoke_subagent(self, agent, payload, context=None):
    if self._subagent_executor is None:
        return {"success": False, "error": "Subagent executor not initialized"}
    result = await self._subagent_executor.execute(...)
    # ... result handling
```

**After:**
```python
async def invoke_subagent(self, agent, payload, context=None):
    if self._subagent_bootstrapper is None:
        return {"success": False, "error": "Subagent bootstrapper not initialized"}
    return await self._subagent_bootstrapper.invoke(agent, payload, context)
```

#### Removed method
- `_initialize_subagents()` - entire method removed (~37 lines)
  - Handler imports moved to `subagents.py`
  - Registration logic moved to `SubagentBootstrapper.initialize()`

#### Imports
- Added: `from dendrophis.session.subagents import SubagentBootstrapper`

### 3. Modified: `dendrophis/session/factory/__init__.py`

#### Imports
- Added: `from dendrophis.session.subagents import SubagentBootstrapper`

#### Construction sequence
**Added after MemoryStore creation:**
```python
# 2b. Subagent Bootstrapper (needs memory_store)
subagent_bootstrapper = SubagentBootstrapper(memory_store=memory_store)
subagent_bootstrapper.initialize()
```

**Session constructor call:**
- Added parameter: `subagent_bootstrapper=subagent_bootstrapper`

## Architecture

```
Factory
  │
  ├── creates: SubagentBootstrapper(memory_store)
  │   └── calls: initialize() → registers all handlers globally
  │
  ├── passes: subagent_bootstrapper → Session
  │
  └── Session
      └── delegates: invoke_subagent() → subagent_bootstrapper.invoke()
```

## Global State Note
The subagent system uses global state via `set_session_executor()` / `get_session_executor()`. This is preserved:
- `SubagentBootstrapper.initialize()` calls `set_session_executor(self._executor)`
- Tools use `get_session_executor()` to access the executor
- This allows tools to invoke subagents without holding a Session reference

## Rollback Instructions

If issues occur:

1. **Revert Session.__init__:**
   - Remove `subagent_bootstrapper` parameter
   - Restore `self._subagent_executor: Any = None`
   - Restore `self._initialize_subagents()` call

2. **Restore _initialize_subagents method:**
   - Copy from git: `git show HEAD:dendrophis/session/session.py | grep -A 35 "def _initialize_subagents"`

3. **Restore invoke_subagent:**
   - Revert to use `self._subagent_executor` directly

4. **Revert Factory:**
   - Remove SubagentBootstrapper creation
   - Remove parameter from Session constructor

5. **Delete new file:**
   - `rm dendrophis/session/subagents.py`

## Testing Checklist

- [ ] Application starts without errors
- [ ] `invoke_subagent` tool works (e.g., ask AI to "research the codebase")
- [ ] All subagent handlers registered:
  - [ ] researcher
  - [ ] code-writer
  - [ ] test-runner
  - [ ] planner
  - [ ] code-reviewer
  - [ ] debugger
- [ ] No import errors in session.py or factory
- [ ] ruff check passes
- [ ] ruff format passes

## Metrics

| Metric | Before | After |
|--------|--------|-------|
| Session.__init__ parameters | 15 | 15 (swapped one) |
| Session methods | ~25 | ~24 (-1) |
| Session lines | ~565 | ~511 (-54) |
| New collaborator lines | 0 | 111 |
| Session responsibilities | 6 | 5 |

## Related
- Step 1: Extracted ChatOrchestrator (chat turn loop)
- Step 3 (pending): Move MemoryAssociationGenerator fully to Factory
