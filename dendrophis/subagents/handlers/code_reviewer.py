"""Code reviewer subagent handler - analyzes code changes for correctness, robustness, and Pythonic elegance."""

from __future__ import annotations

import dataclasses
import json
import logging
import re
from pathlib import Path
from typing import Any

from dendrophis.config.schema import DendrophisConfig, LLMConfig
from dendrophis.events import TextDeltaEvent
from dendrophis.llm.client import LLMClient

from ..messages import SubagentRequest, SubagentResponse

logger = logging.getLogger(__name__)

CODE_REVIEWER_SYSTEM_PROMPT = """You are a senior code reviewer: seasoned greybeard pragmatism, Hettinger-level
Python taste, laid-back delivery. Find the real problems and state them plainly.

Review lens:
- Robustness: race conditions, resource leaks, unhandled edge cases, silent
  failures. Silent exception swallowing is a BLOCKER — every caught exception
  must be logged with context or re-raised.
- Pythonic craft: one job per function, descriptive names that say what they
  hold, built-ins and idioms, EAFP where natural. Single-letter variable
  names (i, x, e, ...) are a BLOCKER; the `_` wildcard is fine.

Severity:
- blocker: bugs, data loss, races, security, silent exception swallowing,
  single-letter names. Must fix before landing.
- warning: edge cases, performance pitfalls, architectural smells.
- suggestion: non-blocking idiom and readability improvements.

Take the time you need to read the code carefully. Keep the answer tight:
findings and fixes only, in plain words.
Limits: at most 3 issues, one sentence each. hettinger_notes and
greybeard_notes: at most 2 short items each.

Respond with a single JSON object and nothing else:
```json
{
  "approval": "approved" | "changes_requested" | "comment",
  "summary": "One or two sentences.",
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
        self._dedicated_llm_client: LLMClient | None = None
        self._config = config
        self._logger = logger

    async def __call__(self, request: SubagentRequest) -> SubagentResponse:
        return await self.execute(request)

    @property
    def llm(self) -> LLMClient:
        """Lazily obtain or create LLM client; uses a dedicated client when code_reviewer_model is configured."""
        if self._config is not None and self._config.llm.code_reviewer_model:
            if self._dedicated_llm_client is None:
                self._dedicated_llm_client = LLMClient(self._get_llm_config())
            return self._dedicated_llm_client

        if self._cached_llm is not None:
            return self._cached_llm

        if self._config is not None:
            self._cached_llm = LLMClient(self._config.llm)
            return self._cached_llm

        from dendrophis.config.loader import ConfigLoader

        config_loader = ConfigLoader.load()
        cfg = config_loader.config
        reviewer_llm_config = dataclasses.replace(cfg.llm, model=cfg.llm.code_reviewer_model or cfg.llm.model)
        self._cached_llm = LLMClient(reviewer_llm_config)
        return self._cached_llm

    def _get_llm_config(self) -> LLMConfig:
        """Get LLM config for code-reviewer, honouring the code_reviewer_model override."""
        if self._config is None:
            raise ValueError("No config available to create LLM client")
        llm_config = self._config.llm
        return dataclasses.replace(llm_config, model=llm_config.code_reviewer_model or llm_config.model)

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
