"""Debugger subagent handler - diagnoses root causes of failures."""

from __future__ import annotations

import json
import logging

from dendrophis.config.loader import ConfigLoader
from dendrophis.config.schema import DendrophisConfig
from dendrophis.events import TextDeltaEvent
from dendrophis.llm.client import LLMClient

from ..messages import SubagentRequest, SubagentResponse

logger = logging.getLogger(__name__)

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


class DebuggerHandler:
    """Handler for debugger subagent."""

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        config: DendrophisConfig | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._config = config

    async def __call__(self, request: SubagentRequest) -> SubagentResponse:
        return await self.execute(request)

    @property
    def llm(self) -> LLMClient | None:
        """Lazily obtain or create LLM client."""
        if self._llm_client is not None:
            return self._llm_client

        if self._config is not None:
            return LLMClient(self._config.llm)

        try:
            config_loader = ConfigLoader.load()
            return LLMClient(config_loader.config.llm)
        except Exception:
            return None

    async def execute(self, request: SubagentRequest) -> SubagentResponse:
        """Execute debugging task."""
        client = self.llm
        if client is None:
            return SubagentResponse(
                agent=request.agent,
                task_id=request.task_id,
                status="failure",
                result={"error": "LLM client not available for debugger subagent"},
            )

        symptom_description = request.payload.get("symptom", "")
        error_details = request.payload.get("error", {})
        context_data = request.payload.get("context", {})
        artifacts_list = request.payload.get("artifacts", [])

        user_prompt = f"""Debug the following issue:

Symptom: {symptom_description}

Error details:
{json.dumps(error_details, indent=2)}

Context:
{json.dumps(context_data, indent=2)}

Artifacts: {json.dumps(artifacts_list)}

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

        except Exception as debug_error:
            logger.error(f"Debugger execution failed: {debug_error}", exc_info=True)
            endpoint_url = getattr(client._config, "base_url", "unknown endpoint")
            return SubagentResponse(
                agent=request.agent,
                task_id=request.task_id,
                status="failure",
                result={"error": f"LLM endpoint at '{endpoint_url}' unreachable: {debug_error}"},
            )


async def execute(request: SubagentRequest) -> SubagentResponse:
    """Execute debugging task using default handler instance."""
    handler = DebuggerHandler()
    return await handler.execute(request)
