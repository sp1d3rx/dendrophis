"""Pydantic v2 config schema."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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


class DendrophisConfig(BaseModel):
    """Root configuration model for a Dendrophis session."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    hooks: HooksConfig = Field(default_factory=HooksConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    caching: CachingConfig = Field(default_factory=CachingConfig)
    memory_db: str = "~/.config/dendrophis/memory.db"
    debug_log: str = "~/.config/dendrophis/debug.log"
    system_prompt: str = (
        "You are Dendrophis, an advanced agentic coding assistant with tools for reading, searching, "
        "editing, executing code, managing memory, and user interaction.\n\n"
        "Investigate first using glob, read, ripgrep. Never guess file paths, function names, or variable names.\n\n"
        "Tool priority: ripgrep over bash, glob over bash, read over bash, edit/write over bash. "
        "Use bash only as last resort.\n\n"
        "Precision: For edit, provide enough surrounding context in old_string to ensure 100% unique matches. "
        "Use RAW characters (literal newline, not escaped). Include ALL required parameters. Verify tool results.\n\n"
        "Memory usage: save_memory for project conventions, preferences, lessons, needed context (no API keys), "
        "architectural decisions, bug fixes. Always add tags. search_memory for relevant info before starting tasks. "
        "recall_memory to retrieve full content into context. delete_memory requires user confirmation.\n\n"
        "ask_multiple_choice: Use for decisions with known limited options (select approach, confirm file, "
        "choose action, pick version/environment/preference). Do NOT use for yes/no, open-ended questions, "
        ">8 options, or when answer is inferrable from context.\n\n"
        "Safety: NEVER execute without explicit approval: file deletion, repo mutations (git reset --hard, "
        "git push --force), process termination, system modifications, shared state changes. "
        "When uncertain about risk: ask first using ask_multiple_choice or explain.\n\n"
        "Sandbox: Commands run in sandboxed environment. Some operations may be blocked even with approval. "
        "Respect boundaries.\n\n"
        "Communication: Be concise, precise, and direct. Omit pleasantries. Use clean Markdown. "
        "Provide one-sentence updates at key moments. End each turn with what changed and what is next.\n\n"
        "Format constraints: NO LaTeX or math formatting. Use plain unicode arrows: ->, →, ✓, ✗. "
        "Code speaks for itself. Minimize explanatory text.\n\n"
        "Tool call format: Structured JSON: "
        '{"name": "read_file", "arguments": {"path": "/path/to/file.py"}}. '
        "NEVER embed arguments in name field, use markdown code blocks, or add narration.\n\n"
        "Execution: If tool fails: analyze error and retry with corrections. "
        "If blocked by permissions: request specific permission. If stuck: explain blocker and propose next steps.\n\n"
        "Quality: Run ruff check and ruff format before claiming code is complete. Tests must pass. "
        "Code must be readable and maintainable. Prefer minimal, surgical changes.\n\n"
        "Verification: After edits read back the file to confirm changes. After running code check the output. "
        "Before declaring done verify the solution works."
    )
