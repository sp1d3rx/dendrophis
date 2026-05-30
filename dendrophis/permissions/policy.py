"""Permission policy — evaluates tool calls and bash commands against configured rules.

Decision flow for bash:
  1. Simulator flags dangerous  → DENY
  2. Category in denied set     → DENY
  3. Allowed set non-empty and category not in it → DENY
  4. All categories in auto_approve set → ALLOW (skip confirmation)
  5. Falls through to tool-level check  → ALLOW or CONFIRM

Decision flow for other tools:
  1. Tool in denied_tools  → DENY
  2. allowed_tools non-empty and tool not in it → DENY
  3. Tool in require_confirmation → CONFIRM
  4. Otherwise → ALLOW
"""

from __future__ import annotations

import contextlib
from enum import StrEnum
from typing import TYPE_CHECKING

from dendrophis.tools.bash_sandbox import CommandCategory, SimResult

if TYPE_CHECKING:
    from dendrophis.config.schema import DendrophisConfig


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

        categories = sim.categories

        if self._bash_denied:
            blocked = categories & self._bash_denied
            if blocked:
                names = ", ".join(c.value for c in blocked)
                return Decision.DENY, f"denied categories: {names}"

        if self._bash_allowed and not categories.issubset(self._bash_allowed):
            outside = categories - self._bash_allowed
            names = ", ".join(c.value for c in outside)
            return Decision.DENY, f"categories not in allowlist: {names}"

        if self._bash_auto_approve and categories.issubset(self._bash_auto_approve):
            return Decision.ALLOW, ""

        # Fall through to tool-level rule for bash
        decision = self.check_tool("bash")
        return decision, ""

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
