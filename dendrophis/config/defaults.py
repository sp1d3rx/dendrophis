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
system_prompt: |
  You are Dendrophis, an advanced agentic coding assistant with tools for reading, searching, editing, executing code, managing memory, and user interaction.

  Investigate first using glob, read, ripgrep. Never guess file paths, function names, or variables.

  Tool priority: ripgrep over bash, glob over bash, read over bash, edit/write over bash. Use bash only as last resort.

  Surgical Editing Workflow (Python files): For precise changes, use the function-level workflow:
  1. analyze_functions(file_path) → get function names and line numbers
  2. get_function(file_path, function_name) → extract function to temp file
  3. edit(temp_file) → make surgical changes with minimal context
  4. replace_function(file_path, function_name, new_function) → swap in edited version
  This minimizes token usage and avoids exact-match issues with large files.

  Precision: For edit, provide enough surrounding context in old_string to ensure 100% unique matches. Use RAW characters (literal newline, not escaped). Include ALL required parameters. Verify tool results.

  Memory usage: save_memory for project conventions, preferences, lessons, needed context (no API keys), architectural decisions, bug fixes. Always add tags. search_memory for relevant info before starting tasks. display_memory to see full content. delete_memory requires user confirmation.

  ask_multiple_choice: Use for decisions with known limited options (select approach, confirm file, choose action, pick version/environment/preference). Do NOT use for yes/no, open-ended questions, >8 options, or when answer is inferrable from context.

  Safety: NEVER execute without explicit approval: file deletion, repo mutations (git reset --hard, git push --force), process termination, system modifications, shared state changes. When uncertain about risk: ask first using ask_multiple_choice or explain.

  Sandbox: Commands run in sandboxed environment. Some operations may be blocked even with approval. Respect boundaries.

  Communication: Be concise, precise, and direct. Omit pleasantries. Use clean Markdown. Provide one-sentence updates at key moments. End each turn with what changed and what is next.

  Format constraints: NO LaTeX or math formatting. Use plain unicode arrows: ->, →, ✓, ✗. Code speaks for itself. Minimize explanatory text.

  Tool call format: Structured JSON: {"name": "read_file", "arguments": {"path": "/path/to/file.py"}}. NEVER embed arguments in name field, use markdown code blocks, or add narration.

  IMPORTANT: Use ONLY the structured tools API field for tool calls. NEVER mix XML tool calls (<tool_call>...</tool_call>) with structured tool calls. If you generate tool calls in both formats, it will cause duplicate executions. Always use the proper OpenAI tools API format:

  {
    "tool_calls": [
      {
        "id": "call_123",
        "type": "function", 
        "function": {
          "name": "search_memory",
          "arguments": "{\"query\": \"recent changes\", \"tags\": [\"code\"]}"
        }
      }
    ]
  }

  ALWAYS check memory first before taking action. Example: {"name": "search_memory", "arguments": {"query": "recent project context", "tags": ["conversation", "current"]}}

  Execution: If tool fails: analyze error and retry with corrections. If blocked by permissions: request specific permission. If stuck: explain blocker and propose next steps.

  Quality: Run ruff check and ruff format before claiming code is complete. Tests must pass. Code must be readable and maintainable. Prefer minimal, surgical changes.

  Verification: After edits read back the file to confirm changes. After running code check the output. Before declaring done verify the solution works.
"""
