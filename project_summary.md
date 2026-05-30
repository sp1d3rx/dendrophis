Dendrophis is Python-native terminal coding agent. Architecture event-driven. EventBus decouples UI, session, tools, memory.

Entry: `__main__.py` calls `cli.main()`. CLI parses args (`--config`, `--model`, `--session`, `--calibrate`, `--list-models`). `main()` builds `ConfigLoader`, creates `DendrophisApp`, runs it. On exit saves session ID.

`DendrophisApp` is Textual root. Initializes `EventBus`, calls `SessionFactory.create_session()`, pushes `MainScreen`. Fetches models in background worker. Handles auth failures with `ApiKeyPromptScreen`.

`Session` is composition root. Owns `ContextManager`, `LLMClient`, `SessionStats`, `SkillManager`, `ToolRegistry`, `SessionToolExecutor`, `SessionPersister`, `PrimerManager`. Orchestrates `send_message()` loop: append user message, check compaction, stream LLM response, execute tools, repeat until no `tool_calls` or max consecutive failures reached.

`EventBus` thread-safe. Uses `RLock` for subscribers. Priority-ordered handlers via `bisect.insort` and `heapq.merge`. Sync handlers run in `ThreadPoolExecutor`. Async handlers scheduled on event loop. Global singleton via `get_event_bus()`.

`LLMClient` streams OpenAI-compatible chat over `httpx.AsyncClient`. `_ProviderContext` computes flags per request: `is_local`, `is_direct_anthropic`, `is_openrouter`, `is_deepinfra`, `use_responses_api`, `use_xml_tools`, `sse_start_mode`. `_sanitize_messages` strips provider-incompatible fields: `tool_calls` for XML mode, `cache_control` for non-Anthropic. DeepInfra validates tool-call and tool-result pairing counts. `_build_payload` constructs request for standard `/chat/completions` or OpenRouter `/responses`. Supports `reasoning_effort`, `prompt_cache_key`, `stop` sequences, `presence_penalty`, `frequency_penalty`. XML tool mode injects tool definitions into system message as text. Native mode sends OpenAI `tools` schema. Stream parsing via `parse_sse_event`. Retry with exponential backoff on HTTP 429, 503, timeouts, connection errors. MLC false-positive detection: `finish_reason=tool_calls` with only reasoning emitted triggers retry with `tool_choice=none`.

`ContextManager` owns message list, token count, turn count. System prompt is first message. Methods: `append_user`, `append_assistant`, `append_tool_result`, `append_file`. File content wrapped in markdown fence with path header. `MAX_FILE_BYTES` 200k. `sync_token_count` from API usage response. `needs_compaction()` fires when `token_pct >= compaction_threshold`. `FileBlockTracker` marks stable files cacheable after N turns. `UnderstandingPhaseDetector` establishes checkpoint after minimum turns. `update_understanding_cache` adds `cache_control` to checkpoint message.

Tools: `BaseTool` abstract class defines `name`, `description`, `parameters`, `execute()`, `schema` property. `ToolRegistry` holds instances. `all()` and `all_schema()` return preferred order: glob, ripgrep, read, edit, write, bash. `ToolExecutor` runs single tool call with JSON argument parsing and error wrapping. `SessionToolExecutor` handles batch execution with permission policy. Confirmation flow: policy evaluates each tool call. Decision `DENY` returns error. Decision `ALLOW` executes immediately. Decision `CONFIRM` emits `ToolConfirmationRequestEvent`, polls for `ToolConfirmationResponseEvent`. Bash commands simulated via `BashSandbox` for category classification. Heredoc writes rejected; agent must use `write` tool.

Filesystem tools: `GlobTool` uses `rglob` with excluded dirs (`.venv`, `__pycache__`, `.git`, `node_modules`). Returns paths sorted by mtime. `ReadTool` reads files with `offset`/`limit` or lists directory entries. `RipgrepTool` runs `rg --json` with context, returns matches grouped by file. `EditTool` performs exact text replace. Unescapes common LLM escaping mistakes (`\\n` to newline). Fails on multiple occurrences. `WriteTool` creates new file, fails if exists, verifies path within cwd. `BashTool` runs subprocess shell with timeout, returns stdout, stderr, returncode, categories.

Memory tools: `SaveMemoryTool` stores to SQLite via `MemoryStore` with computed embedding. `SearchMemoryTool` uses `MemorySearcher` combining cosine similarity and tag filtering. `DisplayMemoryTool` retrieves full entry. `DeleteMemoryTool` requires user confirmation per permission policy.

Interaction: `AskMultipleChoiceTool` emits `MultipleChoiceRequestEvent`, awaits `MultipleChoiceResponseEvent` via `asyncio.Future`. Self-confirming; no permission dialog.

Config: Pydantic v2 schema. `LLMConfig` holds `base_url`, `api_key`, `model`, `max_tokens`, `temperature`, `context_limit`, `compaction_threshold`, `timeout`, `stop`, `reasoning_effort`, `prompt_cache_key`, `tool_mode`. `CachingConfig` defines three tiers: tier1 always-cache (system prompt, tool definitions); tier2 stable-content (file blocks after N turns, project understanding after min turns); tier3 checkpoint on compaction. `PermissionsConfig` lists allowed, denied, require-confirmation tools. Bash subconfig defines allowed, denied, auto-approve categories. `ConfigLoader` uses `ruamel.yaml` for round-trip editing. Environment overrides `DENDROPHIS_API_KEY`, `BASE_URL`, `MODEL`.

`MemoryStore` uses SQLite with WAL mode. Tables: `memories`, `tags`, `tag_memories`. Embeddings stored as float32 BLOBs. Pluggable `BaseEmbedder`. CRUD operations, tag management, `cosine_similarity` computation.

Skills: `SkillManager` loads `.md` files from skills directory. Parses YAML frontmatter for name and description. `activate()` injects skill content into context via `/command`. `deactivate` via `/stop` or `/normal`.

`SessionPersister` saves and loads session JSON, optionally `xz` compressed. `PrimerManager` tracks files for project understanding persistence.

`SessionStats` tracks cumulative and per-turn metrics: prompt tokens, completion tokens, cached tokens, cost USD, tokens per second, time to first token. TPS sampled every 8 tokens.

`PermissionPolicy` evaluates tool calls against config. `check_tool()` for non-bash. `check_bash()` uses `SimResult` categories. Decision enum: `ALLOW`, `DENY`, `CONFIRM`.
