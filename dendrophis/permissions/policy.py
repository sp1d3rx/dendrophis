"""Permission policy — evaluates tool calls and bash commands against configured rules.

Decision flow for bash:
  1. Simulator flags dangerous  → DENY
  2. Tool-level deny rules (denied_tools / allowed_tools) → DENY
  3. Category in denied set     → DENY
  4. Allowed set non-empty and category not in it → DENY
  5. Non-empty categories all in auto_approve set → ALLOW (skip confirmation)
  6. Otherwise → CONFIRM: a category left at "require confirm" prompts
     even when bash itself is tool-level allowed.

Decision flow for other tools:
  1. Tool in denied_tools  → DENY
  2. allowed_tools non-empty and tool not in it → DENY
  3. Tool in require_confirmation → CONFIRM
  4. Otherwise → ALLOW
"""

from __future__ import annotations

import contextlib
import json
import logging
from enum import StrEnum
from typing import TYPE_CHECKING

from dendrophis.tools.bash_sandbox import BashSandbox, CommandCategory, SimResult

if TYPE_CHECKING:
    from dendrophis.config.schema import DendrophisConfig

logger = logging.getLogger(__name__)


class Decision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    CONFIRM = "confirm"


class PermissionPolicy:
    def __init__(
        self,
        allowed_tools: list[str],
        denied_tools: list[str],
        require_confirmation: list[str],
        bash_allowed: set[CommandCategory],
        bash_denied: set[CommandCategory],
        bash_auto_approve: set[CommandCategory],
    ) -> None:
        self._allowed_tools = set(allowed_tools)
        self._denied_tools = set(denied_tools)
        self._require_confirmation = set(require_confirmation)
        self._bash_allowed = bash_allowed
        self._bash_denied = bash_denied
        self._bash_auto_approve = bash_auto_approve

    # -- Public API ----------------------------------------------------------

    def check_tool(self, tool_name: str) -> Decision:
        """Return decision for a non-bash tool call."""
        if tool_name in self._denied_tools:
            return Decision.DENY
        if self._allowed_tools and tool_name not in self._allowed_tools:
            return Decision.DENY
        if tool_name in self._require_confirmation:
            return Decision.CONFIRM
        return Decision.ALLOW

    def check_bash(self, sim: SimResult) -> tuple[Decision, str]:
        """Return (decision, reason) for a bash command given its SimResult."""
        if sim.dangerous:
            return Decision.DENY, sim.reason

        if "bash" in self._denied_tools:
            return Decision.DENY, "tool 'bash' is in denied_tools"
        if self._allowed_tools and "bash" not in self._allowed_tools:
            return Decision.DENY, "tool 'bash' is not in allowed_tools"

        categories = sim.categories

        if self._bash_denied:
            blocked = categories & self._bash_denied
            if blocked:
                names = ", ".join(category_item.value for category_item in blocked)
                return Decision.DENY, f"denied categories: {names}"

        if self._bash_allowed and (not categories or not categories.issubset(self._bash_allowed)):
            outside = categories - self._bash_allowed
            names = ", ".join(category_item.value for category_item in outside) or "empty"
            return Decision.DENY, f"categories not in allowlist: {names}"

        if categories and self._bash_auto_approve and categories.issubset(self._bash_auto_approve):
            return Decision.ALLOW, ""

        return Decision.CONFIRM, self.explain_bash(sim)

    # -- Explanations ----------------------------------------------------------

    def explain_tool(self, tool_name: str) -> str:
        """Why a non-bash tool call is asking for confirmation."""
        if tool_name in self._require_confirmation:
            return f"tool '{tool_name}' is listed in require_confirmation"
        return f"tool '{tool_name}' requires confirmation"

    def explain_bash(self, sim: SimResult) -> str:
        """Why a bash command is asking for confirmation, given its SimResult."""
        if sim.dangerous:
            return sim.reason or "command flagged as dangerous"
        pending = sim.categories - self._bash_auto_approve
        if pending:
            names = ", ".join(sorted(category_item.value for category_item in pending))
            auto = ", ".join(sorted(category_item.value for category_item in self._bash_auto_approve))
            suffix = f" (auto-approved: {auto})" if auto else ""
            return f"bash command categories not auto-approved: {names}{suffix}"
        return self.explain_tool("bash")

    # -- Factory -------------------------------------------------------------

    @classmethod
    def from_config(cls, config: DendrophisConfig) -> PermissionPolicy:
        permissions = config.permissions
        bash = permissions.bash

        def _parse_categories(category_names: list[str]) -> set[CommandCategory]:
            categories = set()
            for category_name in category_names:
                with contextlib.suppress(ValueError):
                    categories.add(CommandCategory(category_name))
            return categories

        return cls(
            allowed_tools=permissions.allowed_tools,
            denied_tools=permissions.denied_tools,
            require_confirmation=permissions.require_confirmation,
            bash_allowed=_parse_categories(bash.allowed_categories),
            bash_denied=_parse_categories(bash.denied_categories),
            bash_auto_approve=_parse_categories(bash.auto_approve_categories),
        )


def explain_confirmation(config: DendrophisConfig, tool_name: str, arguments: str) -> str:
    """Explain why a tool call is asking for confirmation."""
    policy = PermissionPolicy.from_config(config)
    if tool_name == "bash":
        try:
            parsed = json.loads(arguments) if arguments else {}
            command = parsed.get("command", "") if isinstance(parsed, dict) else ""
            if isinstance(command, str) and command:
                return policy.explain_bash(BashSandbox().simulate(command))
        except Exception as explain_error:
            logger.debug("Falling back to tool-level confirmation reason: %s", explain_error)
    return policy.explain_tool(tool_name)
