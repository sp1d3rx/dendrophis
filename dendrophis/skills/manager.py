"""Skill manager for loading and activating Markdown-based skills."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml import YAML

logger = logging.getLogger(__name__)


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

        for md_file in sorted(self.skills_dir.glob("*.md")):
            content = md_file.read_text(encoding="utf-8")
            skill = self._parse_skill(content)
            if skill:
                self._all_skills[skill.name] = skill
                for alias in skill.aliases:
                    self._aliases[alias] = skill.name

    def _parse_skill(self, content: str) -> Skill | None:
        """Extract name, description and aliases from Markdown frontmatter."""
        frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if not frontmatter_match:
            return None

        frontmatter_text = frontmatter_match.group(1)
        try:
            yaml_parser = YAML(typ="safe")
            frontmatter_data = yaml_parser.load(frontmatter_text)
            if isinstance(frontmatter_data, dict):
                skill_name = str(frontmatter_data.get("name", "")).strip()
                if not skill_name:
                    return None
                skill_description = str(frontmatter_data.get("description", "")).strip()
                raw_aliases = frontmatter_data.get("aliases", ())
                if isinstance(raw_aliases, list):
                    aliases = tuple(str(alias_item).strip() for alias_item in raw_aliases if str(alias_item).strip())
                elif isinstance(raw_aliases, str):
                    aliases = (raw_aliases.strip(),)
                else:
                    aliases = ()
                return Skill(
                    name=skill_name,
                    description=skill_description,
                    raw_content=content,
                    aliases=aliases,
                )
        except Exception as yaml_error:
            logger.debug("Falling back to regex skill frontmatter parser: %s", yaml_error)

        skill_name = self._extract_frontmatter_value(frontmatter_text, "name")
        if not skill_name:
            return None

        skill_description = self._extract_frontmatter_value(frontmatter_text, "description")
        if skill_description is None:
            skill_description = ""
        skill_description = skill_description.replace("\\n", "\n")

        aliases = self._extract_aliases(frontmatter_text)
        return Skill(name=skill_name, description=skill_description, raw_content=content, aliases=aliases)

    def _extract_frontmatter_value(self, fm: str, key: str) -> str | None:
        """Extract a single-line frontmatter value for ``key``."""
        pattern = rf"^{key}:\s*(.*)$"
        match = re.search(pattern, fm, re.MULTILINE)
        if not match:
            return None
        return match.group(1).strip()

    def _extract_aliases(self, fm: str) -> tuple[str, ...]:
        """Parse optional aliases from frontmatter (inline list or YAML block list)."""
        inline = re.search(r"^aliases:\s*(\[.*?\])\s*$", fm, re.MULTILINE)
        if inline:
            items = re.findall(r"['\"]?([A-Za-z0-9_-]+)['\"]?", inline.group(1))
            return tuple(item.strip() for item in items if item.strip())

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
            parts = skill.raw_content.split("---", 2)
            if len(parts) >= 3:
                instructions.append(parts[2].strip())
            elif len(parts) >= 2:
                instructions.append(parts[1].strip())
            else:
                instructions.append(skill.raw_content)

        return "\n".join(instructions)

    def list_skills(self) -> list[str]:
        """Return names of all discovered skills."""
        return sorted(self._all_skills.keys())

    def is_active(self, name: str) -> bool:
        return self._resolve_name(name) in self.active_skills
