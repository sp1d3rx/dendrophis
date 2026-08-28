"""Planner subagent handler - decomposes tasks into executable steps."""

from __future__ import annotations

import json

from dendrophis.config.loader import ConfigLoader
from dendrophis.config.schema import DendrophisConfig
from dendrophis.events import ErrorEvent, TextDeltaEvent
from dendrophis.llm.client import LLMClient

from ..messages import SubagentRequest, SubagentResponse

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
    import re

    text = text.strip()

    # Strategy 1: Direct JSON parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Extract from markdown code block
    patterns = [
        r"```json\s*(\{.*?\})\s*```",
        r"```\s*(\{.*?\})\s*```",
        r"(\{[\s\S]*\})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
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
        context_data = request.payload.get("context", {})

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

        # Parse JSON response - try multiple strategies
        plan = _extract_json(response_text)
        if plan is None:
            # Fallback: wrap raw text as single-step plan
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
