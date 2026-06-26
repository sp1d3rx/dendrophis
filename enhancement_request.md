# Enhancement Request: Tool Call Result Handling Improvements

## Background

The current tool execution flow works correctly but has several areas where the model experience could be improved. These changes would reduce token usage, improve error recovery, and make the agent more robust.

## Current Flow

```
User Message → LLM → ToolCall(s) → Execute → Plain Text Result → Append as tool message → Next Turn
```

## Proposed Changes

### 1. Structured Tool Results (JSON Format)

**Problem:** Tools like `glob`, `ripgrep`, `read` return structured data, but we serialize it to plain text strings. The model must parse text output to understand results.

**Example:**
```python
# Current — model parses text
glob_result = "dendrophis/tools/registry.py\ndendrophis/tools/executor.py"

# Better — model gets structured data
glob_result = '{"files": ["dendrophis/tools/registry.py", "dendrophis/tools/executor.py"], "count": 2}'
```

**Implementation:**
- Modify `ToolResult` dataclass in `dendrophis/tools/models.py` to include a `format` field:
  ```python
  @dataclass(frozen=True)
  class ToolResult:
      content: str
      toolCallId: str
      metadata: dict[str, Any] = field(default_factory=dict)
      format: str = "text"  # "text" | "json" | "error"
  ```
- Update tool implementations to set `format="json"` when returning structured data
- Update `make_tool_result_message()` to include format hint in the message

**Files to change:**
- `dendrophis/tools/models.py` — add format field
- `dendrophis/tools/builtins/filesystem.py` — glob, ripgrep return JSON
- `dendrophis/tools/builtins/filesystem.py` — read returns text with metadata
- `dendrophis/llm/models.py` — `make_tool_result_message()` includes format

---

### 2. Error vs Success Distinction

**Problem:** When a tool fails (file not found, permission denied, invalid arguments), the error text looks identical to a successful result. The model cannot distinguish "tool executed successfully and returned empty list" from "tool crashed with exception."

**Example:**
```python
# Current — ambiguous
content = "Error: File not found: dendrophis/nonexistent.py"

# Better — explicit error flag
content = '{"error": "File not found", "path": "dendrophis/nonexistent.py"}'
# With is_error=True, the model knows this is a failure
```

**Implementation:**
- Add `is_error: bool` to `ToolResult`
- Update `ToolExecutor.execute()` to catch exceptions and set `is_error=True`
- Update `make_tool_result_message()` to include error indicator:
  ```python
  def make_tool_result_message(tool_call_id: str, name: str, content: Any, is_error: bool = False) -> dict[str, Any]:
      msg = {
          "role": "tool",
          "tool_call_id": tool_call_id,
          "name": name,
          "content": content if isinstance(content, str) else json.dumps(content),
      }
      if is_error:
          msg["is_error"] = True
      return msg
  ```

**Files to change:**
- `dendrophis/tools/models.py` — add is_error field
- `dendrophis/tools/executor.py` — catch exceptions, set is_error
- `dendrophis/llm/models.py` — update make_tool_result_message
- `dendrophis/subagents/handlers/code_writer.py` — pass is_error through

---

### 3. Context Growth Management

**Problem:** Each tool call adds 2 messages to context (assistant tool_call + tool result). With MAX_TURNS=10 in code-writer, that's 20 extra messages per invocation. Context grows quickly with nested subagents.

**Current:**
```
Turn 1: user → assistant(tool_call) → tool(result)
Turn 2: assistant(tool_call) → tool(result)
Turn 3: assistant(tool_call) → tool(result)
... (20 messages for 10 turns)
```

**Proposed:** Summarize or compress tool results when context approaches limit.

**Implementation:**
- After N tool turns, compress previous tool results into a single summary message
- Or: keep only the last M tool result messages, summarize older ones
- Add to `ContextManager` or create a `ToolResultCompressor`

**Files to change:**
- `dendrophis/context/manager.py` — add compression logic
- `dendrophis/config/schema.py` — add `tool_result_compression_threshold` setting

---

### 4. Argument Validation

**Problem:** Invalid tool call arguments (missing required fields, wrong types) are only caught at execution time. The model wastes a turn on malformed calls.

**Example:**
```python
# Model sends:
{"name": "read", "arguments": "{\"file_path\": \"test.py\"}"}  # missing "why" field

# Current — execute fails, model gets error, tries again
# Better — validate before execute, return immediate error without executing
```

**Implementation:**
- Add JSON Schema validation in `ToolExecutor.execute()` before calling the tool function
- Use the tool's schema definition to validate arguments
- Return a `ToolResult` with `is_error=True` immediately if validation fails

**Files to change:**
- `dendrophis/tools/executor.py` — add validate_arguments() method
- `dendrophis/tools/models.py` — may need schema access on ToolCall

---

### 5. Parallel Tool Execution

**Problem:** Code-writer executes tool calls sequentially in a `for` loop. Independent calls (e.g., `glob` + `ripgrep`) could run in parallel.

**Current (code_writer.py):**
```python
for tool_call in result.tool_calls:
    tool_result = await executor.execute(tool_call)  # sequential
```

**Proposed:**
```python
# Execute independent tools in parallel
tool_results = await asyncio.gather(
    *[executor.execute(tc) for tc in result.tool_calls]
)
```

**Considerations:**
- Some tools have side effects (edit, write, bash) — these must remain sequential
- Read-only tools (read, glob, ripgrep) can be parallelized
- Add dependency tracking or categorization to tools

**Files to change:**
- `dendrophis/tools/registry.py` — add `is_read_only` or `side_effects` flag to tool schema
- `dendrophis/tools/executor.py` — add `execute_parallel()` method
- `dendrophis/subagents/handlers/code_writer.py` — use parallel execution for read-only tools

---

## Priority

1. **Error vs Success** (High) — Immediate improvement to error recovery
2. **Structured Results** (High) — Reduces model parsing burden
3. **Argument Validation** (Medium) — Saves turns on malformed calls
4. **Parallel Execution** (Medium) — Performance improvement
5. **Context Growth** (Low) — Only needed for very long sessions

## Acceptance Criteria

- [ ] Tool results include format indicator (text/json/error)
- [ ] Failed tool calls are clearly distinguishable from successful ones
- [ ] Invalid tool arguments are rejected before execution with clear error messages
- [ ] Read-only tools execute in parallel when multiple are requested in one turn
- [ ] Context compaction handles tool result bloat gracefully
