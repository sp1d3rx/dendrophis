"""Skill manager for loading and activating Markdown-based skills."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Skill:
    """Metadata for a loaded skill."""

    name: str
    description: str
    raw_content: str


@dataclass
class SkillManager:
    """Manages discovery and activation of skills."""

    skills_dir: Path
    active_skills: dict[str, Skill] = field(default_factory=dict)
    _all_skills: dict[str, Skill] = field(default_factory=dict)

    def load_skills(self) -> None:
        """Scan skills directory and parse Markdown files."""
        if not self.skills_dir.exists():
            return

        for md_file in self.skills_dir.glob("*.md"):
            content = md_file.read_text(encoding="utf-8")
            skill = self._parse_skill(content)
            if skill:
                self._all_skills[skill.name] = skill

    def _parse_skill(self, content: str) -> Skill | None:
        """Extract name and description from Markdown frontmatter."""
        # Look for YAML-like block at start of file
        match = re.search(r"^---\s*\nname:\s*(.*?)\ndescription:\s*>\s*(.*?)\n---", content, re.DOTALL)
        if not match:
            # Fallback to simpler pattern if the block is slightly different
            match = re.search(r"^---\s*\nname:\s*(.*?)\ndescription:\s*(.*?)\n---", content, re.DOTALL)

        if match:
            name = match.group(1).strip()
            description = match.group(2).strip().replace("\\n", "\n")
            return Skill(name=name, description=description, raw_content=content)
        return None

    def activate(self, name: str, args: list[str] | None = None) -> bool:
        """Activate a skill by name."""
        if name in self._all_skills:
            self.active_skills[name] = self._all_skills[name]
            return True
        return False

    def deactivate(self, name: str) -> bool:
        """Deactivate a skill by name."""
        if name in self.active_skills:
            del self.active_skills[name]
            return True
        return False

    def get_instructions(self) -> str:
        """Combine all active skill contents into a single instruction block."""
        if not self.active_skills:
            return ""

        instructions = ["\n# Active Skills Instructions\n"]
        for skill in self.active_skills.values():
            # We want the rules part, usually after the frontmatter
            # Split only twice to handle frontmatter (--- content ---) and ignore
            # any additional --- in the content itself
            parts = skill.raw_content.split("---", 2)
            if len(parts) >= 3:
                # Skill has frontmatter, take content after closing ---
                instructions.append(parts[2].strip())
            elif len(parts) >= 2:
                # No frontmatter but has --- in content, take after first ---
                instructions.append(parts[1].strip())
            else:
                # No --- at all, take entire content
                instructions.append(skill.raw_content)

        return "\n".join(instructions)

    def list_skills(self) -> list[str]:
        """Return names of all discovered skills."""
        return sorted(self._all_skills.keys())

    def is_active(self, name: str) -> bool:
        return name in self.active_skills
