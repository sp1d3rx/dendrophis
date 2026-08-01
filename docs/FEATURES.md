# Dendrophis Feature List

This document provides a comprehensive overview of all capabilities and features implemented in **Dendrophis**.

---

## 🖥️ Terminal User Interface (TUI)

- **Textual-Based Async Interface:** Reactive terminal user interface featuring smooth streaming, keyboard bindings, and modal dialogs.
- **Multi-Screen Workflow:**
  - `MainScreen`: Main conversation workspace with interactive message history.
  - `DebugLogScreen`: Live stream of system events, API logs, and diagnostic output (toggle with `Ctrl+Shift+D`).
  - `SettingsScreen`: Interactive configuration menu for LLM parameters, paths, and options.
  - `ModelSwitcherScreen`: Quick model switching dialog with model metadata filtering.
  - `MemoryViewerScreen`: Searchable inspector for persistent vector memories.
  - `SessionPickerScreen`: Interactive session loader and history manager.
  - `Confirmation Dialogs`: Dedicated approval screens for file writes (`WriteConfirmationScreen`), file edits (`EditConfirmationScreen`), Python code execution (`PythonExecConfirmationScreen`), and generic tool use (`ToolConfirmationScreen`).
- **Telemetry Sidebar:** 15+ configurable real-time telemetry panels:
  - `model`: Active model name, provider, and context limits.
  - `tokens`: Prompt, completion, and total token usage tracking.
  - `status`: Current session status and worker activity.
  - `speed`: Processing speed (tokens per second) and time-to-first-token.
  - `context`: Visual context window usage percentage.
  - `temp`: Current sampling temperature.
  - `cost`: Cumulative USD session cost estimation.
  - `sysinfo`: Host CPU, memory, and runtime metrics.
  - `mcp`: Model Context Protocol server connection states.
  - `cache`: Prompt cache hit rates and tier 1–3 cache states.
  - `reason`: Reasoning token telemetry for reasoning models.
  - `event`: Live event bus transaction counter.
  - `todo`: Task checklist progress indicator.
  - `memory_association`: Active vector memory linkages.
  - `primer`: Project understanding checkpoint status.

---

## ⚡ Core & Event System

- **Decoupled EventBus Architecture:** Asynchronous, thread-safe pub/sub event bus decoupling UI, LLM streaming, tools, subagents, and memory.
- **Priority Event Dispatching:** Priority-ordered handler queues executed via `ThreadPoolExecutor` (sync) or scheduled on the asyncio loop (async).
- **Session Lifecycle & Composition Root:** `Session` manages conversation orchestration, state persistence, turn execution, and error recovery.

---

## 🤖 LLM Client & Provider Integration

- **Multi-Provider Support:** OpenAI, Anthropic, OpenRouter, DeepInfra, and local servers (oMLX, MLC, LM Studio, Ollama).
- **Streaming & SSE Parsing:** Asynchronous Server-Sent Events (SSE) parser handling text deltas, reasoning deltas, and tool calls.
- **Dual Tool Calling Modes:**
  - **Native Mode:** Standard OpenAI JSON schema tool calling.
  - **XML Injected Mode:** Prompt-injected XML definitions for models lacking native tool call schemas.
- **Provider Incompatibility Sanitization:**
  - Message sanitizer stripping incompatible fields per provider (e.g., removing `tool_calls` for XML mode or `cache_control` for non-Anthropic).
  - DeepInfra tool call/result count alignment.
  - MLC false-positive retry mode handling `finish_reason=tool_calls` with reasoning-only output.
- **Reasoning & Caching Controls:** Support for `reasoning_effort`, `prompt_cache_key`, custom `stop` sequences, and penalty parameters.

---

## 🛠️ Tooling & Security Sandbox

- **Dynamic Tool Discovery:** Automatic registration and dependency injection for tool classes via `discover_tool_classes`.
- **Built-in Filesystem Tools:**
  - `glob`: Fast directory tree inspection with automatic exclusion rules (`.venv`, `node_modules`, `.git`).
  - `read`: File content reading with line offsets, slice bounds, and directory listing fallback.
  - `ripgrep`: Regex text search returning structured match results.
  - `edit`: Exact string replacement with unescaping helpers for LLM output errors.
  - `write`: File creation and overwrite protection.
  - `patch`: Multi-chunk file modification tool.
- **Code Execution & Analysis Tools:**
  - `bash`: Subprocess execution with category classification and timeout controls.
  - `python_exec`: Sandboxed Python script execution environment.
  - `function_analyzer`: AST function signature and docstring analysis tool.
- **Task & Session Management:**
  - `todo_manager`: Session task tracking and progress management.
  - `AskMultipleChoiceTool`: Interactive user prompt and questionnaire tool.
- **Model Context Protocol (MCP):** Dynamic integration of external MCP servers and tools via `MCPManager` and `MCPTool`.
- **Configurable Security Policy:** `PermissionPolicy` with `ALLOW`, `DENY`, and `CONFIRM` decisions backed by `BashSandbox` category analysis.

---

## 👥 Autonomous Subagent Framework

- **Background Agent Execution:** Parallel subagent task runner (`SubagentExecutor`) supporting isolated or inherited conversation contexts.
- **Pre-Packaged Specialist Subagents:**
  - `researcher`: Read-only codebase and external research specialist.
  - `planner`: Step-by-step task breakdown and strategy architect.
  - `code-writer`: Precise implementation and code generation agent.
  - `code-reviewer`: Quality assurance and static code review agent.
  - `test-runner`: Automated test execution and failure diagnostic agent.
  - `debugger`: Root-cause investigation and stack trace diagnostic agent.
- **Subagent Control Tools:** `invoke_subagent`, `define_subagent`, `send_message`, `manage_subagents`.

---

## 🧠 Memory & Context Optimization

- **Persistent Vector Memory (`MemoryStore`):** SQLite (WAL mode) database storing text entries, tags, and float32 embedding BLOBs for cosine similarity search.
- **Intelligent Context Compaction (`ContextManager`):** Token tracking and automatic conversation compaction when token usage reaches threshold.
- **Multi-Tier Prompt Caching:**
  - **Tier 1:** Always-cached static system prompts and tool definitions.
  - **Tier 2:** Stable file block caching via `FileBlockTracker`.
  - **Tier 3:** Project understanding checkpoints via `UnderstandingPhaseDetector`.

---

## ⚙️ Configuration & Extension

- **YAML Configuration (`ConfigLoader`):** Round-trip YAML configuration (`dendrophis.yaml`) with Pydantic v2 validation and environment variable overrides (`DENDROPHIS_API_KEY`, `BASE_URL`, `MODEL`).
- **Markdown Skill System (`SkillManager`):** Dynamic skill injection from `.md` files with YAML frontmatter metadata.
- **Session Persistence (`SessionPersister`):** JSON session state serialization with optional `xz` compression.
