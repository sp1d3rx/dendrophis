# Token Caching Strategy for Dendrophis

## Overview

This document outlines a three-tier caching strategy for dendrophis to reduce token consumption across long coding sessions while maintaining conversation freshness and avoiding stale context issues.

## Tier 1: Always-Cached (Ephemeral)

**Sent with every request, never changes during session**

### System Prompt
- **When**: First message of session
- **Why**: Identical for all requests; defines agent behavior
- **Risk**: None—this is instructions, not context
- **Implementation**: Mark in context manager during initialization
  ```python
  message = {
      "role": "system",
      "content": config.system_prompt,
      "cache_control": {"type": "ephemeral"}
  }
  ```

### Tool Definitions
- **When**: Every request that uses tools
- **Why**: Function schemas never change; takes significant tokens
- **Risk**: None—tools are static during session
- **Implementation**: Mark tool definitions in payload
  ```python
  "tools": [
      {
          "type": "function",
          "function": {...},
          "cache_control": {"type": "ephemeral"}
      }
  ]
  ```
- **Savings**: Typical 200-500 tokens per request (for 5+ tools)

---

## Tier 2: Stable-Content (Cacheable After N Turns)

**Conversation content that becomes stable and is referenced repeatedly**

### File Content Blocks

When a user reads a file into context, it becomes stable until explicitly re-read.

**Strategy**:
1. Track file reads by path + hash (detect if file changed)
2. After a file has been in context for **N turns without the file changing** (suggest N=3), mark it as cacheable
3. If user re-reads same file, reuse cached version
4. If file changes (hash mismatch), invalidate cache for that file

**Implementation**:
```python
@dataclass
class FileContext:
    path: str
    content_hash: str
    turn_added: int
    turns_stable: int = 0
    cached: bool = False

def mark_file_cacheable(file_ctx):
    if file_ctx.turns_stable >= 3 and file_ctx.content_hash == current_hash(file_ctx.path):
        file_ctx.cached = True
        # Add cache_control to the message in context.messages
```

**Savings**: High. A 5KB file = ~1250 tokens; after caching, 0 tokens on subsequent requests.

**Risk**: Medium. If the file is read, edited, and the old version is still cached in context, the LLM sees stale content. Mitigation: invalidate on file hash change.

---

### Project/Codebase Understanding Block

Early in a session, users establish context: "Here's my project structure, here's the architecture."

**Strategy**:
1. Identify the **first 5-10 turns** that establish project understanding (typically until user asks first task)
2. After those turns, mark that block as a stable "understanding checkpoint"
3. Cache those messages together
4. All subsequent turns reference this cached understanding

**Implementation**:
```python
class UnderstandingPhase:
    def __init__(self):
        self.start_turn = 0
        self.established = False
        self.cached_turns = []
    
    def check_if_established(self, current_turn: int):
        # Heuristic: if 5+ turns and user asks first real task, mark established
        if current_turn >= 5 and last_message_is_task_request():
            self.established = True
            self.cached_turns = messages[self.start_turn:current_turn]
```

**Savings**: Medium. Saves re-sending 2-5K of codebase context on each request (500-1250 tokens).

**Risk**: Low. This is static project info that doesn't change. Invalidate only if user explicitly says "project structure changed."

---

### Completed Tool Execution Sequences

When a user asks to do something (read file → understand → make change → test), that sequence becomes a completed unit.

**Strategy**:
1. Identify patterns: user message → multiple tool calls → results → assistant summary
2. After **N turns have passed since completion** (suggest N=2), mark the sequence as cacheable
3. Useful when user does similar patterns repeatedly

**Implementation**:
```python
class ToolSequence:
    start_turn: int
    tool_calls: list[ToolCall]
    results: list[ToolResult]
    completion_turn: int
    cacheable_after_turn: int
    
def mark_sequence_cacheable(seq, current_turn):
    if current_turn >= seq.completion_turn + 2:
        seq.cacheable = True
```

**Savings**: Low-Medium. Saves 300-800 tokens if similar patterns repeat.

**Risk**: Medium. If a tool result becomes outdated (e.g., test passed, then test fails), cached result is stale. Mitigation: invalidate if user re-runs the tool or explicitly changes state.

---

## Tier 3: Checkpointing (On Context Compaction)

**When context approaches limit, compaction summarizes old messages**

### Compaction Checkpoint

When `ContextManager` detects token usage approaching `context_limit × compaction_threshold`:

1. **Current behavior**: Summarize oldest messages into a single "summary" message
2. **New behavior**: Mark the summary + everything before it (up to the checkpoint) as cacheable
3. This creates a **saved state** of understood context

**Implementation**:
```python
def compact_context(self):
    # Existing logic: create summary of messages[0:N]
    summary = self._summarize(messages[0:N])
    
    # New: mark summary block as cacheable
    summary_msg = {
        "role": "user",  # or "assistant"
        "content": summary,
        "cache_control": {"type": "ephemeral"},
        "_checkpoint_marker": True  # for debugging
    }
    
    # Replace old messages with summary
    self.messages = [summary_msg] + messages[N:]
```

**Savings**: High. On long sessions, saves re-sending all historical context (could be 5K-20K tokens).

**Risk**: Medium. Summaries can lose details. Mitigation: use high-quality summarization; keep the previous few messages unhidden for reference.

---

## Configuration

Add to `dendrophis.yaml`:

```yaml
caching:
  enabled: true
  
  # Tier 1: Always cached
  tier1:
    system_prompt: true
    tool_definitions: true
  
  # Tier 2: Stable content
  tier2:
    file_blocks:
      enabled: true
      stable_turns: 3        # Mark cacheable after N turns
      hash_validation: true  # Invalidate if file changes
    
    project_understanding:
      enabled: true
      establish_after_turns: 5  # Phase ends after N turns
      invalidate_on_user_signal: true
    
    tool_sequences:
      enabled: true
      cacheable_after_turns: 2
  
  # Tier 3: Checkpointing
  tier3:
    on_compaction: true
    keep_recent_unhidden: 3  # Don't cache last N messages
```

---

## Invalidation Rules

**When to clear caches:**

| Tier | Trigger | Action |
|------|---------|--------|
| Tier 1 | Never | (Immutable) |
| Tier 2 (files) | File hash changes | Invalidate file block |
| Tier 2 (understanding) | User says "project changed" | Clear checkpoint |
| Tier 2 (sequences) | User re-runs same tool | Clear that sequence |
| Tier 3 | Never (auto-refreshed) | Overwrites on next compaction |

**User-facing signal**: Add a `/refresh-cache` command or auto-detect via:
- User mentioning "changed X file"
- User explicitly editing a cached file
- Time-based: invalidate after 30 min if file not explicitly read

---

## Implementation Roadmap

### Phase 1 (MVP): Tier 1 Only
- Add cache_control to system prompt
- Add cache_control to tool definitions
- Config option to enable/disable
- **Effort**: 2-3 hours
- **Savings**: 200-500 tokens per request (~10-20% reduction)

### Phase 2: Tier 2 (Files + Understanding)
- Track file reads and hashes
- Implement "understanding phase" detection
- Invalidation logic
- **Effort**: 4-6 hours
- **Savings**: Additional 500-1500 tokens on established sessions

### Phase 3: Tier 2 (Sequences)
- Identify tool execution patterns
- Mark completed sequences
- **Effort**: 2-3 hours
- **Savings**: Additional 300-800 tokens for repetitive tasks

### Phase 4: Tier 3 (Checkpointing)
- Integrate with existing compaction logic
- Mark compaction checkpoint as cacheable
- **Effort**: 2 hours
- **Savings**: High on very long sessions (5K+ tokens)

---

## Monitoring & Metrics

Add events to track cache effectiveness:

```python
@dataclass
class CacheHitEvent:
    tier: int
    item_type: str  # "system_prompt", "file", "understanding", etc.
    tokens_saved: int
    timestamp: float

@dataclass  
class CacheInvalidationEvent:
    tier: int
    item_type: str
    reason: str  # "file_changed", "user_signal", "time_expired"
```

Add sidebar panel: "Cache: 2.5K tokens saved (12% reduction)"

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Stale file content | Hash-based invalidation; explicit user signals |
| Outdated understanding | Invalidate on project changes; summaries include dates |
| Lost details in summaries | Keep N previous messages unhidden; high-quality summarization |
| Cache misses from provider | Graceful fallback if provider doesn't support; re-send uncached |
| Confusing UX (why is this cached?) | Debug log shows cache status; optional verbose mode |

---

## Example Session Flow

```
Turn 1: User sends system prompt + project overview
  → Mark project understanding block as cacheable after turn 5

Turn 2-4: User asks clarifying questions, reads files
  → Files marked cacheable after turn 3+stable

Turn 5: User asks first task ("add feature X")
  → Project understanding checkpoint complete; mark as cached

Turn 6-8: User iterates (edit → test → fix)
  → Tool sequences tracked; mark cacheable after turn 8+2

Turn 9: User asks different task
  → Cached project understanding reused; saves 500+ tokens

Turn 20: Context approaching limit
  → Compaction triggered; create checkpoint cache of summary

Turn 21+: All requests reuse checkpoint cache; saves 5K+ tokens
```

**Total savings on 20-turn session**: ~8K-12K tokens (~15-25% reduction)

