# Refactor: Agentic Workflow (Sandboxed Execution & Peer Review)

This refactor transforms the subagent execution model from a "text-plan" parsing system to a robust, tool-based, sandboxed loop with automated peer review.

## Goal
To make code modification resilient, autonomous, and high-quality by moving away from fragile regex-based parsing and toward a formal "Worker-Reviewer-Manager" architecture.

## Roadmap

### Phase 1: The Foundation (Tooling & Safety)
- [x] **Automatic Backup Tooling:** Implement a decorator or middleware in the tool execution layer that automatically creates `.bak` files before any `write_file` or `edit_function` call.
- [x] **Standardized Toolset for Agents:** Define and implement a clean, high-level toolset for the `CodeWriter` (e.g., `read_file`, `write_file`, `edit_function`, `list_dir`).
- [ ] **The Orchestrator Loop Update:** Refactor the main execution loop to handle "Tool Call" responses from agents instead of parsing raw text strings.

### Phase 2: The Worker (CodeWriter Refactor)
- [ ] **Transition CodeWriter to Tool-User:** Rewrite `CodeWriter` so it generates tool calls (JSON or function call format) rather than text-based edit blocks.
- [ ] **Self-Correction Loop:** Enable the `CodeWriter` to receive error messages from failed tool calls to perform immediate self-correction.

### Phase 3: The Auditor (CodeReviewer Agent)
- [ ] **Implement CodeReviewer Agent:** Create a new subagent with a high-strictness persona.
- [ ] **Reviewer Capabilities:** The `CodeReviewer` must be able to run `ruff`, `py_compile`, and potentially `pytest` on the proposed changes.
- [ ] **Feedback Loop:** Establish a protocol where `CodeReviewer` returns structured failure reports to the Orchestrator.

### Phase 4: The Manager (Orchestration & Recovery)
- [ ] **The Recovery Mechanism:** Implement the logic where the Orchestrator restores `.bak` files if the `CodeReviewer` rejects the changes.
- [ ] **Final Commit:** Implement the logic where the Orchestrator "cleans up" (deletes `.bak` files) once a change is officially approved.

## Progress Tracking
*All completed steps will be moved to `refactors/agentic_workflow/completed/` or noted in a `step_results.md` file.*
