"""Code reviewer subagent handler - analyzes code changes for correctness, robustness, and Pythonic elegance."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from dendrophis.config.schema import DendrophisConfig
from dendrophis.events import TextDeltaEvent
from dendrophis.llm.client import LLMClient

from ..messages import SubagentRequest, SubagentResponse

logger = logging.getLogger(__name__)

CODE_REVIEWER_SYSTEM_PROMPT = """You are Dendrophis CodeReviewer, an elite senior code review subagent
combining the seasoned pragmatism of a Unix Greybeard with the Pythonic elegance of Raymond Hettinger.

Your mission is to perform thorough, rigorous, and constructive code reviews that safeguard system
stability and enforce high craft standards.

### Review Principles:

1. **Greybeard Pragmatism & Robustness**:
   - Hunt for subtle failure modes: race conditions, concurrency traps, unhandled edge cases,
     resource leaks, and silent failures.
   - **NO SILENT EXCEPTION SWALLOWING (MANDATORY BLOCKER)**:
     * Never allow exceptions to be swallowed silently (`except Exception: pass`, bare `except:`,
       or empty catch blocks).
     * All caught exceptions must be logged with context (`logger.exception` / `logger.error(..., exc_info=True)`)
       or re-raised.
   - Reject over-engineering, unnecessary abstractions, and clever hacks. Value simple, explicit,
     bulletproof code that is easy to debug at 3 AM.
   - Check error handling: ensure exceptions carry actionable context and resources are reliably
     cleaned up (using `try/finally` or context managers).
   - Ensure backward compatibility and safe API boundaries.

2. **Raymond Hettinger Pythonic Elegance**:
   - **Concept Chunking**:
     * Structure code into cohesive, bite-sized conceptual chunks (single level of abstraction).
     * One clear thought per function/method. Do not mix high-level workflow with low-level index/string twiddling.
     * Extract complex multi-clause boolean conditions into descriptive predicate functions.
     * Separate data pipeline preparation from business execution using generators and iterables.
   - Write beautiful, idiomatic Python: leverage built-ins, `itertools`, `collections`,
     `contextlib`, `enumerate`, and clean comprehensions.
   - Prefer EAFP (Easier to Ask for Forgiveness than Permission) when appropriate, and avoid repetitive boilerplate.
   - **STRICT SINGLE-LETTER VARIABLE RULE (MANDATORY BLOCKER)**:
     * Flag and REJECT single-letter variable names (such as `i`, `j`, `k`, `x`, `y`, `v`, `e`, `r`)
       under all circumstances (loops, comprehensions, exceptions, lambda parameters).
     * The canonical discard/wildcard identifier `_` (e.g. in unpacking `head, *_ = sequence` or unused loop index)
       is permitted.
     * Demand descriptive names that clearly state what they represent (e.g. `index`, `datum`, `item`,
       `file_path`, `exception_error`, `line_number`).
   - Pedantic variable naming: discourage vague catch-alls like `data`, `item`, `obj`, `stuff`, `thing`.

3. **Issue Categorization**:
   - `blocker`: Critical bugs, data loss risks, race conditions, security vulnerabilities,
     silent exception swallowing, or non-discard single-letter variable violations that MUST be fixed before landing.
   - `warning`: Architectural concerns, unhandled edge cases, performance pitfalls, or significant code smells.
   - `suggestion`: Non-blocking Hettinger Pythonic improvements, cleaner idioms, or readability enhancements.

4. **Output Format**:
   You MUST return a clean JSON object with this exact structure:
   ```json
   {
     "approval": "approved" | "changes_requested" | "comment",
     "summary": "High-level review assessment summary.",
     "issues": [
       {
         "severity": "blocker" | "warning" | "suggestion",
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
"""


class CodeReviewerHandler:
    """Handler for code-reviewer subagent."""

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        config: DendrophisConfig | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._cached_llm: LLMClient | None = llm_client
        self._config = config
        self._logger = logger

    async def __call__(self, request: SubagentRequest) -> SubagentResponse:
        return await self.execute(request)

    @property
    def llm(self) -> LLMClient:
        """Lazily obtain or create LLM client."""
        if self._cached_llm is not None:
            return self._cached_llm

        if self._config is not None:
            self._cached_llm = LLMClient(self._config.llm)
            return self._cached_llm

        from dendrophis.config.loader import ConfigLoader

        config_loader = ConfigLoader.load()
        self._cached_llm = LLMClient(config_loader.config.llm)
        return self._cached_llm

    async def execute(self, request: SubagentRequest) -> SubagentResponse:
        """Execute code review task."""
        changes = request.payload.get("changes", [])
        diff_text = request.payload.get("diff") or (request.context.get("diff") if request.context else "") or ""
        files_list = (
            request.payload.get("files")
            or (request.context.get("files") if request.context else [])
            or (request.context.get("file_paths") if request.context else [])
            or []
        )
        review_context = request.payload.get("context", request.context or {})
        task_instruction = request.payload.get("task") or request.payload.get("query") or ""
        focus_areas = request.payload.get("focus", ["correctness", "robustness", "maintainability", "pythonic_style"])

        review_prompt_sections: list[str] = []

        if task_instruction:
            review_prompt_sections.append(f"Review Objective / Task:\n{task_instruction}")

        if diff_text:
            review_prompt_sections.append(f"Unified Diff to Review:\n```diff\n{diff_text}\n```")

        if changes:
            review_prompt_sections.append(f"Structured Changes:\n{json.dumps(changes, indent=2)}")

        if files_list:
            file_contents_list: list[str] = []
            for target_file_path in files_list:
                resolved_path = Path(target_file_path)
                if resolved_path.exists():
                    try:
                        file_text = resolved_path.read_text(encoding="utf-8")
                        preview_length = 8000
                        content_preview = file_text[:preview_length]
                        if len(file_text) > preview_length:
                            content_preview += f"\n... (+{len(file_text) - preview_length} more characters)"

                        file_contents_list.append(
                            f"--- File: {target_file_path} ---\n{content_preview}\n--- End {target_file_path} ---"
                        )
                    except Exception as read_error:
                        file_contents_list.append(f"--- File: {target_file_path} [Error reading: {read_error}] ---")
            if file_contents_list:
                review_prompt_sections.append("Referenced Files:\n" + "\n\n".join(file_contents_list))

        if review_context:
            filtered_context = {
                context_key: context_value
                for context_key, context_value in review_context.items()
                if context_key not in ("diff", "files", "file_paths", "changes", "focus", "task", "query")
            }
            if filtered_context:
                review_prompt_sections.append(f"Context / Conventions:\n{json.dumps(filtered_context, indent=2)}")

        review_prompt_sections.append(f"Focus Areas: {', '.join(focus_areas)}")
        review_prompt_sections.append(
            "Please analyze the provided changes and return your review as a structured JSON object."
        )

        user_content = "\n\n".join(review_prompt_sections)

        messages = [
            {"role": "system", "content": CODE_REVIEWER_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        try:
            response_text = ""
            async for event in self.llm.stream_chat(messages):
                if isinstance(event, TextDeltaEvent):
                    response_text += event.delta

            review_payload = self._parse_review_json(response_text)

            return SubagentResponse(
                agent=request.agent,
                task_id=request.task_id,
                status="success",
                result=review_payload,
            )

        except Exception as review_error:
            self._logger.error(f"Code review failed: {review_error}", exc_info=True)
            return SubagentResponse(
                agent=request.agent,
                task_id=request.task_id,
                status="failure",
                result={"error": str(review_error)},
            )

    def _parse_review_json(self, response_text: str) -> dict[str, Any]:
        """Extract and parse structured review JSON from model response."""
        try:
            return json.loads(response_text)
        except json.JSONDecodeError:
            pass

        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Fallback structured wrapper with explicit parse_error flag
        return {
            "approval": "comment",
            "parse_error": True,
            "summary": response_text[:500] if response_text else "Review completed without valid JSON format.",
            "issues": [
                {
                    "severity": "warning",
                    "description": "Model response could not be parsed into strict JSON format.",
                    "suggestion": "Inspect raw_review field for raw model commentary.",
                }
            ],
            "hettinger_notes": [],
            "greybeard_notes": [],
            "raw_review": response_text,
        }


async def execute(request: SubagentRequest) -> SubagentResponse:
    """Entry point for code reviewer subagent."""
    handler = CodeReviewerHandler()
    return await handler.execute(request)
