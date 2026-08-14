"""Pydantic v2 config schema."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class LLMConfig(BaseModel):
    """LLM provider connection and generation settings."""

    base_url: str = "https://api.deepinfra.com/v1/openai"
    api_key: str = ""
    model: str = "meta-llama/Meta-Llama-3.1-70B-Instruct"
    # Override model specifically for code-writer subagent
    code_writer_model: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.2
    # Filter to top K tokens (None = disabled)
    top_k: int | None = None
    # Filter out tokens with probability < min_p * probability of top token
    min_p: float | None = None
    # Discourages repeating tokens (1.0 = neutral, >1.0 = penalize)
    repetition_penalty: float | None = None
    # Discourages repeating the same topic
    presence_penalty: float = 0.0
    # Discourages repeating exact tokens
    frequency_penalty: float = 0.0
    context_limit: int = 128_000
    compaction_threshold: float = 0.85
    timeout: float = 120.0
    # Custom stop sequences for the model
    stop: list[str] | None = None
    # Controls reasoning depth for models that support it (e.g. gemma-4, gemini-2.5, deepseek-r1).
    # None = don't send the param (use model default). "none" = disable reasoning entirely.
    reasoning_effort: str | None = None
    # Mistral/Kimi prompt cache key for caching prompts across requests.
    # None = don't send the param. Set to a string key to enable prompt caching.
    # Requests with the same key and model share a KV cache, even if prompts differ slightly.
    # Recommended format: session-scoped like "user123-chat456" or "dendrophis-{session_id}"
    prompt_cache_key: str | None = None
    # How to send tool definitions to the provider:
    #   "auto"   — xml for local (127.0.0.1/localhost), native OpenAI API otherwise
    #   "native" — always use OpenAI tools API (e.g. LMStudio supports this)
    #   "xml"    — always inject tool defs as XML into the system prompt (MLC-style)
    tool_mode: Literal["auto", "native", "xml"] = "auto"
    # For OpenRouter: force use of /chat/completions instead of /responses API
    # Useful when Responses API doesn't work well with certain models
    use_responses_api: bool | None = None
    # Start mode for the streaming parser:
    #   "text"     — start parsing as text
    #   "thinking" — start parsing as thinking/reasoning
    #   None       — auto-detect based on model name
    thinking_start_mode: Literal["text", "thinking"] | None = None
    # How to preserve reasoning/thoughts (thinking blocks) in the conversation history
    # sent back to the LLM:
    #   "always"  — always preserve reasoning for all turns
    #   "current" — preserve reasoning only for the current active turn
    #   "never"   — never preserve reasoning in the context (e.g. for Gemini)
    preserve_reasoning: Literal["always", "current", "never"] = "always"
    # --- Visual VLM Features (Opt-in) ---
    # Render system prompt as a 1-bit binary image for supported VLM models
    visual_system_prompt: bool = False
    # Render compacted past history as a 1-bit binary image for supported VLM models
    visual_compaction: bool = False
    # Render large tool outputs (> visual_threshold_chars) as a 1-bit binary image for supported VLM models
    visual_tool_results: bool = False
    # Render large user prompts/error logs (> visual_threshold_chars) as a 1-bit binary image for supported VLM models
    visual_user_prompts: bool = False
    # Minimum character threshold before tool results or user prompts are converted to 1-bit images
    visual_threshold_chars: int = 1000
    # When the model stops with text but no tool call and the text signals it intended to act
    # ("let me read...", "I'll check..."), append a short nudge and continue the turn instead of
    # silently ending. Recovers from models that narrate intent but forget to call a tool.
    continuation_nudge: bool = True

    @model_validator(mode="before")
    @classmethod
    def validate_preserve_reasoning(cls, values: Any) -> Any:
        if isinstance(values, dict):
            val = values.get("preserve_reasoning")
            if val is True:
                values["preserve_reasoning"] = "always"
            elif val is False:
                values["preserve_reasoning"] = "never"
        return values


class HookEntry(BaseModel):
    """Single hook definition with an optional tool-name matcher."""

    matcher: str = ""
    command: str


class HooksConfig(BaseModel):
    """Pre/post tool-use hook lists."""

    pre_tool_use: list[HookEntry] = Field(default_factory=list)
    post_tool_use: list[HookEntry] = Field(default_factory=list)


class SidebarConfig(BaseModel):
    """Sidebar layout and panel selection."""

    position: Literal["left", "right"] = "right"
    width: int = 28
    panels: list[str] = Field(default_factory=list)


class ToolsConfig(BaseModel):
    """Tool execution limits."""

    extra_paths: list[str] = Field(default_factory=list)
    max_calls: int = 3
    parallel_tools: bool = Field(default=True, description="Allow parallel tool execution")


class BashPermissions(BaseModel):
    """Category-level allow/deny policy for bash commands."""

    # Empty allowed_categories means all categories are permitted (unless denied).
    allowed_categories: list[str] = Field(default_factory=list)
    denied_categories: list[str] = Field(default_factory=lambda: ["system_destructive"])
    # Commands whose effects fall entirely within auto_approve_categories skip confirmation.
    auto_approve_categories: list[str] = Field(default_factory=lambda: ["filesystem_read"])


class PermissionsConfig(BaseModel):
    """Tool-level and bash-category permission rules."""

    # Empty allowed_tools means all tools are permitted (unless denied).
    allowed_tools: list[str] = Field(default_factory=list)
    denied_tools: list[str] = Field(default_factory=list)
    require_confirmation: list[str] = Field(default_factory=lambda: ["bash", "delete_memory"])
    bash: BashPermissions = Field(default_factory=BashPermissions)


class CachingConfig(BaseModel):
    """Token caching configuration for prompt cache optimization."""

    enabled: bool = True

    # Tier 1: Always-cached (sent every request)
    tier1_system_prompt: bool = True
    tier1_tool_definitions: bool = True

    # Tier 2: Stable-content (cacheable after N turns)
    tier2_file_blocks: bool = True
    tier2_file_blocks_stable_turns: int = 3  # Mark cacheable after N turns
    tier2_project_understanding: bool = True
    tier2_project_understanding_min_turns: int = 5  # Establish after N turns

    # Tier 3: Checkpointing (On context compaction)
    tier3_on_compaction: bool = True

    # ──────────────────────────────────────────────────────────────────────
    # Primer (project memory) feature controls
    # ──────────────────────────────────────────────────────────────────────
    pr_enabled: bool = True  # Enable/disable primer saving/loading entirely


class UIColors(BaseModel):
    """Custom color palette for the UI."""

    primary: str = "#3B82F6"
    secondary: str = "#8B5CF6"
    success: str = "#16A34A"
    warning: str = "#D97706"
    danger: str = "#DC2626"
    surface: str = "#FFFFFF"
    text: str = "#111827"
    neutral: str = "#FFFFFF"


class UIConfig(BaseModel):
    """Configuration for the Textual TUI."""

    theme: str = "monokai"
    colors: UIColors = Field(default_factory=UIColors)
    sidebar: SidebarConfig = Field(default_factory=SidebarConfig)
    scrollback_limit: int = 100


class MCPServerConfig(BaseModel):
    """Configuration for an individual MCP server."""

    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    enabled: bool = True
    url: str | None = None

    @model_validator(mode="after")
    def validate_command_or_url(self) -> MCPServerConfig:
        if not self.command and not self.url:
            raise ValueError("Either command or url must be specified for MCP server config.")
        return self


class DendrophisConfig(BaseModel):
    """Root configuration model for a Dendrophis session."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)
    ui: UIConfig = Field(default_factory=UIConfig)
    hooks: HooksConfig = Field(default_factory=HooksConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    caching: CachingConfig = Field(default_factory=CachingConfig)
    memory_db: str = "~/.config/dendrophis/memory.db"
    debug_log: str = "~/.config/dendrophis/debug.log"
    system_prompt: str = (
        "You are Dendrophis, an agentic coding assistant.\n\n"
        "Investigate first using search and read tools (ripgrep, glob, read). Never guess file paths or symbol names.\n"
        "File editing: Prefer edit/patch for surgical modifications, write for new files, and append for additions.\n"
        "Code execution: Use execute_code for Python and bash for system commands.\n"
        "Subagents: Use invoke_subagent for isolated subtasks and parallel exploration.\n"
        "Memory: Use search_memory, recall_memory, and save_memory for persistent context.\n"
        "Communication: Be concise, precise, and direct. "
        "Use clean Markdown (no LaTeX math formatting; use unicode arrows like -> or →).\n"
        "Safety: Require explicit approval before running destructive operations or repository mutations.\n"
        "Quality: Ensure tests pass and code is formatted with ruff before completing tasks."
    )
