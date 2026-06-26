# Problem Report: Tool Output Not Displaying in Chat UI

## Summary
Despite successful tool execution and the implementation of `ToolResultEvent` to carry tool output, the actual content (`stdout`/`stderr`) of tool calls is not appearing in the user's chat window. The LLM can see the output in its internal tool-call blocks, but the UI fails to render the results to the user.

## Current State
- **Tool Execution**: Working correctly. Commands like `ls -la` execute and return results to the agent.
- **Smart Output Logic**: The `bash` tool has been enhanced with intelligent truncation, exit code reporting, and expansion capabilities.
- **Event Emission**: `dendrophis/session/tools.py` has been updated to emit both `ToolExecutionFinishedEvent` (for lifecycle) and `ToolResultEvent` (for the actual output content).
- **Observation**: The user reports that "nothing" appears in the chat window regarding tool outputs, even for simple commands like `ls`.

## Technical Analysis
The issue appears to be in the event-to-UI dispatch pipeline. 

### Findings:
1. **Event Definition**: `ToolResultEvent` is correctly defined in `dendrophis/events/types.py` and contains the `content` field.
2. **Event Emission**: In `dendrophis/session/tools.py`, the following logic is implemented in `_execute_single_tool_returning`:
   ```python
   # Emit tool execution finished
   success = "error" not in result.content.lower()
   self._emit(ToolExecutionFinishedEvent(tool_name=tc.name, success=success))
   # Also emit the actual result content so the UI can display it
   self._emit(ToolResultEvent(tool_call_id=tc.id, name=tc.name, content=result.content))
   ```
3. **Potential Failure Points**:
   - **Event Subscription**: The `SessionEventHandler` in `dendrophis/session/events.py` subscribes to many events (e.g., `SendMessageRequest`, `ToolConfirmationResponseEvent`), but it does **not** appear to have a subscription or handler for `ToolResultEvent`.
   - **Client Dispatch**: If the backend does not explicitly handle/forward `ToolResultEvent` via the event bus to the client-side listener, the UI will never receive the data.
   - **Frontend Listener**: The frontend may not be listening for `ToolResultEvent` or doesn't know how to render it.

## Recommended Next Steps
1. **Verify Event Handling**: Check `dendrophis/session/events.py` and the `SessionEventHandler` class to see if `ToolResultEvent` is being subscribed to.
2. **Trace Event Flow**: Trace the path of a `ToolResultEvent` from emission in `SessionToolExecutor` $\rightarrow$ `EventBus` $\rightarrow$ `SessionEventHandler` $\rightarrow$ `LLMClient`/`UI`.
3. **Check Frontend/Bridge**: Investigate the bridge between the Python backend and the UI to ensure all event types in the `ToolExecutionEvent` hierarchy are being serialized and transmitted.
