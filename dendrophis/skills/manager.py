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
    aliases: tuple[str, ...] = ()


@dataclass
class SkillManager:
    """Manages discovery and activation of skills."""

    skills_dir: Path
    active_skills: dict[str, Skill] = field(default_factory=dict)
    _all_skills: dict[str, Skill] = field(default_factory=dict)
    _aliases: dict[str, str] = field(default_factory=dict, repr=False)

    def load_skills(self) -> None:
        """Scan skills directory and parse Markdown files."""
        if not self.skills_dir.exists():
            return

        for md_file in self.skills_dir.glob("*.md"):
            content = md_file.read_text(encoding="utf-8")
            skill = self._parse_skill(content)
            if skill:
                self._all_skills[skill.name] = skill
                for alias in skill.aliases:
                    self._aliases[alias] = skill.name

    def _parse_skill(self, content: str) -> Skill | None:
        """Extract name, description and aliases from Markdown frontmatter."""
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if not fm_match:
            return None

        fm = fm_match.group(1)
        name = self._extract_frontmatter_value(fm, "name")
        if not name:
            return None

        description = self._extract_frontmatter_value(fm, "description")
        if description is None:
            description = ""
        description = description.replace("\\n", "\n")

        aliases = self._extract_aliases(fm)
        return Skill(name=name, description=description, raw_content=content, aliases=aliases)

    def _extract_frontmatter_value(self, fm: str, key: str) -> str | None:
        """Extract a single-line frontmatter value for ``key``."""
        pattern = rf"^{key}:\s*(.*)$"
        match = re.search(pattern, fm, re.MULTILINE)
        if not match:
            return None
        return match.group(1).strip()

    def _extract_aliases(self, fm: str) -> tuple[str, ...]:
        """Parse optional aliases from frontmatter (inline list or YAML block list)."""
        # Inline list: aliases: [a, b, c]
        inline = re.search(r"^aliases:\s*(\[.*?\])\s*$", fm, re.MULTILINE)
        if inline:
            items = re.findall(r"['\"]?([A-Za-z0-9_-]+)['\"]?", inline.group(1))
            return tuple(item.strip() for item in items if item.strip())

        # YAML block list
        block_match = re.search(r"^aliases:\s*$\n((?:\s*-\s*\S+\s*\n)+)", fm, re.MULTILINE)
        if block_match:
            items = re.findall(r"^\s*-\s*(\S+)", block_match.group(1), re.MULTILINE)
            return tuple(item.strip() for item in items if item.strip())

        return ()

    def _resolve_name(self, name: str) -> str:
        """Resolve a skill name or alias to the canonical skill name."""
        if name in self._all_skills:
            return name
        return self._aliases.get(name, name)

    def activate(self, name: str, args: list[str] | None = None) -> bool:
        """Activate a skill by name or alias."""
        canonical = self._resolve_name(name)
        if canonical in self._all_skills:
            self.active_skills[canonical] = self._all_skills[canonical]
            return True
        return False

    def deactivate(self, name: str) -> bool:
        """Deactivate a skill by name or alias."""
        canonical = self._resolve_name(name)
        if canonical in self.active_skills:
            del self.active_skills[canonical]
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
