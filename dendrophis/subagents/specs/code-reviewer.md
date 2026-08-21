# Code Reviewer Subagent

**Purpose:** Analyze code changes for correctness, robustness, and Pythonic elegance without modifying files.

## Responsibilities
- Review diffs for bugs, race conditions, resource leaks, silent exception swallowing, security issues
- Enforce Hettinger-style Pythonic elegance: concept chunking, descriptive naming, no single-letter variables
- Check against project conventions
- Suggest improvements, don't implement
- Approve, request changes, or leave comments

## Input Schema

All fields are read from the request `payload` (with fallback to `context` for `files`/`file_paths`/`context`):

```json
{
  "task": "Review objective / task description (alias: query)",
  "diff": "unified diff text to review",
  "changes": [{"file": "", "diff": "", "description": ""}],
  "files": ["path/to/file.py"],
  "context": {"conventions": [], "related_tests": [], "original_files": []},
  "focus": ["correctness", "robustness", "maintainability", "pythonic_style"]
}
```

Notes:
- `files` entries are read from disk and embedded in the prompt, truncated to an 8000-character preview; read failures are inlined as `[Error reading: ...]`.
- `focus` defaults to `["correctness", "robustness", "maintainability", "pythonic_style"]` when omitted.
- Keys `diff`, `files`, `file_paths`, `changes`, `focus`, `task`, `query` are stripped from the context section to avoid duplication.

## Output Schema

```json
{
  "approval": "approved | changes_requested | comment",
  "summary": "High-level review assessment summary.",
  "issues": [
    {
      "severity": "blocker | warning | suggestion",
      "file": "path/to/file.py",
      "line": 42,
      "description": "Clear explanation of the problem.",
      "suggestion": "Concrete actionable fix or code snippet."
    }
  ],
  "hettinger_notes": ["Specific Pythonic elegance and naming notes."],
  "greybeard_notes": ["Pragmatic engineering and robustness observations."]
}
```

Severity definitions:
- `blocker`: Critical bugs, data loss risks, race conditions, security vulnerabilities, silent exception swallowing, or non-discard single-letter variable violations. MUST be fixed before landing.
- `warning`: Architectural concerns, unhandled edge cases, performance pitfalls, or significant code smells.
- `suggestion`: Non-blocking Pythonic improvements, cleaner idioms, or readability enhancements.

Parse fallback: if the model response is not valid JSON (raw or in a fenced block), the handler returns a structured wrapper instead:

```json
{
  "approval": "comment",
  "parse_error": true,
  "summary": "<first 500 chars of raw response, or a fixed notice>",
  "issues": [{"severity": "warning", "description": "...", "suggestion": "..."}],
  "hettinger_notes": [],
  "greybeard_notes": [],
  "raw_review": "<full raw model response>"
}
```

On handler failure (e.g. LLM error) the response `status` is `failure` and `result` is `{"error": "..."}`.

## Constraints
- Read-only. Never modify files.
- Be specific: file paths, line numbers, function names, exact issues.
- Distinguish blockers from warnings from suggestions.
- No silent exception swallowing — a mandatory blocker category.
- No non-discard single-letter variable names — a mandatory blocker category (the canonical `_` wildcard is exempt).

## Invocation Pattern
Orchestrator calls code-reviewer when:
- Code-writer completes changes
- User requests review
- Pre-commit validation
- Learning from past mistakes
