# Step 3 Refactor: Cleanup MemoryAssociationGenerator

## Summary
Cleaned up `MemoryAssociationGenerator` integration in `ChatOrchestrator`. Moved import to top-level, removed development debug logging, and improved type annotation.

## Changes Made

### Modified: `dendrophis/session/chat.py`

#### Imports
**Before:**
```python
from dendrophis.caching.understanding import UnderstandingPhaseDetector
from dendrophis.config.schema import DendrophisConfig
from dendrophis.context.manager import ContextManager
from dendrophis.events import (
```

**After:**
```python
from dendrophis.caching.understanding import UnderstandingPhaseDetector
from dendrophis.config.schema import DendrophisConfig
from dendrophis.context.manager import ContextManager
from dendrophis.events import (
    ...
)
from dendrophis.llm.client import LLMClient, ModelInfo
from dendrophis.llm.models import supports_caching_by_id, supports_tools_by_id
from dendrophis.memory.association import MemoryAssociationGenerator  # NEW
from dendrophis.session.tools import is_tool_error, tool_call_to_payload
```

#### Type annotation
**Before:**
```python
association_generator: Any | None = None  # MemoryAssociationGenerator
```

**After:**
```python
association_generator: MemoryAssociationGenerator | None = None
```

#### send_message() method - memory association block
**Before:**
```python
# Generate memory association - "this makes me think of..."
self._log(f"[ASSOC-DEBUG] Generator exists: {self.association_generator is not None}")
if self.association_generator is not None:
    try:
        self._log("[ASSOC-DEBUG] Calling on_turn...")
        association = self.association_generator.on_turn(text)
        self._log(f"[ASSOC-DEBUG] Result: {association is not None}")
        if association is not None:
            from dendrophis.memory.association import MemoryAssociationGenerator

            assoc_text = MemoryAssociationGenerator.format_association(association)
            self._log(f"[ASSOC-DEBUG] Injecting: {assoc_text[:80]}...")
            self.context.append_system(f"[Memory: {assoc_text}]")
            self._emit(association)
    except Exception as association_error:
        self._log(f"[ASSOC-DEBUG] Exception: {association_error}")
```

**After:**
```python
# Generate memory association - "this makes me think of..."
if self.association_generator is not None:
    try:
        association = self.association_generator.on_turn(text)
        if association is not None:
            assoc_text = MemoryAssociationGenerator.format_association(association)
            self.context.append_system(f"[Memory: {assoc_text}]")
            self._emit(association)
    except Exception as association_error:
        self._log(f"Memory association failed: {association_error}")
```

## Improvements

1. **Import at top-level**: No more lazy import inside the method
2. **Proper type annotation**: `MemoryAssociationGenerator | None` instead of `Any | None`
3. **Removed debug logging**: `[ASSOC-DEBUG]` lines were development artifacts
4. **Cleaner error message**: "Memory association failed" instead of "[ASSOC-DEBUG] Exception"
5. **~6 lines removed**: Simpler, more maintainable code

## Architecture

```
Factory
  │
  ├── creates: MemoryAssociationGenerator(memory_store)
  │
  ├── passes: association_generator → ChatOrchestrator
  │
  └── ChatOrchestrator
      └── uses: association_generator.on_turn(text) in send_message()
```

## Rollback Instructions

If issues occur:

1. **Revert imports:** Remove `from dendrophis.memory.association import MemoryAssociationGenerator`

2. **Revert type annotation:** Change back to `association_generator: Any | None = None`

3. **Revert send_message block:** Restore the `[ASSOC-DEBUG]` logging and lazy import

## Testing Checklist

- [ ] Application starts without errors
- [ ] Memory associations still work (if enabled in config)
- [ ] No import errors in chat.py
- [ ] ruff check passes
- [ ] ruff format passes

## Metrics

| Metric | Before | After |
|--------|--------|-------|
| ChatOrchestrator lines | ~504 | ~499 (-5) |
| Debug log lines | 5 | 0 |
| Lazy imports | 1 | 0 |
| Type safety | Any | MemoryAssociationGenerator \| None |

## Related
- Step 1: Extracted ChatOrchestrator
- Step 2: Extracted SubagentBootstrapper
- All 3 steps complete - Session decomposition finished
