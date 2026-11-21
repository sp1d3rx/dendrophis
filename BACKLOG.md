# Dendrophis Backlog

Features removed from active configuration schemas until their runtime execution paths are implemented.

## Planned Features

### 1. Pre/Post Tool-Use Shell Hooks (`hooks.pre_tool_use`, `hooks.post_tool_use`)
- Run user-configured shell commands before and after tool execution in `SessionToolExecutor`.
- Support optional tool-name matchers (exact name or regex/glob) per hook entry.
- Define clear failure/abort semantics, environment variable passing (tool name, arguments, exit status), and timeout bounds.

### 2. External Custom Tool Discovery (`tools.extra_paths`)
- Dynamically discover and instantiate `BaseTool` subclasses from user-configured filesystem directories in `create_builtin_registry()`.
- Validate module boundaries, dependency injection signatures, and permission defaults (`require_confirmation` for external tools by default).
