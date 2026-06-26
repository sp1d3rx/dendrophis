# Refactoring Plan: Extract ChatOrchestrator from Session

## Goal
Extract the core chat loop and turn-based logic from `Session` into a new, specialized `ChatOrchestrator` class to reduce the complexity of `Session` and adhere to the Single Responsibility Principle.

## 🛠️ Extraction Mapping

### 1. The Core Loop
**Move to:** `ChatOrchestrator._run_completion_loop`
*   **Source:** `Session._run_completion_loop` (Lines 657–833)
*   **Logic:** The `while` loop managing the LLM stream, handling `TurnResult`, executing tool calls, and managing consecutive failures.

### 2. The Orchestration Logic
**Move to:** `ChatOrchestrator.send_message`
*   **Source:** `Session.send_message` (Lines 509–655)
*   **Logic:**
    *   **Pre-turn:** Understanding phase detection, Memory association, Context compaction, and starting the stats task.
    *   **The Call:** Awaiting the completion loop.
    *   **Post-turn:** File cache updates, Stats finalization, and emitting `StreamingFinishedEvent`.
    *   *Note: Slash command handling may stay in `Session` or move to a new `CommandRegistry`.*

### 3. Supporting State & Helpers
**Move to:** `ChatOrchestrator`
*   **Adaptive Buffering:** `Session._flush_buffers` (Lines 260–268) and the buffer variables (`_text_buffer`, `_reasoning_buffer`, `_last_flush_time`).
*   **Streaming Control:** `Session.cancel_streaming` (Lines 447–449) and `Session.is_streaming` (Lines 451–453).
*   **Stats Emission:** `Session._emit_stats_periodically` (Lines 490–503).

## 🏗️ Proposed Architecture

### `ChatOrchestrator`
A specialized actor that manages the transient state of a single chat turn.
**Dependencies (via Injection):**
*   `LLMClient`
*   `ContextManager`
*   `EventBus`
*   `SessionStats`
*   `SessionToolExecutor`
*   `DendrophisConfig`
*   `UnderstandingPhaseDetector`
*   `MemoryStore`
*   `session_id` (for logging)

### `Session` (The Refactored Version)
A thin "composition root" container. It will hold the long-lived components and delegate the chat lifecycle to the `ChatOrchestrator`.

## 🚀 Execution Steps
1.  Create `dendrophis/session/orchestrator.py`.
2.  Implement `ChatOrchestrator` with the extracted logic.
3.  Refactor `Session` to use the new orchestrator.
4.  Verify functionality and run tests.
