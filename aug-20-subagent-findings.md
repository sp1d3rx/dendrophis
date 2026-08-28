# Subagent Test Findings — 2026-08-20

Test session for the `invoke_subagent` tool, boiga root workspace.
Goal: exercise subagent dispatch and record behaviors for follow-up fixing.

## Environment

- Repo: boiga root (contains `dendrophis/` package, `twitch-chat/`, fossil VCS)
- Config: `dendrophis.yaml`
  - LLM endpoint: `http://127.0.0.1:8005/v1` (OpenAI-compatible), `api_key: none`
  - Model: `gemma-4-26B-A4B-it`, `code_writer_model: null` (fallback to default)
  - `context_limit: 262144`, `compaction_threshold: 0.85`, `max_tokens: 16384`, `temperature: 1.0`

## Subagent implementation layout (reference)

`dendrophis/subagents/`:
- `executor.py`, `messages.py`, `registry.py`, `specs/`
- `handlers/`: `code_reviewer.py`, `code_writer.py`, `debugger.py`, `planner.py`, `researcher.py`, `test_runner.py`

## Test 1 — researcher (SUCCESS, low confidence)

Dispatch:
- agent: `researcher`
- task: survey the `dendrophis/` package (modules, subsystems, subagent dispatch, config roles)
- context: `{"files": ["dendrophis/", "dendrophis.yaml", "AGENTS.md"]}`

Result:
- Returned structured JSON: `findings[]` (source, relevance, summary, type), `synthesis`, `gaps: []`, `confidence: "low"`.
- No error, `status: "success"`.

Issues observed:

1. **Directory context entries are not expanded.** I passed `"dendrophis/"` (a directory) in `context.files`, but the researcher only saw the two concrete files (`dendrophis.yaml`, `AGENTS.md`). Its synthesis claims "actual `dendrophis/` source tree is missing" and "no Python source ... under `dendrophis/` is included in the evidence" — both factually wrong. Direct verification (`list dendrophis/`) shows 22 entries including `__init__.py`, `__main__.py`, `cli.py`, `config/`, `llm/`, `subagents/`, `events/`, `tools/`, `memory/`, `skills/`, `ui/`, `web/`, etc.
   - Possible causes to check:
     - `context.files` entries that are directories are skipped / not traversed by the researcher's context loader;
     - the researcher only reads file contents (no directory-listing capability), so it cannot infer tree structure.
   - Suggested fix: expand directory entries into a file listing (possibly recursive with a depth limit) when building researcher context, or document that `context.files` only accepts files and give the researcher a way to list directories itself.

2. **`gaps: []` is misleading.** The synthesis explicitly lists knowledge gaps ("actual `dendrophis/` source tree is missing", "Cannot verify: top-level modules, entry points, ...") yet the `gaps` field is an empty list. Either populate `gaps` from the synthesis or drop the field.

3. **Memory search works and is useful.** Findings include relevant memory hits (twitch-chat workflow, caveman spec) with `type: "memory"` — expected, good behavior.

4. `confidence: "low"` is correctly honest given the limited evidence.

## Test 2 — planner (FAILURE: connection error)

Dispatch:
- agent: `planner`
- task: short implementation plan for a generic `--version` CLI flag (no repo files, no edits)
- context: `{}`

Result:
- `success: false`
- `error: "Connection error: All connection attempts failed"`

Root-cause investigation:
- Probed `http://127.0.0.1:8005/v1/models` → `URLError: <urlopen error [Errno 61] Connection refused>`.
- Interpretation: the planner handler appears to make an LLM call (unlike researcher, which completed fine using only search + memory). With the local LLM endpoint refusing connections at test time, the planner's LLM call failed and the whole dispatch surfaced a connection error.
- Uncertainty: the parent agent session was functioning normally during this same window, so either the parent uses a different endpoint / session state, or the server went down between calls. This test alone cannot distinguish the two.
- Suggested fixes to consider:
  - Surface a clearer error: "LLM endpoint at `<base_url>` unreachable" instead of the generic "All connection attempts failed".
  - Retry / health-check before dispatch; or mark which subagents require LLM vs. search-only, and fail fast with a useful message.
  - Verify the `code_writer_model: null` fallback chain (planner likely shares it) actually reaches a live endpoint.

## Sandbox / tooling notes (adjacent findings)

1. **bash blocks `/dev` paths.** `curl -s -o /dev/null ...` was rejected with: "Access to /dev blocked in command: ...". Known rule (also recorded from an earlier twitch-chat session in memory). Workaround: use `execute_code` (Python `urllib`/`socket`) for network checks.
2. `execute_code` handles network probing fine (used for the endpoint check above).

## Questions for the fixer

1. Should `context.files` accept directories? If so, where is the context builder that should expand them (researcher handler vs. registry/executor)?
2. Where does the planner get its endpoint from, and why did it fail while the parent session was still operating?
3. Should `gaps` be populated by the researcher, or removed from the result schema?

## Repro

1. Researcher (works): `invoke_subagent(agent="researcher", task="survey X", context={"files": [...]})`.
2. Planner (fails while 127.0.0.1:8005 is down): any planner dispatch.
3. Endpoint probe: `urllib.request.urlopen("http://127.0.0.1:8005/v1/models")` → Errno 61, Connection refused.
