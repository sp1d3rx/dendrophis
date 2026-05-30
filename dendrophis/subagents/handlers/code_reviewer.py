"""Code reviewer subagent handler - analyzes code changes for correctness."""

from __future__ import annotations

import json

from dendrophis.config.loader import ConfigLoader
from dendrophis.events import TextDeltaEvent
from dendrophis.llm.client import LLMClient

from ..messages import SubagentRequest, SubagentResponse

SYSTEM_PROMPT = """You are the Code Reviewer subagent for Dendrophis.

Your job is to analyze code changes for correctness, style, and potential issues without modifying files.

Rules:
1. Review diffs for bugs, anti-patterns, security issues
2. Check against project conventions
3. Verify test coverage for changes
4. Suggest improvements, don't implement
5. Approve or request changes

Be specific: line numbers, function names, exact issues.
Distinguish blockers from nits.
Acknowledge good patterns, not just problems.

Output must be valid JSON with approval status and specific issues."""


async def execute(request: SubagentRequest) -> SubagentResponse:
    """Execute code review task."""
    config = ConfigLoader.load().config
    client = LLMClient(config.llm)

    changes = request.payload.get("changes", [])
    context = request.payload.get("context", {})
    focus = request.payload.get("focus", ["correctness", "maintainability"])

    user_prompt = f"""Review the following code changes:

Changes:
{json.dumps(changes, indent=2)}

Context:
{json.dumps(context, indent=2)}

Focus areas: {", ".join(focus)}

Provide a review with approval status, specific issues (with severity), suggestions, and praise for good patterns."""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response_text = ""
        async for event in client.stream_chat(messages):
            if isinstance(event, TextDeltaEvent):
                response_text += event.delta

        # Parse JSON response
        try:
            review = json.loads(response_text)
        except json.JSONDecodeError:
            import re

            json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL)
            if json_match:
                review = json.loads(json_match.group(1))
            else:
                review = {
                    "approval": "needs_discussion",
                    "issues": [{"severity": "warning", "description": "Response was not valid JSON"}],
                    "suggestions": [],
                    "questions": ["Could not parse review output"],
                    "praise": [],
                }

        return SubagentResponse(
            agent=request.agent,
            task_id=request.task_id,
            status="success",
            result=review,
        )

    except Exception as e:
        return SubagentResponse(
            agent=request.agent,
            task_id=request.task_id,
            status="failure",
            result={"error": str(e)},
        )
