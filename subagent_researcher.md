# Researcher Subagent — Live Test Report

**Date:** 2026-08-20 (11:36–11:40; follow-up sessions 12:45, 12:55)
**Tested by:** Dex (orchestrator) via `invoke_subagent`
**Scope:** `dendrophis/subagents/handlers/researcher.py` (587 lines at last check, 2026-08-20 12:55)
**Method:** 6 live invocations across 3 sessions + source cross-check of the handler

## Verdict

The researcher works. Explicit `context["patterns"]` produce correct, well-scoped findings, `search_meta` is present and honest, and synthesis quality is good with accurate file:line citations. The failure mode observed in run 1 is an input-derivation problem, not a plumbing bug: when no explicit patterns are passed, the handler derives regexes from tokens in the task text, and filename tokens (e.g. `README.md`) never match because ripgrep matches file *contents*, not names.

| Capability | Status |
|---|---|
| Explicit `context["patterns"]` | ✓ Worked (run 2: 6/6 true findings) |
| `context["path"]` / `context["include"]` scoping | ✓ Worked (search confined to `dendrophis/`, `*.py` only) |
| `search_meta` (patterns, match counts, errors) | ✓ Present in both runs; zero-match visible |
| `context["sources"]` filtering | ✓ `["files"]` skipped memory |
| Task-derived pattern extraction | ⚠ Mechanism works, but filename tokens yield 0 matches |
| Synthesis (LLM-backed) | ✓ Accurate citations, honest knowledge gaps |
| Confidence scoring | ✓ Code findings separated from memory; 0 findings → `low` |

## Test Runs

### Run 1 — task only, no explicit patterns

```
agent="researcher"
task="Survey the top-level structure of the dendrophis/ package: list its modules
      and subpackages, identify the main entry point (e.g., __main__.py, cli, or
      app module) … check AGENTS.md or README.md for a project overview sentence."
context={"path": "dendrophis", "depth": "quick"}
```

Result: `status: success`, `findings: []`, `confidence: low`, template synthesis
("No relevant information found…").

`search_meta` explained the failure completely:

```json
{
  "patterns_attempted": ["AGENTS.md", "README.md", "__main__.py"],
  "match_counts": {"AGENTS.md": 0, "README.md": 0, "__main__.py": 0},
  "code_findings": 0,
  "errors": []
}
```

Why zero matches (verified against `_derive_search_patterns` and `RipgrepTool`):

1. No backticked symbols or snake_case identifiers in the task, so the handler fell
   to identifier-candidate extraction. Dotted tokens qualified, pulling
   `AGENTS.md`, `README.md`, `__main__.py` out of the prose.
2. Ripgrep searches file **content**. The strings `AGENTS.md` / `README.md` do not
   appear in any file content under `dendrophis/`, and `dendrophis/__main__.py`
   does not contain the literal string `__main__.py` in its body. All three
   patterns were valid regexes that matched nothing.
3. The `path: "dendrophis"` scope additionally excluded the root-level
   `AGENTS.md` / `README.md` where those names actually live as filenames.

Net: run 1 failed despite every mechanism (derivation, scoping, metadata) working
as designed. The gap is between "names files mentioned in a task" and "tokens that
appear in code."

### Run 2 — explicit patterns

```
agent="researcher"
task="Find the main entry point and top-level structure of the dendrophis package…"
context={
  "path": "dendrophis",
  "patterns": ["if __name__ == \"__main__\"", "def main\\("],
  "include": "*.py",
  "sources": ["files"],
  "depth": "quick"
}
```

Result: `status: success`, 6 code findings, `confidence: high`.

```json
{
  "patterns_attempted": ["def main\\(", "if __name__ == \"__main__\""],
  "match_counts": {"def main\\(": 2, "if __name__ == \"__main__\"": 4},
  "code_findings": 6,
  "errors": []
}
```

Findings (all verified against source):

| Source | Content |
|---|---|
| `dendrophis/cli.py:351` | `def main() -> None:` |
| `dendrophis/__main__.py:7` | `if __name__ == "__main__":` |
| `dendrophis/debug_chat.py:555, 700` | `async def main():` + `__main__` guard |
| `dendrophis/scripts/summarize_memories.py:78` | `__main__` guard |
| `dendrophis/benchmarks/event_bus_benchmark.py:811` | `__main__` guard |

Synthesis correctly identified the entry flow (`python -m dendrophis` →
`__main__.py` → `cli.py:main()`), distinguished the primary CLI from standalone
scripts, and listed honest gaps (full body of `__main__.py` unseen, no
`pyproject.toml` console-script check). Post-hoc verification of
`dendrophis/__main__.py` confirmed the flow exactly.

## Handler Behavior (current source)

`dendrophis/subagents/handlers/researcher.py`:

- **Pattern derivation** (`_derive_search_patterns`): explicit
  `context["patterns"]` first, then backticked symbols in the task, then
  snake_case/camelCase/PascalCase/dotted identifier candidates (len ≥ 4, stop-word
  filtered), and finally a ≤4-word first-line fallback.
- **Codebase search** (`_search_codebase`): one ripgrep pass per pattern with
  `path` and `include` from context; parses the nested
  `{file, matches: [{line, content}]}` shape correctly; ≤5 line findings per file;
  errors recorded in `search_meta["errors"]` (also logged at debug).
- **Explicit files** (`context["files"]`): full read with 8000-char preview plus
  truncation marker; directories get listing + up to 8 child files read.
- **Memory search** (`_search_memories`): score threshold ≥ 0.2 before a memory
  finding enters the evidence pool.
- **Confidence** (`_calculate_confidence`): computed over code findings first
  (≥2 findings and avg relevance ≥ 0.7 → `high`); falls back to overall average.
  Empty pool → `low`.
- **Synthesis**: LLM with structured prompt; `_extract_knowledge_gaps` parses the
  "Knowledge Gaps" section into the `gaps` array; template fallback if LLM
  unavailable; top-12 (quick) / top-30 (thorough) finding cap.

## Comparison with Prior Report

The previous `subagent_researcher.md` (deleted per user request, 2026-08-20
11:12–11:20 session) documented the handler's P0/P1 bugs. Today's runs plus source
review confirm they are **fixed** in current HEAD:

| Prior issue | Current state |
|---|---|
| R1: ripgrep parse mismatch (`KeyError: 'line'`, all results discarded) | Fixed — nested `{file, matches}` shape parsed correctly (run 2 produced real findings) |
| R2: raw natural-language sentence used as regex (`query[:60]`) | Fixed — `_derive_search_patterns` tokenizes; short-query fallback capped at 4 words |
| R3: search failures invisible (debug-log only) | Fixed — `search_meta` in every response (seen in both runs) |
| R4: `context["patterns"]` / `constraints` ignored | Fixed — `patterns` honored (run 2). `constraints` still not read by the handler |
| R5: `path` / `include` never passed to ripgrep | Fixed — both forwarded from context |
| R6: ~0.07 memory noise reaching synthesis | Fixed — 0.2 threshold (no sub-threshold findings in run 1) |
| R7: blind 1000-char file truncation | Fixed — 8000-char preview with explicit `+N more characters` marker |
| R8: confidence diluted by mixed finding types | Improved — code findings scored separately |

Not re-verified today: `constraints` handling, spec drift in
`specs/researcher.md` (`memory_tags`, `depth: "exhaustive"`, clarification promise),
and the tool description in `tools/builtins/subagents.py`.

## How to Use the Researcher Effectively

1. **Always pass explicit `patterns`** for anything that must be found by content
   search. Derivation from task text only helps when the task contains
   backticked/snake_case identifiers that appear in code.
2. **Use `context["files"]` for known paths.** If you know the file, hand it over
   instead of hoping a name token regex-matches.
3. **Scope with `path` + `include`** to keep results tight (`*.py`, subpackage).
4. **Use `sources: ["files"]`** to skip memory for deterministic codebase surveys.
5. **Check `search_meta` first.** `code_findings: 0` with populated
   `patterns_attempted` means "search ran, nothing matched" — fix the patterns,
   don't retry with the same task.
6. **Depth:** `quick` caps at 12 findings, `thorough` at 30.
7. **One question per call.** The runner enforces a hard 120s timeout with no
   partial output (follow-up Run C, below). Multi-file recon must be split into
   separate calls.

## Remaining Limitations

- Single-shot: no iterative refinement from intermediate results. A task needing
  multi-round exploration ("find X, then find where X is called") needs
  orchestrator-driven follow-up calls.
- Filename mentions in task text are not searchable — ripgrep is content-only, and
  the derivation step has no glob/filename fallback.
- `constraints` in context is silently unused.
- Fixed relevance scores (0.85 code / 0.6–0.95 files) — ranking is positional,
  not semantic.

## Evidence

- Run 1 / Run 2 raw responses: this session, 11:36–11:38 (orchestrator tool logs).
- Source: `dendrophis/subagents/handlers/researcher.py` (461 lines, read in full
  2026-08-20 11:40).
- Verification: `dendrophis/__main__.py` read directly — imports `main` from
  `dendrophis.cli`, call confirmed.

## Follow-up Test — 2026-08-20 12:45

**Context:** Second `invoke_subagent` test session. Two invocations: Run C timed out, Run D succeeded. All observations below cross-checked against handler source at this session. Note: the handler has grown since the 11:36 report (461 → 541 lines), so some earlier "Remaining Limitations" no longer fully apply.

### Run C — broad multi-part task (TIMED OUT)

```
agent="researcher"
task="Test invocation of researcher subagent. Do a quick, read-only recon of
      this workspace (depth: quick): (1) identify what the top-level project is
      by reading README.md and AGENTS.md (first ~50 lines each), (2) list the
      main source directories under dendrophis/ and twitch-chat/ with their
      top-level contents, (3) note any existing subagent test artifacts at
      subagent_researcher.md and aug-20-subagent-findings.md (first ~20 lines
      each). Report a concise structured summary."
context={"sources": ["files"], "depth": "quick",
         "files": ["README.md", "AGENTS.md", "subagent_researcher.md",
                   "aug-20-subagent-findings.md"]}
```

Result: `Tool execution timed out after 120 seconds`. No partial result returned,
no artifacts written. Four full-file reads (including the 8.8KB prior report it
was asked to inspect) plus two directory reconstructions plus LLM synthesis
exceeds the budget. The subagent runner has a hard 120s timeout with **no partial
output** — the call either completes or vanishes.

### Run D — tight single-question task (SUCCESS)

```
agent="researcher"
task="Verify the main entry point of the dendrophis package: what does
      dendrophis/__main__.py import and call? Report the entry flow in 3-5
      sentences."
context={"path": "dendrophis",
         "patterns": ["def main\\(", "if __name__ == \"__main__\""],
         "include": "*.py",
         "files": ["dendrophis/__main__.py"],
         "sources": ["files"],
         "depth": "quick"}
```

Result: `status: success`, `confidence: high`, `code_findings: 8`, `errors: []`.

```json
"search_meta": {
  "patterns_attempted": ["file:__main__.py", "def main\\(", "if __name__ == \"__main__\""],
  "match_counts": {"file:__main__.py": 1, "def main\\(": 2, "if __name__ == \"__main__\"": 4},
  "code_findings": 8,
  "errors": []
}
```

Synthesis reported the entry flow; verified directly against source post-hoc:
`python -m dendrophis` → `dendrophis/__main__.py` → `from dendrophis.cli import main`
→ `main()` under the `__main__` guard (`__main__.py:7`) → `def main() -> None:` at
`dendrophis/cli.py:351`. Read of `__main__.py` (8 lines) and a targeted ripgrep
(`^def main\(` in `cli.py`) both match the synthesis exactly. Knowledge gap
(`cli.py` main body not in evidence) honestly flagged.

### New Findings (source-verified)

1. **120s hard timeout, no partial output** (Run C). Budget one focused question
   and a handful of small files per call. Multi-file surveys need
   orchestrator-driven follow-up calls.
2. **Double-read of `context["files"]` entries, no dedup.** `dendrophis/__main__.py`
   appears **twice** in Run D findings (relevance 0.95 and 0.6, both `type: "file"`,
   identical content):
   - 0.95 entry: filename-candidate path. The bare token `__main__.py` in the task
     text (the tokenizer splits `dendrophis/__main__.py` on `/`) was resolved via
     direct existence check at `search_path` and read (researcher.py:255-305;
     fixed 0.95 at :299).
   - 0.6 entry: explicit-files block reading the same file a second time
     (researcher.py:348-375; baseline 0.6, 0.95 only if the full query string
     occurs in the content).
   No `source`-based dedup exists anywhere — findings sets are deduped only during
   pattern *derivation* (researcher.py:152-153), not during evidence collection.
   Cosmetic: inflates `code_findings` (8 reported, 7 unique) and doesn't distort
   synthesis. Worth a dedup-by-source fix before anyone ranks on finding counts.
3. **`file:` prefix in `search_meta` is by design** (researcher.py:261, :282) —
   filename candidates are tracked alongside ripgrep content patterns with their
   own match counts (resolved file count, not content matches).
4. **Filename tokens in task text ARE now searchable** — corrects the 11:36
   "Remaining Limitations" item ("Filename mentions in task text are not
   searchable"). Current `_derive_search_patterns` (researcher.py:151-241) routes
   any token ending in a known extension (.py/.md/.json/.yaml/.yml/.toml/.rst/
   .txt/.ts/.js/.html/.css/.sh) — from explicit `context["patterns"]`, backticked
   symbols, or bare word tokens — into `filename_candidates_set`, and
   `_search_codebase` resolves them via direct existence check at `Path.cwd()` /
   `Path(search_path)` plus a GlobTool `**/<name>` fallback when a glob tool is
   wired (researcher.py:255-280). Run D's 0.95 finding is this path working.
   Caveat: direct checks only hit files at the two roots; nested files depend on
   the GlobTool fallback.
5. **Handler drift since 11:36 report:** 461 → 541 lines. Filename-candidate
   resolution is new or expanded; "How to Use" items 1-2 from the 11:36 report
   remain valid (explicit patterns and known files are still the reliable shape),
   but the "gap between names files mentioned in a task and tokens that appear in
   code" is now partially bridged.

### Verdict (12:45)

Plumbing confirmed working a second time. Reliable invocation shape: one question
+ explicit `patterns` + `files` + `path`/`include` + `depth: quick`. Known rough
edges: hard 120s timeout with zero partial output, and duplicate file findings
from double-read. No new bugs in search or synthesis; both verified against source.

## Follow-up Test — 2026-08-20 12:55

**Context:** Third `invoke_subagent` test session. Two invocations: Run E (plain-English task, no explicit patterns) returned an empty result; Run F (explicit patterns + a backticked symbol) succeeded with a mostly-accurate synthesis. The handler drifted again since the 12:45 session: 541 → 587 lines, and the 12:45 double-read finding is now fixed in source.

### Run E — plain-English task, no explicit patterns (SILENT NO-OP)

```
agent="researcher"
task="Map the dendrophis Python package structure: identify the main entry
      point, the module responsible for subagent invocation, and how tools
      are registered/wired. Report file paths with line numbers."
context={"path": "dendrophis", "include": "*.py", "depth": "quick"}
```

Result: `status: success`, `findings: []`, `confidence: low`, template synthesis
("No relevant information found…"). `search_meta` was entirely empty:

```json
{"patterns_attempted": [], "match_counts": {}, "code_findings": 0, "errors": []}
```

Root cause, traced line-by-line through `_derive_search_patterns`:

1. No explicit `context["patterns"]`, no backticked symbols in the task.
2. Identifier token pass: no token in the task contains `_`, ends in a known
   file extension, or has interior uppercase. The camelCase test is
   `any(c.isupper() for c in token[1:])`, so pure single-word PascalCase like
   `Python` is missed, and plain lowercase words (`subagent`, `invocation`,
   `responsible`) qualify only if they contain an underscore.
3. Fallback requires the first line to be ≤ 4 words / ≤ 40 chars; the task is a
   single 15-word line, so nothing was added.

Net: **zero patterns derived, zero searches executed, nothing recorded in
`search_meta["errors"]`**. The call "succeeds" with empty findings and a
low-confidence template answer. This is a stronger failure mode than the 11:36
Run 1: there, derivation at least produced filename tokens and
`patterns_attempted` documented what was tried (and failed to match); here the
no-op is completely silent — the only tell is an empty `patterns_attempted`.

Secondary observation: the derivation stop-word list hard-codes project names
(`"dendrophis"`, `"boiga"`) — repo-specific test residue baked into a generic
handler.

### Run F — explicit patterns + backticked symbol (SUCCESS)

```
agent="researcher"
task="Search the dendrophis package for subagent invocation code. Find the
      function that implements `invoke_subagent`, and list which subagent
      types are supported. Report exact file paths and line numbers."
context={"path": "dendrophis", "include": "*.py",
         "patterns": ["def invoke_subagent", "subagent", "entry_point", "register"],
         "depth": "quick"}
```

Result: `status: success`, `confidence: high`, 12 findings returned.

```json
"search_meta": {
  "patterns_attempted": ["def invoke_subagent", "entry_point", "invoke_subagent",
                         "register", "subagent"],
  "match_counts": {"def invoke_subagent": 1, "entry_point": 0,
                   "invoke_subagent": 6, "register": 28, "subagent": 71},
  "code_findings": 99,
  "errors": []
}
```

Notes:

- **Backtick extraction confirmed.** The fifth pattern `invoke_subagent` was not
  in `context["patterns"]`; it came from the backticked symbol in the task text.
  The "Handler Behavior" description from the 11:36 report holds.
- **`search_meta.code_findings` (99) ≠ findings returned (12).** The meta counts
  pre-depth-cap; `quick` truncates the returned array to 12. The meta has no
  `findings_returned` or `capped` flag, so the discrepancy is invisible unless
  the consumer counts.
- **Generic-word flood + the 12-cap truncated the synthesis.** `"subagent"` (71
  matches) and `"register"` (28) produced a wall of single-line hits. All code
  findings carry the fixed 0.85 relevance, so the stable sort preserves insertion
  order and the cap cut at 12. The returned evidence included the
  `code-reviewer` registration (`session/subagents.py:66`) but not the `planner`
  (`:74`) or `debugger` (`:80`) registrations, so the synthesis listed **4 of 6**
  registered agent types. Both missing registrations verified directly in
  `dendrophis/session/subagents.py` (read at 12:55). Truncation, not
  hallucination, degraded the answer — same single-line-snippet class as the
  12:45 item, worse in degree.
- Spot-check passed: `dendrophis/session/session.py:240`
  (`async def invoke_subagent(self, agent, payload, context=None)`) and all four
  `register_handler` lines in `session/subagents.py` confirmed by direct read.

### Handler Drift Since 12:45 (source-verified; 541 → 587 lines)

1. **The 12:45 double-read finding (New Findings #2) is now fixed in source.**
   `_search_codebase` ends with
   `deduplicated_findings = self._deduplicate_findings(codebase_findings)` —
   dedup by `source`, keeping the highest-relevance entry — and the
   `already_processed_files` set now guards **both** the filename-candidate read
   path and the explicit-`context["files"]` read path. The 12:45
   `__main__.py` 0.95+0.6 duplicate pair cannot reproduce on this code path.
   (Source-verified only; no new run exercised it.)
2. The `_deduplicate_findings` static method is new. The remaining line delta
   (~30 lines) is unaccounted drift between sessions; no other structural change
   was identified by re-reading the file in full.

### New / Updated Limitations (12:55)

- **Silent no-op for patternless plain-English tasks** (Run E). A task with no
  backticks, no snake_case identifiers, no file-extension tokens, and a first
  line longer than 4 words derives zero patterns, executes zero searches, and
  reports success with `findings: []`. The only signal is an empty
  `patterns_attempted`. Recommend: record the no-op in `search_meta["errors"]`
  (or log a warning) and/or fall back to a directory listing / glob of the
  scoped path so synthesis has at least structural evidence.
- **No cap indicator in `search_meta`** — `code_findings` is pre-cap; the
  returned array length is the only post-cap number.
- **Quick-cap truncation degrades completeness on broad queries.** 12 single-line
  findings out of 99. "List all X" questions need `depth: thorough` and targeted
  patterns (`register_handler`, not `register`).
- **Fixed 0.85 relevance for code findings** (re-confirms the 11:36 limitation):
  with no semantic ranking, cap ordering = insertion order = sorted-pattern
  order, not importance.
- **Hard-coded project stop-words** in `_derive_search_patterns`
  (`"dendrophis"`, `"boiga"`) — portability smell.
- **CamelCase heuristic misses pure single-word PascalCase** (`Python` is not
  picked up; `invokeSubagent` would be).

### Verdict (12:55)

Plumbing stable across three test sessions. Reliably working: explicit
`patterns` (plus auto-extraction of backticked symbols), `path`/`include`
scoping, honest `search_meta`. Still biting: (1) patternless plain-English tasks
silently no-op; (2) broad generic patterns + the quick cap yield truncated
evidence that synthesis presents as complete (missed 2 of 6 registered agent
types). The 12:45 double-read bug is fixed in source.

**Recommended invocation shape (updated):** one question + targeted explicit
`patterns` (prefer `def X`, `X =`, `register_handler` over bare nouns) +
`path`/`include` + `files` for known paths + `depth: quick` (use `thorough` for
"list all" questions). For plain-English tasks, extract 2–4 symbols yourself and
pass them as `patterns` — the derivation fallback cannot be relied on.

## Evidence (12:55 session)

- Run E / Run F raw responses: this session, 12:55–12:59 (orchestrator tool logs).
- Source: `dendrophis/subagents/handlers/researcher.py` (587 lines, read in full
  2026-08-20 12:59); `dendrophis/session/subagents.py` (118 lines, planner at
  :69–74, debugger at :76–80); `dendrophis/session/session.py:236–245`
  (invoke_subagent signature).


## Follow-up Test — 2026-08-20 13:23

**Context:** Fourth `invoke_subagent` test session. Single invocation (Run G): a plain-English structural recon with only `path`/`include`/`depth` context — no explicit patterns, no backticked symbols, no `files`. Deliberately close in shape to the 12:55 Run E (silent no-op) to test whether the refactor closed that failure mode. Handler drifted again between sessions: 587 → 699 lines (expected — user confirms ongoing test/refactor cycles between sessions).

### Run G — plain-English structural recon, no explicit patterns (SUCCESS)

```
agent="researcher"
task="Recon the dendrophis Python package structure: identify the main entry
      point, list core modules, and note what the top-level README describes
      the project as. Quick pass — just structure and entry points, no deep
      analysis."
context={"path": "dendrophis", "include": "*.py", "depth": "quick"}
```

Result: `status: success`, `confidence: high`, 4 findings:

| Source | Type | Relevance | Origin (source-verified) |
|---|---|---|---|
| `dendrophis/__init__.py` | file | 0.9 | New key-entry-file read (researcher.py:535-554, fixed 0.90, 4000-char preview) |
| `dendrophis/__main__.py` | file | 0.9 | Same block |
| `dendrophis/subagents/handlers/researcher.py:187` | code | 0.85 | Derived pattern `README` — sole match is the handler's own docstring comment (self-referential) |
| `dendrophis` | directory | 0.82 | New structural-context fallback (researcher.py:499-527) |

```json
"search_meta": {
  "patterns_attempted": ["README"],
  "match_counts": {"README": 1},
  "code_findings": 4,
  "errors": [],
  "code_findings_discovered": 4,
  "findings_returned": 4,
  "capped": false
}
```

Synthesis correctly reported the entry flow (`python -m dendrophis` → `__main__.py` → `cli.py:main()`), version `0.7.4`, and the module list — verified against a direct `list_dir` of `dendrophis/` in this session (22 entries, matching the directory finding's "(22 entries)" count exactly). It also honestly flagged that top-level `README.md` content was outside the `path: "dendrophis"` scope instead of guessing — scope-exclusion behaving as documented.

### New Behaviors (source-verified; 587 → 699 lines)

1. **Structural-context fallback** (researcher.py:499-556). When no explicit `context["files"]` are provided, the handler reads the `search_path` (or `.`) as a directory and emits a `type: "directory"` finding (0.82, up to 35-entry preview). For a scoped path (anything other than `.`) it additionally reads `__init__.py` and `__main__.py` when present (fixed 0.90, 4000-char preview). This is exactly the mitigation the 12:55 report recommended ("fall back to a directory listing / glob of the scoped path so synthesis has at least structural evidence"). The Run E silent no-op can no longer fully materialize on scoped structural tasks — zero derived patterns now still yields directory + key-file evidence. (Source-verified; no fresh re-run of Run E's exact task.)
2. **`search_meta` cap indicators** (researcher.py:125-127): `code_findings_discovered` (pre-cap), `findings_returned` (post-cap), `capped` (boolean). Directly closes the 12:55 limitation "No cap indicator in search_meta". Run G: 4/4, `capped: false`.
3. **Line-level relevance boost** (researcher.py:404-410): code lines starting with `def `/`async def `/`class `, containing `register_`, or containing ` = ` score 0.92 vs the 0.85 baseline. Run G's only code finding was a comment line and correctly stayed at 0.85.
4. **Derivation nuance, confirmed empirically**: the task token `README` (all-caps) passes the uppercase test (`any(c.isupper() for c in token[1:])`) — unlike the single-word PascalCase `Python` flagged as missed in the 12:55 report. `dendrophis` was filtered by the hard-coded stop-word list. So the only derived pattern was `README`, and its sole match was the handler's own docstring example comment (researcher.py:187, which mentions `README.md`). The substance of the answer came from the structural fallback, not content search — worth remembering when judging "how well did derivation work" on such runs.

### Prior Limitations Now Closed

| Item (12:55 report) | Status (13:23) |
|---|---|
| Silent no-op for patternless plain-English tasks | Closed (structurally) — scoped path now yields directory + key-file findings; Run E re-run not performed |
| No cap indicator in `search_meta` | Closed — `findings_returned` / `capped` / `code_findings_discovered` present |

Still open from 12:55: fixed baseline relevance (now 0.85/0.92, still non-semantic), hard-coded project stop-words (`"dendrophis"`, `"boiga"`), single-word PascalCase derivation gap, single-shot 120s timeout with no partial output.

### Verdict (13:23)

Plumbing stable across four test sessions. The refactor between 12:55 and 13:23 closed the two most annoying documented gaps: silent structural no-ops and invisible capping. A plain-English structural recon with only `path` scoping now returns a usable, verifiable answer — no explicit patterns needed for "what is the layout" questions. Explicit targeted `patterns` remain required for "where is X implemented" questions, and broad nouns still flood under the quick cap.

### Evidence (13:23 session)

- Run G raw response: this session, 13:23 (orchestrator tool log).
- Source: `dendrophis/subagents/handlers/researcher.py` (699 lines; verified :125-127 cap metadata, :404-410 line relevance, :499-556 structural fallback incl. key-file reads at fixed 0.90).
- Verification: `list_dir dendrophis/` returned 22 entries, matching the directory finding count; `__init__.py` `__version__ = "0.7.4"` confirmed in the Run G finding.
