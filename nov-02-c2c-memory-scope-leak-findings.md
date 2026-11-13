# c2c memory scope leak — findings (2026-11-02)

## Symptom
`search_memory` from a boiga (dendrophis) session surfaced c2c project memories.
c2c is a distinct project; its memories should not be in boiga's default scope.

## Root cause (confirmed)
- All 13 c2c memories carry `project_id = "github.com/sp1d3rx/dendrophis"` —
  boiga's own ID (verified in `~/.config/dendrophis/memory.db`).
- `default_scope()` for boiga = `[boiga_id, "global", ""]`, so those rows are
  inside the visible scope. Search/filter code is correct (21/21 scope tests pass);
  the data was mis-scoped at save time.

## Why they got boiga's ID
c2c is a **nested git repo** at `boiga/projects/c2c` (own `.git`, **no remote**).
- `_project_id(boiga)` -> `github.com/sp1d3rx/dendrophis`
- `_project_id(c2c)`   -> `local:/Users/derekw/Documents/projects/boiga/projects/c2c`
- `current_project_id()` = `_project_id(detect_project_root())`; detection walks UP
  from CWD to nearest `.git`. Memories stamped boiga => the saving session resolved
  its root to boiga, not c2c. Plausible mechanisms (session CWD history not
  recoverable, so not overcommitted):
  1. c2c had no `.git` yet when early memories were saved (c2c `.git` created
     ~Oct 22-26; memories span Oct 22 11:23 -> Oct 23 13:33) -> walk-up lands on boiga.
  2. Session CWD was boiga root, not inside c2c.

## Contributing design gaps
1. **Nested-repo identity ambiguity** — c2c inside boiga; a boiga-rooted session
   silently scopes c2c work to boiga.
2. **No-remote repos get path-bound `local:` IDs** — stable only while the dir
   stays put; embeds the operator's username in memory data.
3. **No backfill / no validation** — post-scoping rows saved with a mis-resolved
   root are silently mis-stamped; nothing checks scope vs. actual work location.
4. **No c2c primer** — only the boiga primer exists on disk; c2c was treated as
   "part of boiga" all along.

## Remediation options
A. **Data-only migration (minimal, immediate):** re-scope the 13 c2c rows to a
   distinct c2c project_id. Requires choosing c2c's canonical ID (see B).
B. **Give c2c a stable identity:** add a git remote (even a local bare or a
   private GitHub) so `_project_id(c2c)` returns a clean `github.com/...` ID
   instead of a path-bound `local:` ID. Then migrate (A) to that ID.
C. **Code hardening (prevents recurrence):**
   - Detect nested repos and surface the ambiguity (warn / prefer innermost).
   - Add a save-time sanity check: if CWD is inside a different repo than the
     scope being written, log a warning.
   - Optional: a one-shot backfill command that re-derives project_id for rows
     whose stored scope no longer matches a resolvable root.
D. **Accept status quo** — treat c2c as part of boiga (no change). Not
   recommended given the user's explicit "different project" framing.

## Decision needed
- Which canonical ID for c2c? (clean remote-based vs. keep `local:` path ID)
- Scope of work: data migration only (A), or + identity (B), or + hardening (C)?
