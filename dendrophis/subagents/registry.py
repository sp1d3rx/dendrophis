"""Subagent registry for managing agent definitions and capabilities."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar


@dataclass(frozen=True)
class AgentDefinition:
    """Definition of a subagent."""

    name: str
    description: str
    modifies_files: bool
    spec_path: Path
    handler: Callable | None = None  # Set at runtime


class SubagentRegistry:
    """Registry of available subagents."""

    _agents: ClassVar[dict[str, AgentDefinition]] = {}

    def __init__(self, specs_dir: Path | None = None):
        self.specs_dir = specs_dir or Path(__file__).parent / "specs"
        self._load_specs()

    def _load_specs(self) -> None:
        """Load agent definitions from spec files."""
        # Hardcoded for now — could parse from markdown
        self._agents = {
            "researcher": AgentDefinition(
                name="researcher",
                description="Gather and synthesize information",
                modifies_files=False,
                spec_path=self.specs_dir / "researcher.md",
            ),
            "planner": AgentDefinition(
                name="planner",
                description="Decompose tasks into executable steps",
                modifies_files=False,
                spec_path=self.specs_dir / "planner.md",
            ),
            "code-writer": AgentDefinition(
                name="code-writer",
                description="Implement changes precisely",
                modifies_files=True,
                spec_path=self.specs_dir / "code-writer.md",
            ),
            "code-reviewer": AgentDefinition(
                name="code-reviewer",
                description="Review changes for correctness",
                modifies_files=False,
                spec_path=self.specs_dir / "code-reviewer.md",
            ),
            "test-runner": AgentDefinition(
                name="test-runner",
                description="Execute tests, analyze failures",
                modifies_files=False,
                spec_path=self.specs_dir / "test-runner.md",
            ),
            "debugger": AgentDefinition(
                name="debugger",
                description="Diagnose root causes",
                modifies_files=False,
                spec_path=self.specs_dir / "debugger.md",
            ),
        }

    def get(self, name: str) -> AgentDefinition | None:
        """Get agent definition by name."""
        return self._agents.get(name)

    def list_agents(self) -> list[str]:
        """List all available agent names."""
        return list(self._agents.keys())

    def get_file_modifiers(self) -> list[str]:
        """Get names of agents that modify files."""
        return [a.name for a in self._agents.values() if a.modifies_files]

    def register_handler(self, name: str, handler: Callable) -> None:
        """Register a handler for an agent."""
        if name not in self._agents:
            raise ValueError(f"Unknown agent: {name}")
        # Create new definition with handler
        old = self._agents[name]
        self._agents[name] = AgentDefinition(
            name=old.name,
            description=old.description,
            modifies_files=old.modifies_files,
            spec_path=old.spec_path,
            handler=handler,
        )


# Global registry instance
_registry: SubagentRegistry | None = None


def get_registry() -> SubagentRegistry:
    """Get or create global registry."""
    global _registry
    if _registry is None:
        _registry = SubagentRegistry()
    return _registry
