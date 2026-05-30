# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

**Dendrophis** is a Python-native terminal coding agent built on Textual. It uses OpenAI-compatible APIs and features an event-driven architecture with async/await throughout. The codebase prioritizes speed, hackability, and clean Python patterns.

## Development Commands

### Running the Application
```bash
# Install and run (first time)
uv venv && uv pip install .

# Run from any directory
dendrophis

# Run with specific config or model
dendrophis --config path/to/config.yaml --model "model-name"

# Enable profiling (outputs to .profiling/)
DENDROPHIS_PROFILE=1 ./dendrophis.sh
```

### Linting and Formatting
```bash
# Check code style (ruff)
ruff check .

# Check formatting
ruff format --check .

# Auto-format code
ruff format .

# Lint configuration
# - Target: Python 3.13+
# - Line length: 120
# - Rules: E (errors), F (pyflakes), I (imports), UP (upgrades)
```

### Testing
```bash
# Run all tests
pytest

# Run specific test file
pytest path/to/test_file.py

# Run with verbose output
pytest -v

# Note: asyncio_mode is set to "auto" in pyproject.toml
```

## Architecture

Dendrophis uses a layered, event-driven architecture with clear separation of concerns:

### Core Layers

**CLI & Startup** (`dendrophis/cli.py`)
- Entry point with argument parsing
- ConfigLoader loads YAML configuration with environment variable overrides
- Creates DendrophisApp and runs the TUI

**DendrophisApp** (`dendrophis/ui/app.py`)
- Root Textual application
- Manages MainScreen and DebugLogScreen
- Sets up EventBus and Session on mount
- Handles API key prompts on first run

**Session** (`dendrophis/session/session.py`)
- Composition root tying together all subsystems
- Manages LLMClient, ContextManager, ToolExecutor
- Orchestrates the conversation flow:
  1. Takes user message
  2. Updates context
  3. Calls LLM with streaming
  4. Handles tool calls with optional confirmation
  5. Accumulates assistant response
  6. Publishes events throughout
- Runs within ThreadPoolExecutor for background processing

### Key Subsystems

**Context Manager** (`dendrophis/context/manager.py`)
- Maintains conversation message history
- Tracks token counts (via tiktoken)
- Triggers context compaction when approaching context_limit
- Handles file reading with size limits (MAX_FILE_BYTES = 200k)
- Converts files to markdown-fenced format for LLM consumption

**LLM Client** (`dendrophis/llm/client.py`)
- Async OpenAI-compatible HTTP client (httpx + httpx-sse)
- Streams responses with SSE
- Parses tool calls from response
- Fetches available models from /v1/models endpoint
- Handles model metadata and filters (e.g., vision models, reasoning models)

**Tool System** (`dendrophis/tools/`)
- **Registry**: Global registry of available tools with JSON schemas
- **Executor**: Runs tool calls, handles errors, emits events
- **Built-ins** (`tools/builtins/filesystem.py`): 
  - `read_file(path)`: Read file contents
  - `edit_file(path, old, new)`: Replace text (requires confirmation)
  - `write_file(path, content)`: Create/overwrite file (requires confirmation)

**Event Bus** (`dendrophis/events/`)
- Decoupled publish-subscribe system
- Events: TextDelta, ToolCall*, ToolResult, Done, Error, etc.
- Async event handlers with ThreadPoolExecutor (max 8 workers)
- Used by UI components, Session, and tool executor to communicate

**Config System** (`dendrophis/config/`)
- YAML parsing via ruamel.yaml (preserves formatting/comments)
- Search paths: `dendrophis.yaml` (local) → `~/.config/dendrophis/config.yaml`
- Environment overrides: `DENDROPHIS_API_KEY`, `DENDROPHIS_MODEL`, `DENDROPHIS_BASE_URL`
- Schema validation via Pydantic

**UI/TUI** (`dendrophis/ui/`)
- Textual-based terminal UI
- **MainScreen**: Main conversation view with sidebar
- **Sidebar**: Configurable panels (model, tokens, status, speed, context, temperature, cost, sysinfo)
- **Screens**: Settings, debug log, model switcher, API key prompt, tool confirmation
- Event-driven widget updates

## Configuration

The `dendrophis.yaml` config file controls runtime behavior:

```yaml
llm:
  base_url: "https://api.deepinfra.com/v1/openai"  # OpenAI-compatible endpoint
  api_key: ""                      # Leave blank to prompt on startup
  model: "model-name"              # Model to use
  max_tokens: 4096                 # Max completion tokens
  temperature: 0.2                 # Sampling temperature
  context_limit: 128000            # Model context window
  compaction_threshold: 0.85       # When to trigger context compaction (% of limit)
  timeout: 120.0                   # Request timeout in seconds

sidebar:
  position: right                  # left or right
  width: 28                        # Character width
  panels: [model, tokens, status, speed, context, temperature, cost, sysinfo]

hooks:
  pre_tool_use: []                # Shell commands to run before tool execution
  post_tool_use: []               # Shell commands to run after tool execution

tools:
  extra_paths: []                 # Paths to search for custom tools

system_prompt: |
  Custom system prompt for the LLM.
```

## Key Design Patterns

**Async Throughout**: All I/O (LLM calls, file ops, hooks) uses asyncio. Session runs in ThreadPoolExecutor to avoid blocking the UI.

**Event-Driven**: Components don't directly call each other; they publish events to the EventBus. UI subscribes to events for reactive updates.

**Context Compaction**: When token usage approaches the context_limit × compaction_threshold, the ContextManager summarizes older messages to stay within limits.

**Tool Confirmation**: Destructive tools (edit, write) can require user confirmation. Handled via ToolConfirmationRequestEvent/ToolConfirmationResponseEvent.

**Streaming**: LLM responses are streamed via SSE. TextDelta and ReasoningDelta events allow incremental UI updates.

## Important Details

- **Python 3.13+**: Uses modern Python features; target version is py313.
- **Textual Styling**: CSS-like styling in `dendrophis/ui/styles/dendrophis.tcss`.
- **Keyboard Bindings**: Ctrl+Q to quit, Ctrl+Shift+D to toggle debug log.
- **Debug Log**: Written to file (configurable path, default in ~/.config/dendrophis/) and displayed in debug screen.
- **Token Counting**: Uses tiktoken to estimate tokens; different models may have slightly different counts.
- **Model Filtering**: The LLM client filters out vision, reasoning, and non-text-generation models from the model list to show only chat models.

## Common Workflows

**Adding a Custom Tool**: Register it in `dendrophis/tools/builtins/` and import in `dendrophis/tools/__init__.py`. Tools use the `@registry.register()` decorator with a JSON schema.

**Modifying the Sidebar**: Edit config.yaml's `sidebar.panels` list and adjust `ui/widgets/sidebar.py` if adding new panel types.

**Changing the System Prompt**: Edit `config.yaml`'s `system_prompt` field or pass via environment (though direct config is preferred).

**Debugging**: Use Ctrl+Shift+D to open the debug log screen, or monitor `.profiling/` if running with profiling enabled.
