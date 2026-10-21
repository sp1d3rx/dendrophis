# Tool-Call Vertical Slice — Audit Findings (2026-10-20)

Scope: `session/chat.py`, `llm/stream.py`, `session/tools.py`, `tools/executor.py`,
`tools/builtins/*`, `tools/bash_sandbox.py`, `ui/screens/main_events.py`.

Provenance note: the original 1–16 findings list was produced in conversation and
lost to context compaction on 2026-10-20. Items 1–11 were completed before the loss
(details in git history + memory). Items 12–16 are a re-audit reconstruction;
item 14 (the `_signals_unfinished_intent` regex heap) was confirmed by the user as
one of the original findings.

## Done (1–11, 2026-10-16 → 20)

- [x] 1–11: completed. Highlights (verbatim wording lost):
  - (4) Tool timeouts: `BaseTool.execution_timeout(arguments)` in `tools/base.py`;
    BashTool derives from its `timeout_ms` arg; executor applies
    `declared + TOOL_TIMEOUT_MARGIN` safety net.
  - (7) Fragile `.bak` heuristic removed from `ToolExecutor.execute`; backups are
    tool-owned via `backup_existing_file()` in `tools/backups.py`.
  - (9) Parallel approved non-interactive batches gated by `config.tools.parallel_tools`;
    order-preserving `asyncio.gather` mapping back to call indices.
  - (10) Future-based confirmations: one `asyncio.Future` per request id, woken by
    `respond_to_confirmation`; cancel-watcher mirrors the threading flag.
  - (6, 8, 11): fixed by user (details in git history).
  - Follow-up regex cleanup (user-flagged): `llm/stream.py` fenced-JSON fallback now
    uses `_JSON_FENCE` scan + `_extract_balanced_json` (string-aware balanced-brace
    scanner); thinking-strip consolidated into compiled `_THINKING_BLOCK`.
    `tools/bash_sandbox.py` `is_heredoc_write_pattern` simplified to
    `_HEREDOC_REDIRECT` / `_ECHO_LINE_REDIRECT` + one compound condition.
    Verified: 40 stream/sandbox tests, 19 consumer-side tests, ruff clean.

## Done (12–17, reconstructed 2026-10-20; all completed 2026-10-20)

### 12. Error semantics defined in 3 places + double-encoded error JSON  ← DONE (2026-10-20)

- `tools/executor.py` decided success at creation: `"error" not in raw_result`.
- `session/tools.py` had `is_tool_error(content)` (JSON-parse + `"error" in dict`).
- `subagents/handlers/code_writer.py::_is_tool_error` had a third copy of the
  same parse+check (only exercised by its unit test, never called in prod).
- `session/tools.py::_execute_single_tool` also derives success:
  `result.success if hasattr(...) else is_tool_error(content)` — a legitimate
  fallback (prefers the `success` attr, falls back to the content predicate),
  kept as-is.
- Real wart: `_make_error_result` wraps its message in
  `json.dumps({"error": msg})`, but the timeout/reject call sites passed messages
  that were ALREADY JSON strings (`'{"error": "Tool execution rejected by user"}'`)
  → the model saw `{"error": "{\"error\": \"...\"}"}`. Double-encoded.
- Fix APPLIED:
  1. `is_tool_error` moved to `tools/executor.py` as the single canonical
     predicate (next to `ToolResult`); `session/tools.py` re-exports it so
     existing importers (chat.py) are untouched. All import paths resolve to the
     SAME function object (asserted in test).
  2. `tools/executor.py` derives `success` via `is_tool_error` in both the dict
     and string branches (no more inline `"error" not in raw_result`).
  3. `code_writer.py::_is_tool_error` delegates to the canonical predicate.
  4. Timeout/reject call sites now pass PLAIN strings to `_make_error_result`
     (single-encode). The `_execute_single_tool` TimeoutError/Exception handlers
     were also consolidated onto `_make_error_result` (was hand-building
     `ToolResult(content=json.dumps(...))`).
- Files: `tools/executor.py`, `session/tools.py`,
  `subagents/handlers/code_writer.py`, `tests/test_tool_error_semantics.py` (new,
  22 tests pinning the predicate + single-encoded content shape).
- Verified: 22 new + 27 affected existing (session executor, code-writer,
  dependency-injection, mcp, chat, multi-turn) + 114-test broad tool/executor/chat
  sweep, ruff check/format clean.
- Follow-up (user-flagged): `tests/test_code_writer_e2e.py` was erroring the whole
  `pytest tests/` collection with `ModuleNotFoundError: No module named 'yaml'`.
  Root cause: it's the only pyyaml consumer in the main project, but pyyaml is NOT
  a declared dependency (only `ruamel.yaml` is; pyyaml belongs to the `twitch-chat`
  workspace member). It's also a live-server e2e test (needs OMLX up +
  `configs/omlx.yaml`). Fix: made it self-skip — `yaml = pytest.importorskip("yaml")`
  at collection (no network) + a module-scoped autouse fixture that probes
  `{base_url}/models` (any HTTP response = up; connection error/timeout = skip).
  Test-file-only, no dependency changes. Now `pytest tests/` collects 349 tests
  with the e2e module cleanly skipped (1 skipped) when pyyaml/OMLX are absent.

### 13. `main_events.py`: six copy-pasted confirmation pushers + 90-line replay monolith  ← DONE (2026-10-20)

- `_on_tool_confirmation_request`, `_on_multiple_choice_request`, and the four
  `*_proposal` handlers are six copies of: inner fn → import screen class →
  `self.call_later(lambda: self.app.push_screen(...))`.
- `_on_context_updated` replays the entire chat history in one ~90-line method
  (role dispatch, pending tool-call/result matching, inline error logging).
- Fix: one `_push_screen_later(screen_cls, *args)` helper; extract
  `_replay_context()` with per-role methods. UI-only, no behavior change.
- Files: `ui/screens/main_events.py`.
- Risk: low (UI only). Verify with existing UI tests + manual replay path.

**Implementation (2026-10-20):**
- Added `_push_screen_later(screen_factory: Callable[[], Any])` — schedules
  `self.call_later(lambda: self.app.push_screen(screen_factory()))`. The
  factory runs at push time (UI context), not event time. All 7 pushers
  (tool_confirmation, multiple_choice, edit/write/append/python_exec
  proposals, primer) now delegate to it as a one-line lambda.
- Screen classes moved from deferred in-handler imports to module-level
  imports. Verified no import cycle: the 7 screens only depend on
  `dendrophis.events` + textual widgets; `main.py` (host) already imports
  textual at top level. Construction is still lazy (inside the lambda).
- Removed 2 dead no-op `@listen` handlers: `_on_tool_call_delta` and
  `_on_tool_execution_finished` (both `pass`). Confirmed no external
  references (the `status_panel._on_tool_execution_finished` is a separate
  class with a real body). Dropped now-unused `ToolCallDeltaEvent` /
  `ToolExecutionFinishedEvent` imports.
- Extracted `_on_context_updated` replay into `_replay_context(chat)` +
  per-role `_replay_assistant_message` / `_replay_tool_message` +
  `_message_text` static helper. Preserved exact behavior: same try/except
  scope per message, tool results read RAW content (not list-joined),
  pending tool-call matching by id, same debug logging (moved
  `import traceback` to module top).
- New `tests/test_main_events_replay.py` (8 tests) pins: mixed-conversation
  replay order + tool matching, orphan tool result, raw (non-joined) tool
  content, malformed-message isolation, `_message_text` variants,
  push-later constructs at push-time not schedule-time, dead handlers gone,
  all 7 pushers use the shared helper.
- Verified: 8 new replay tests + 21 `test_memory.py` (instantiates
  `MainScreen`) + full suite 357 passed / 1 skipped (OMLX e2e). Ruff
  check/format clean on both files.

### 14. `_signals_unfinished_intent` keyword heap in `chat.py`  ← CONFIRMED original finding — DONE v2 (2026-10-20)

- 29 action verbs × 14 intro phrases were hardcoded inside the method body;
  406 `re.search` builds per call (pattern rebuilt every verb×intro pair);
  one ad-hoc `back` exclusion; zero tests.
- v1 fix (superseded): lists lifted to module constants; 406 (match,
  back-exclusion) pairs precompiled into `_INTENT_PATTERNS`. Review caught that
  v1 pinned buggy behavior instead of fixing it:
  - Still 812 compiled patterns / up to 406 `.search` calls per turn instead of
    one alternation.
  - Missing leading `\b`: `i'?ll` matched inside "still"/"will"/"skill"
    ("Things still look good." → True, a false positive).
  - Global per-pair `back` exclusion caused a false negative: "I'll check back
    later; first I'll check the logs" → False (the pair was suppressed because
    "check back" appeared ANYWHERE in the tail).
  - Redundant/broken intros: `i'?ll start` only matched ungrammatical
    "I'll start <verb>"; `now i'?ll` / `now i will` / `i'?m going to` /
    `i am going to` were shadowed by `i'?ll` / `i will` / `going to`.
- v2 fix (current): ONE compiled regex
  `_INTENT_RE = re.compile(rf"\b(?:{'|'.join(_INTENT_INTROS)})\s+(?:{'|'.join(_INTENT_ACTION_VERBS)})\b(?!\s+back\b)")`
  — single alternation, per-position negative lookahead, leading `\b`.
  Intros pruned to the 9 non-redundant forms (longest-first at shared
  prefixes); method body is a single `_INTENT_RE.search(tail) is not None`.
- Behavior changes vs v1 (all intended, all test-pinned):
  - "Things still look good." / "My skill test passed" / "Things will look
    good." now False (false positives fixed by `\b`).
  - "I'll check back later; first I'll check the logs" now True (the engine
    skips the suppressed "check back" match and finds the second "I'll check"
    in one pass).
  - "I'll read back the file, then let me check the config" stays True via the
    per-position lookahead (no regression).
- Files: `session/chat.py`, `tests/test_continuation_nudge_intent.py`.
- Verified: 33-case pinning suite (all expectations probed against the new
  regex in a fresh process BEFORE editing), 51 chat-related tests, exactly 1
  `re.Pattern` in the module, ruff clean.

### 15. `session/tools.py` robustness smells  ← DONE (2026-10-20)

- `_process_bash_tool` ends in bare `except Exception: pass` — a `BashSandbox`
  crash silently degrades to "ask the user for confirmation" instead of a visible
  error result.
- `POLL_INTERVAL` cancel-watcher polls a `threading.Event` every 100 ms — leftover
  of the old polling design; acceptable, but undocumented.
- Fix: narrow the except to the JSON-parse step (let sandbox errors surface as an
  error result, or log + confirm); add a comment on why the watcher polls.
- Files: `session/tools.py`.
- Risk: low. Sandbox errors currently fall through to confirmation; surfacing them
  as errors is a behavior change for a rare path — call it out in the PR note.

**Implementation (2026-10-20):**
- Narrowed the broad `except Exception: pass` in `_process_bash_tool` so only
  `json.loads` is wrapped. A malformed `arguments` payload (bad JSON, non-object,
  or non-string `command`) is now rejected as an **invalid** tool call
  (`invalid_tools`), never sent to confirmation. Added `isinstance` guards so a
  non-dict `arguments` (AttributeError on `.get`) and non-string `command`
  (TypeError in `simulate`) are caught cleanly instead of propagating.
- `BashSandbox().simulate()` is now in its own targeted `try/except` that fails
  **closed**: on error it logs via `_debug_logger` (if present) and appends a
  visible `"Bash simulation failed: ..."` to `invalid_tools` — no longer a silent
  fall-through to a confirmation prompt for an un-analyzed command. This is the
  behavior change called out in the risk note (rare path; previously silent).
- ALLOW / DENY / CONFIRM / heredoc paths unchanged. Verified there is no upstream
  `try/except` around `execute()` in `_run_completion_loop`, so fail-closed
  (rather than propagating) was the safe choice for a bash-executing agent.
- Documented `_watch_cancel_flag`: explains the 100 ms poll mirrors a
  `threading.Event` (no native asyncio awaitable) from the cancel thread into all
  pending confirmation futures, and why a single shared polling task is preferred
  over a thread-pool `event.wait()` per confirmation.
- New `tests/test_bash_tool_policy.py` (11 tests) pins: invalid-JSON / non-object /
  non-string-command rejected-not-confirmed, empty-args → empty command,
  simulation-failure fails closed (unit + end-to-end via `execute()` asserting no
  `ToolConfirmationRequestEvent` and no execution), and the preserved
  CONFIRM / ALLOW / DENY / heredoc paths.
- Verified: 11 new + 56 existing executor/permission/sandbox/MCP/DI tests + full
  suite 368 passed / 1 skipped (OMLX e2e). Ruff check/format clean.

### 16. `llm/stream.py` does two jobs; tag data scattered  ← DONE (2026-10-20)

- One ~800-line module held both the SSE streaming parser and the
  text-embedded tool-call/thinking tag parser (`_scan_text_mode` and friends).
- Tag tuples (`THINK_START_TAGS`, `THOUGHT_CLOSE_TAGS`, `TOOL_START_TAGS`,
  `TOOL_CLOSE_TAGS`) were module-level data mixed with parser code; quirk:
  `<tool_call|>` appears in BOTH `TOOL_START_TAGS` and `TOOL_CLOSE_TAGS`
  (undocumented — models that use it as a delimiter).
- XML/JSON tool-call decoding was copy-pasted between `_emit_tool_call_events`
  (streaming) and `parse_text_tool_calls` (batch).

Fix APPLIED:
1. **Split the two jobs.** New `llm/tag_parser.py` owns the text side: the tag
   tables, the `_scan_*` state machine + `_run_tag_state_machine`,
   `_parse_function_xml`, `_extract_balanced_json`, `_emit_tool_call_events`,
   and `parse_text_tool_calls`. `stream.py` is now SSE-only (`parse_sse_event`,
   `iter_sse_events`, the delta/usage/error/finish extractors, the Responses-API
   handler) and drives the tag machine per text delta. One-way dependency
   `stream → tag_parser` (no cycle). `parse_text_tool_calls` is re-exported from
   `stream.py` (in `__all__`) so `client.py`'s existing import is untouched.
2. **Documented the dual boundary tag.** A note above the tag tables explains
   that a tag in both a START and CLOSE table is a *boundary marker* (same token
   opens and closes), and that close tags are matched before start tags in
   `tool_calling` mode — which is what lets `<tool_call|>` close an open call.
   `_scan_tool_calling_mode`'s docstring carries the same note.
3. **Deduplicated the XML/JSON decode.** New `_decode_tool_call_payload`
   (XML→JSON, returns `(name|None, serialized_args)` or `None`) +
   `_tool_arguments_from_payload` (the previously copy-pasted
   `"arguments"`-or-rest-of-keys serialization) are now the single decode path
   for both `_emit_tool_call_events` and `parse_text_tool_calls`.
4. **Removed dead code.** `_parse_python_literal` (defined, never called) is gone.

Behavior notes (safe normalizations for malformed input, no test pinned the old
way; all realistic/tested paths unchanged):
- A JSON payload with a null/missing `"name"` now yields `"unknown"` on the
  streaming path (was `None`) and is *skipped* on the batch path (was: a
  null-name `ToolCall` could be appended) — you can't dispatch a nameless call.
- `_emit_tool_call_events` no longer crashes (AttributeError) if the payload is
  a JSON array/scalar; it now falls back to a `TextDeltaEvent` like the
  non-JSON case.
- Files: `llm/stream.py` (refactored to SSE-only), `llm/tag_parser.py` (new),
  `tests/test_stream.py` (`_parse_function_xml` import moved to `tag_parser`;
  12 new tests for the dedup helpers + dual-tag behavior; `parse_text_tool_calls`
  still imported from `stream` to pin the re-export).
- Verified: 39 stream tests (was 27) + full suite 382 passed / 1 skipped (OMLX
  e2e). Ruff check/format clean. `client.py` untouched.

### 17. `visual_tokens_saved` double-count + side-effect coupling  ← DONE (2026-10-20); ORIGINAL audit item (pasted by user; was one of the lost 12–16)

Original wording (user paste):
> LLMClient._apply_visual_vlm_transformations mutates an instance attribute
> self.last_visual_tokens_saved as a side effect of payload building, which
> ChatOrchestrator._run_completion_loop pulls via getattr(self.llm,
> "last_visual_tokens_saved", 0) on UsageEvent. Because
> _apply_visual_vlm_transformations re-transforms the full message list on every
> iteration of a multi-tool turn, visual_tokens_saved gets re-added on every tool
> loop step, and SessionStats.reset() forgets to reset self.visual_tokens_saved = 0.

Verified in code (2026-10-20):
- `llm/client.py:329` — `self.last_visual_tokens_saved = 0` in `__init__`;
  `:639` reset per transform pass; 4 accumulation sites (`:657, :687, :729, :~757`)
  sum savings over the FULL sanitized message list each pass.
- `llm/client.py:802` — `_build_payload` calls the transform on every
  `stream_chat` call; transform is non-mutating (fresh list), so every loop
  iteration re-counts the same system prompt / old tool results.
- `session/chat.py:626` — `self.stats.visual_tokens_saved +=
  getattr(self.llm, "last_visual_tokens_saved", 0)` on every UsageEvent → the
  same content's savings added once per tool-loop step (number inflates with
  turn length).
- `session/chat.py:190-205` — `SessionStats.reset()` resets prompt/completion/
  cached/cost/tps/ttft but NOT `visual_tokens_saved`; `session.reset()` (the
  /reset command) reuses the LLM client, so stale savings survive into the next
  session and are shown by TokensPanel next to fresh P/C counts.
- Note: re-RENDERING cost is already handled — `render_text_to_1bit_data_uri`
  has a global content-hash cache (`_VISUAL_RENDER_CACHE` in visual_prompt.py).
  The wart is purely the accounting + the cross-module getattr.

Fix APPLIED (2026-10-20):
1. `events/types.py` — `UsageEvent` gained `visual_tokens_saved: int = 0`
   (additive, backward compatible).
2. `llm/client.py` — `self._visual_counted: set[str]` of sha256(content)
   digests + `_count_visual_savings(content)` helper replacing all 4 inline
   accumulation sites (each unique content counted once per client lifetime).
   `stream_chat` captures `last_visual_tokens_saved` right after
   `_build_payload` and attaches it to the yielded UsageEvent via
   `dataclasses.replace` (only when non-zero). `last_visual_tokens_saved` is
   now client-internal — the cross-module `getattr` in chat.py is gone.
3. `session/chat.py` — UsageEvent handler uses `event.visual_tokens_saved`;
   `SessionStats.reset()` clears `visual_tokens_saved`.
4. Tests in `tests/test_visual_prompt.py` (4 new): dedup across transform
   passes (122 → 0 → 90), UsageEvent attachment via stubbed `_stream_raw` +
   `_build_payload`, orchestrator accumulation with a real SessionStats and an
   LLM stub that has NO `last_visual_tokens_saved` attribute (fails on old
   code), and `reset()` clearing the counter.

Semantic after fix: the panel's 🖼 number = tokens saved by UNIQUE content
converted to images (counted once), stable across a long agentic turn.
Known nuance: `_visual_counted` survives `/reset` (the LLM client is not
recreated on session reset), so a new session reusing the same system prompt
under-counts rather than re-counting — consistent with the dedup semantic.

- Files: `events/types.py`, `llm/client.py`, `session/chat.py`,
  `tests/test_visual_prompt.py`.
- Verified: 56 targeted tests (visual + stream + chat + client + compaction +
  cancellation + multi-turn), fresh-process smoke test (step1=122, step2=90,
  no inflation), ruff check/format clean.

## Status

| #  | Title                                        | Status        |
|----|----------------------------------------------|---------------|
| 12 | Error semantics ×3 + double-encoded JSON     | done 2026-10-20 |
| 13 | main_events pushers + replay monolith        | done 2026-10-20 |
| 14 | `_signals_unfinished_intent` regex heap      | done v2 2026-10-20 (single regex) |
| 15 | tools.py bare except + watcher               | done 2026-10-20 |
| 16 | stream.py two jobs + tag tables              | open          |
| 17 | `visual_tokens_saved` double-count + coupling | done 2026-10-20 (original item) |
