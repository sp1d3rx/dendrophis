"""Default config YAML template (written on first run)."""

from __future__ import annotations

DEFAULT_CONFIG_YAML = """\
llm:
  # --- Connection Settings ---
  # Base URL of the OpenAI-compatible API endpoint
  base_url: "https://api.deepinfra.com/v1/openai"
  # API key used for authentication (or set DENDROPHIS_API_KEY env var)
  api_key: ""
  # Maximum network request timeout in seconds
  timeout: 120.0

  # --- Model Selection ---
  # The primary LLM used for standard chat and agentic reasoning
  model: "meta-llama/Meta-Llama-3.1-70B-Instruct"
  # The model dedicated to the code-writer subagent for executing code changes (null = fallback to default)
  code_writer_model: null

  # --- Context & Compaction ---
  # Maximum context window limit in tokens
  context_limit: 128000
  # Compress history when token usage exceeds this fraction of the context limit (e.g., 0.85 = 85%)
  compaction_threshold: 0.85

  # --- Generation & Sampling ---
  # Maximum tokens the model is allowed to generate per response
  max_tokens: 4096
  # Sampling temperature (lower is more deterministic, higher is more creative)
  temperature: 0.2
  # Limit sampling to the top K most likely tokens (null = disabled)
  top_k: null
  # Nucleus sampling threshold (null = disabled)
  top_p: null
  # Reasoning depth for thinking models (e.g., low, medium, high, or none to disable)
  reasoning_effort: null
  # How the streaming parser starts ("text" for standard models, "thinking" for thinking models, null = auto)
  thinking_start_mode: null
  # How to preserve reasoning/thoughts in context: "always" (all turns), "current" (current turn only), or "never"
  preserve_reasoning: "always"
  # Mistral/Kimi prompt cache key (set to enable prompt caching for supported models)
  # Run `dendrophis --calibrate MODEL` to check if your model supports it
  # Requests with the same key and model share a KV cache, even if prompts differ slightly.
  prompt_cache_key: null

  # --- Tool Configuration ---
  # Format to send tools: "auto" (XML for local, native otherwise), "native" (OpenAI API), or "xml"
  tool_mode: "auto"

  # --- Visual VLM Features (Opt-in) ---
  # Render system prompt as a 1-bit binary image for supported VLM models
  visual_system_prompt: false
  # Render compacted past history as a 1-bit binary image for supported VLM models
  visual_compaction: false
  # Render large tool outputs (> visual_threshold_chars) as a 1-bit binary image for supported VLM models
  visual_tool_results: false
  # Render large user prompts/error logs (> visual_threshold_chars) as a 1-bit binary image for supported VLM models
  visual_user_prompts: false
  # Minimum character threshold before tool results or user prompts are converted to 1-bit images
  visual_threshold_chars: 1000

ui:
  theme: monokai
  colors:
    primary: "#3B82F6"
    secondary: "#8B5CF6"
    success: "#16A34A"
    warning: "#D97706"
    danger: "#DC2626"
    surface: "#FFFFFF"
    text: "#111827"
    neutral: "#FFFFFF"
  scrollback_limit: 100
  sidebar:
    position: right
    width: 28
    panels:
      - model
      - status
      - primer
      - tokens
      - speed
      - context
      - temperature
      - cache
      - cost
      - sysinfo
      - reasoning
      - mcp_status

hooks:
  pre_tool_use: []
  post_tool_use: []

tools:
  extra_paths: []
  max_calls: 3

permissions:
  # Tools that require user confirmation before running (empty = none)
  require_confirmation:
    - bash
    - delete_memory
  # Tools blocked entirely (empty = none blocked)
  denied_tools: []
  # Tools explicitly allowed (empty = all tools allowed)
  allowed_tools: []
  bash:
    # Categories blocked outright — never executed regardless of confirmation
    denied_categories:
      - system_destructive
    # Categories that skip the confirmation prompt (trusted read-only ops)
    auto_approve_categories:
      - filesystem_read
    # Categories explicitly allowed (empty = all non-denied categories are allowed)
    allowed_categories: []

caching:
  enabled: true
  # Tier 1: Always-cached (sent every request)
  tier1_system_prompt: true
  tier1_tool_definitions: true
  # Tier 2: Stable-content (cacheable after N turns)
  tier2_file_blocks: true
  tier2_file_blocks_stable_turns: 3
  tier2_project_understanding: true
  tier2_project_understanding_min_turns: 5
  # ──────────────────────────────────────────────────────────────────────
  # Primer (project memory) feature controls
  # ──────────────────────────────────────────────────────────────────────
  pr_enabled: true  # Enable/disable primer saving/loading entirely

memory_db: "~/.config/dendrophis/memory.db"
"""
