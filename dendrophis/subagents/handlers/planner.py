"""Planner subagent handler - decomposes tasks into executable steps."""

from __future__ import annotations

import contextlib
import json
import logging
import re

from dendrophis.config.loader import ConfigLoader
from dendrophis.config.schema import DendrophisConfig
from dendrophis.events import ErrorEvent, TextDeltaEvent
from dendrophis.llm.client import LLMClient

from ..messages import SubagentRequest, SubagentResponse

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Planner subagent for Dendrophis.

Your job is to break complex tasks into executable steps and determine agent assignments.

Rules:
1. Analyze task complexity and dependencies
2. Decompose into atomic, ordered steps
3. Assign each step to the appropriate subagent
4. Identify required context for each step
5. Produce a clear execution plan

Available subagents:
- researcher: Gather and synthesize information (read-only)
- code-writer: Implement changes precisely (modifies files)
- code-reviewer: Review changes for correctness (read-only)
- test-runner: Execute tests, analyze failures (read-only)
- debugger: Diagnose root causes (read-only)

CRITICAL: You must respond with ONLY valid JSON. No markdown, no explanations, no code blocks. Just raw JSON.

Output format:
{
  "steps": [
    {
      "order": 1,
      "agent": "researcher",
      "instruction": "Find relevant files",
      "inputs": {},
      "outputs": {},
      "dependencies": []
    }
  ],
  "parallel_groups": [[1]],
  "estimated_cost": "low|medium|high",
  "risks": ["what could go wrong"]
}
"""


def _extract_json(text: str) -> dict | None:
    """Extract JSON from text using multiple strategies."""
    text = text.strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    patterns = [
        r"```json\s*(\{.*?\})\s*```",
        r"```\s*(\{.*?\})\s*```",
        r"(\{[\s\S]*\})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1))
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

    return None


class PlannerHandler:
    """Handler for planner subagent."""

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        config: DendrophisConfig | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._owned_llm_clients: set[LLMClient] = set()
        self._config = config

    async def __call__(self, request: SubagentRequest) -> SubagentResponse:
        return await self.execute(request)

    @property
    def llm(self) -> LLMClient | None:
        """Lazily obtain or create LLM client (cached; closed by aclose)."""
        if self._llm_client is not None:
            return self._llm_client

        if self._config is not None:
            self._llm_client = LLMClient(self._config.llm)
            self._owned_llm_clients.add(self._llm_client)
            return self._llm_client

        try:
            config_loader = ConfigLoader.load()
            self._llm_client = LLMClient(config_loader.config.llm)
            self._owned_llm_clients.add(self._llm_client)
            return self._llm_client
        except Exception as exc:
            logger.debug("Could not create LLM client from default config: %s", exc)
            return None

    async def aclose(self) -> None:
        """Close LLM clients this handler created itself."""
        for client in self._owned_llm_clients:
            with contextlib.suppress(Exception):
                await client.aclose()
        if self._llm_client in self._owned_llm_clients:
            self._llm_client = None
        self._owned_llm_clients.clear()

    async def execute(self, request: SubagentRequest) -> SubagentResponse:
        """Execute planner task."""
        client = self.llm
        if client is None:
            return SubagentResponse(
                agent=request.agent,
                task_id=request.task_id,
                status="failure",
                result={"error": "LLM client not available for planner subagent"},
            )

        task_description = request.payload.get("task", "")
        constraints = request.payload.get("constraints", {})
        context_data = {**(request.context or {}), **(request.payload.get("context") or {})}

        user_prompt = f"""Task: {task_description}

Constraints: {json.dumps(constraints, indent=2)}

Context: {json.dumps(context_data, indent=2)}

Provide a plan with steps, agent assignments, dependencies, and risk assessment."""

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        response_text = ""
        error_messages: list[str] = []

        try:
            async for event in client.stream_chat(messages):
                if isinstance(event, TextDeltaEvent):
                    response_text += event.delta
                elif isinstance(event, ErrorEvent):
                    error_messages.append(event.message)
        except Exception as execution_error:
            endpoint_url = getattr(client._config, "base_url", "unknown endpoint")
            error_messages.append(f"LLM endpoint at '{endpoint_url}' unreachable: {execution_error}")

        if error_messages:
            return SubagentResponse(
                agent=request.agent,
                task_id=request.task_id,
                status="failure",
                result={"error": "; ".join(error_messages)},
            )

        if not response_text.strip():
            return SubagentResponse(
                agent=request.agent,
                task_id=request.task_id,
                status="failure",
                result={"error": "No response from LLM"},
            )

        plan = _extract_json(response_text)
        if plan is None:
            plan = {
                "steps": [{"order": 1, "agent": "code-writer", "instruction": response_text[:500]}],
                "parallel_groups": [[1]],
                "estimated_cost": "medium",
                "risks": ["Response was not valid JSON"],
            }

        return SubagentResponse(
            agent=request.agent,
            task_id=request.task_id,
            status="success",
            result=plan,
        )


async def execute(request: SubagentRequest) -> SubagentResponse:
    """Execute planner task using default handler instance."""
    handler = PlannerHandler()
    return await handler.execute(request)
