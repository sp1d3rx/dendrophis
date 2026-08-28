# Code-Reviewer Subagent — Live Test Report

**Date:** 2026-08-20 (13:43–14:20)
**Tested by:** Dex (orchestrator) via `invoke_subagent`
**Scope:** `dendrophis/subagents/handlers/code_reviewer.py` (223 lines at test time) + registration in `dendrophis/session/subagents.py` + payload wiring in `dendrophis/tools/builtins/subagents.py`
**Method:** 3 live invocations (2 timeouts, 1 success) + direct model benchmarking + source cross-check of handler, tool, client, and tool-executor timeout

## Verdict

The code-reviewer **works**, but only inside a narrow envelope. The active LLM is a **local reasoning model** (Qwen3.8-27B-MLX-oQ6e on omlx-server, port 8000) that emits `reasoning_content` tokens before any answer. A full, unconstrained review takes 70–160+ s of generation, and the session's tool executor kills every tool call at a **hard 120 s** (`TOOL_EXECUTION_TIMEOUT`, `session/tools.py:29`) with no partial output. Both failures were this budget collision, not a plumbing bug. A 29-line diff with an output-constrained task finished in **86.0 s** with a correct, schema-exact, verifiably-accurate review.

| Capability | Status |
|---|---|
| Registration (DI refactor, all 6 handlers) | ✓ Consistent — bootstrapper passes `llm_client` + `config` (run 3 used this path) |
| Prompt assembly (task + diff + files + context) | ✓ Works; diff reaches the model (run 3) |
| JSON parsing (plain, fenced, fallback wrapper) | ✓ Run 3 returned clean schema-exact JSON |
| Review accuracy on small diff | ✓ Correct verdict, exact line citation, constraints honored |
| Large diff (410 lines, 1 file) | ✗ Timeout at 120.00 s (run 2) |
| Multi-file review (10 files, ~80 KB input) | ✗ Timeout at 120.00 s (run 1) |
| `search_meta`-style observability | ✗ None — no elapsed time, no token usage in `SubagentResponse` |

## Test Runs

### Run 1 — multi-file review (TIMED OUT at 120.00 s)

```
agent="code-reviewer"
task="Review the uncommitted changes to the subagent infrastructure... Focus on
      (1) correctness bugs, (2) error handling, (3) consistency across handler
      files, (4) resource/safety issues..."
context={"files": [10 files incl. .temp/code_reviewer_test_diff.txt (2317 lines),
         all 6 handlers, subagents tool + session module],
         "depth": "thorough", "constraints": [3 items]}
```

Result: `Tool execution timed out after 120 seconds` (debug log: 13:44:29.116 → 13:46:29.117, exactly 120.001 s). No partial output.

Why: the handler reads each of 10 files up to 8000 chars (≈ 80 KB ≈ 20 k prompt tokens) — prefill on a local 27B alone consumes a large slice of the budget — then the verbose new system prompt (Greybeard + Hettinger doctrine, mandatory JSON with `issues` + `hettinger_notes` + `greybeard_notes` + `praise`) drives a long reasoning phase plus a long generation.

### Run 2 — 410-line single-file diff (TIMED OUT at 120.00 s)

```
agent="code-reviewer"
task="Review this diff (.temp/cr_test_diff.txt, git diff of the subagent handler
      registration and CodeReviewerHandler). Check correctness, error handling,
      consistency, edge cases. Report under 60 lines, ranked by severity."
context={"files": [".temp/cr_test_diff.txt"], "depth": "quick"}
```

Result: `Tool execution timed out after 120 seconds` (13:48:57.962 → ~13:50:58). Note: `files` was passed but **not** `context["diff"]`, so the 410-line diff was delivered only via the 8000-char file preview — and the tool never populates `payload["diff"]` anyway (see Finding F1).

### Run 3 — 29-line diff, output-constrained (SUCCESS, 86.0 s)

```
agent="code-reviewer"
task="Review this small test-only diff. Return at most 3 issues, each described
      in one sentence. Keep hettinger_notes, greybeard_notes, and praise to at
      most 2 short items each."
context={"diff": "<29-line unified diff of tests/test_compactor.py + tests/test_config.py>"}
```

Result (14:12:59.961 → 14:14:25.993 = **86.03 s**):

```json
{
  "approval": "approved",
  "summary": "The diff is behavior-preserving and mostly stylistic; the main
              concern is readability of the collapsed test YAML string.",
  "issues": [
    {"severity": "suggestion", "file": "tests/test_config.py", "line": 29,
     "description": "The collapsed YAML payload on one line is difficult to scan
                     and may exceed line-length limits.",
     "suggestion": "Use a triple-quoted or `textwrap.dedent` literal to preserve
                    the configuration's visual structure."}
  ],
  "hettinger_notes": [2 items], "greybeard_notes": [2 items], "praise": [2 items]
}
```

**Post-hoc verification (all passed):**

- `line: 29` is exact — the collapsed `write_text` call is literally at `tests/test_config.py:29` (confirmed by direct read).
- "behavior-preserving" is correct — the concatenated original and the collapsed string are byte-identical (`"llm:\n  provider: openai\n  model: gpt-4o\n  api_key: test-key\n"`).
- The newline-fix praise matches the `\ No newline at end of file` marker removed in `test_compactor.py`.
- All output constraints honored: 1 issue (≤3), exactly 2 items in each notes/praise array.
- Severity calibration sane: a style regression on a test file → `suggestion`, not `blocker`.

## Root-Cause Analysis of the Timeouts

Evidence chain:

1. **Hard 120 s ceiling, no partial output.** `session/tools.py:29` — `TOOL_EXECUTION_TIMEOUT = 120.0`; applied via `asyncio.wait_for` at `:392` to *every* tool, including `invoke_subagent`. Identical to the researcher's documented limitation (run C, 12:45 session).
2. **Active model is a reasoning model.** `configs/omlx.yaml` → `model: Qwen3.8-27B-MLX-oQ6e` at `http://127.0.0.1:8000/v1`. Direct benchmark (streaming chat completion, trivial reviewer-style prompt): 37 `reasoning_content` chunks (≈ 130 tokens) before 5 `content` chunks (≈ 19 tokens) — **~87% of output tokens are reasoning**. Non-streaming repeat: 134 output tokens in 6.2 s (server `total_time: 6.16`), effective ≈ 22 tok/s.
3. **The handler discards reasoning but pays for it.** `code_reviewer.py` accumulates only `TextDeltaEvent` deltas; `ReasoningDeltaEvent` (emitted by `llm/client.py:1207`, defined `events/types.py:192`) is streamed to the event bus and dropped. Reasoning tokens buy nothing in the output but consume the entire wall-clock budget.
4. **The new system prompt inflates the output floor.** The pre-refactor prompt asked for "approval status and specific issues." The post-refactor prompt mandates a full JSON object with four prose arrays plus per-issue `description` + `suggestion` — on a reasoning model this means 1.5–3 k tokens of thinking plus 0.5–1.5 k of JSON for a *real* review.
5. **Extrapolation fits the data.** Run 3 (tiny diff, constrained output): 86 s. Run 2 (410-line diff, unconstrained): >120 s. Run 1 (≈ 20 k prompt tokens + unconstrained): >120 s. Consistent ordering; no other variable changed between runs.

Config note: `configs/omlx.yaml` sets LLM `timeout: 300.0` (client-level) and leaves `reasoning_effort:` empty (client sends nothing; server uses default full thinking) — so the 300 s LLM timeout is never reached; the 120 s tool timeout always fires first.

## Review of the Diff Under Test

Run 2 was supposed to produce this; it timed out, so I performed it directly against the 410-line diff (`.temp/cr_test_diff.txt`). Findings ranked by severity:

| # | Sev | Location | Finding | Fix |
|---|---|---|---|---|
| F1 | medium | `tools/builtins/subagents.py:137-146` | `_build_payload` never sets `payload["diff"]` for code-reviewer, and the tool description documents only `files/patterns/path/include/sources/depth` — not `diff`, the handler's primary input. The working path (`context["diff"]`) is undocumented. Run 2's diff never reached the model as a diff because of this. | In the default branch of `_build_payload`, forward `context.get("diff")`; add `"diff"` to the tool + context descriptions. |
| F2 | medium | `code_reviewer.py` (`review_context` section) | When the payload has no `context` key, the handler falls back to `request.context` and JSON-dumps it — which contains the same diff again. In run 3 the diff was included **twice** (once as "Unified Diff to Review", once inside "Context / Conventions"), doubling input tokens against the 120 s budget. | Skip the context dump for keys already rendered as their own section (at minimum `diff`, `files`). |
| F3 | medium | `code_reviewer.py:163-173` (file preview) | `[:8000]` truncation with **no marker**. Researcher (587→699-line refactor) emits `+N more characters`; reviewer silently presents a partial file as whole — a truncated file can earn "approved" on unseen code. | Add the same truncation marker researcher uses. |
| F4 | low | `code_reviewer.py` system prompt | The "STRICT SINGLE-LETTER VARIABLE RULE (MANDATORY BLOCKER)" bans `_` (Python's canonical discard idiom) and would emit blocker-severity findings on pre-existing code (`test_runner.py:345,348` uses `for x in error`). Blocker inflation on style nits degrades the signal. | Demote to `suggestion`/`warning`; exempt `_`. |
| F5 | low | `code_reviewer.py:219-223` (module-level `execute`) | The fallback entry point constructs `CodeReviewerHandler()` with no client; the `llm` property then builds a **fresh `LLMClient` per invocation** and never closes its httpx client — a leak on that path (the bootstrapper DI path used in live sessions is fine). | Cache the client on the handler; close in `aclose` if ever created lazily. |
| F6 | low | `code_reviewer.py` `_parse_review_json` fallback | Unparseable model output returns `approval: "comment"`, `status: "success"` — indistinguishable from a genuine "comment" verdict unless the consumer checks for the `raw_review` key. The pre-refactor code at least injected an explicit "Response was not valid JSON" warning issue. | Set a distinct `approval` value (e.g. `"unparsed"`) or a `parse_error: true` flag. |
| F7 | low | handler ↔ tool contract | `patterns`, `path`, `include`, `depth`, `sources`, `constraints` are documented in the tool description but silently unused by this handler (same class as researcher's `constraints` gap). | Either honor them or prune them from the description. |

Praise (what the diff does well):

- DI refactor is **consistent across all six handlers** — every handler now receives `llm_client` + `config` from `SubagentBootstrapper`; no stragglers on the old module-function registration.
- JSON extraction hardened: plain → fenced-block → fallback wrapper (previously inline, single-shot).
- Failure path logs with `exc_info=True` and returns `status: "failure"` with the error — and `InvokeSubagentTool.execute` now surfaces `response.result["error"]` to the orchestrator. Good end-to-end error plumbing.

## Unexplained Event

Mid-test, an `execute_code` call (parsing an 8.7 KB SSE file — microseconds of Python) **timed out at exactly 120 s** (~14:04). An equivalent `bash` + `python3 -c` ran instantly seconds later. Cause unverified — candidates: `execute_code` sandbox spawn latency under load, event-loop congestion, or a tool-specific stall. Not reproducible on demand so far; flagged for the next session.

## How to Use the Code-Reviewer Effectively

1. **Keep diffs small: ≤ ~50 lines per call.** Run 3 (29 lines) took 86 s; budget nearly the whole 120 s. Split large diffs per-file or per-hunk.
2. **Pass the diff via `context["diff"]`** (undocumented — see F1). Don't rely on `files` for the diff content.
3. **Constrain the output in the task**: "at most N issues, one sentence each, 2 notes max" measurably shrinks the generation (and reasoning) budget. Run 3 did exactly this.
4. **Skip `files`** unless the model genuinely needs surrounding context — each file adds up to 8000 chars *and* gets double-dumped via the context JSON (F2).
5. **Expect ~1.5–2 min of wall time** per call; there is no progress or partial output.
6. **Verify citations** — they were exact in run 3, but the model never sees the repo beyond what you hand it.

## Recommended Fixes (for the codebase, in priority order)

1. **Raise or make per-tool the 120 s ceiling for `invoke_subagent`** (e.g. 300 s, matching `llm.timeout`). This is the single change that makes all LLM-backed subagents usable for real work. (`session/tools.py:29,392`)
2. **Set `reasoning_effort: low` (or `none`) in `configs/omlx.yaml` for subagent LLM clients** if the server honors it — subagent calls don't need a full thinking trace.
3. F1 (document + wire `diff`), F2 (dedupe context dump), F3 (truncation marker) — cheap, each directly protects the wall-clock budget or review integrity.
4. Add elapsed-time + prompt/completion token accounting to `SubagentResponse` (the client already receives `usage`) so future tests can diagnose budget collisions from the response instead of a black-box timeout.

## Evidence

- Run 1 / 2 / 3 raw responses: this session, 13:44, 13:48, 14:12 (orchestrator tool logs).
- Debug log `~/.config/dendrophis/debug.log` lines 40922–41190: exact tool-call/result timestamps for all three runs (120.001 s / 120.001 s / 86.03 s).
- Benchmark artifacts: `.temp/bench_small.sse` (streaming: 37 reasoning / 5 content chunks), `.temp/bench_small.json` (non-streaming: `usage.total_time: 6.16`, 134 output tokens).
- Source verified: `dendrophis/subagents/handlers/code_reviewer.py` (223 lines, full read), `dendrophis/tools/builtins/subagents.py:117-146`, `dendrophis/session/tools.py:24-29,386-400`, `dendrophis/llm/client.py:1207`, `dendrophis/events/types.py:192`, `configs/omlx.yaml` (model, endpoint, `reasoning_effort` empty, LLM timeout 300).
- Verification: `tests/test_config.py:24-32` direct read (line-29 citation exact); string-equality check of collapsed vs original YAML payload (identical).
