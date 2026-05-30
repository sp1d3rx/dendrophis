"""Debugger subagent handler - diagnoses root causes of failures."""

from __future__ import annotations

import json

from dendrophis.config.loader import ConfigLoader
from dendrophis.events import TextDeltaEvent
from dendrophis.llm.client import LLMClient

from ..messages import SubagentRequest, SubagentResponse

SYSTEM_PROMPT = """You are the Debugger subagent for Dendrophis.

Your job is to diagnose root causes of failures and recommend fixes.

Rules:
1. Analyze error traces, logs, and state
2. Reproduce issues consistently (mentally)
3. Isolate minimal failing case
4. Identify root cause (not just symptoms)
5. Recommend fix strategy, don't implement

Distinguish code bugs from environment/config issues.
Verify reproduction before claiming root cause.
Cite specific evidence for all claims.

Output must be valid JSON with root cause, location, and fix strategy."""


async def execute(request: SubagentRequest) -> SubagentResponse:
    """Execute debugging task."""
    config = ConfigLoader.load().config
    client = LLMClient(config.llm)

    symptom = request.payload.get("symptom", "")
    error = request.payload.get("error", {})
    context = request.payload.get("context", {})
    artifacts = request.payload.get("artifacts", [])

    user_prompt = f"""Debug the following issue:

Symptom: {symptom}

Error details:
{json.dumps(error, indent=2)}

Context:
{json.dumps(context, indent=2)}

Artifacts: {json.dumps(artifacts)}

Provide a diagnosis with root cause, location, reproduction steps, error analysis, and fix strategy."""

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
            diagnosis = json.loads(response_text)
        except json.JSONDecodeError:
            import re

            json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL)
            if json_match:
                diagnosis = json.loads(json_match.group(1))
            else:
                diagnosis = {
                    "root_cause": "Could not parse debugger output",
                    "location": {"file": "", "line": 0, "function": ""},
                    "reproduction": "N/A",
                    "analysis": response_text[:500],
                    "fix_strategy": "Review the error manually",
                    "confidence": "low",
                    "related_issues": [],
                }

        return SubagentResponse(
            agent=request.agent,
            task_id=request.task_id,
            status="success",
            result=diagnosis,
        )

    except Exception as e:
        return SubagentResponse(
            agent=request.agent,
            task_id=request.task_id,
            status="failure",
            result={"error": str(e)},
        )
