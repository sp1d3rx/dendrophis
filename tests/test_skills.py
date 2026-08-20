"""Tests for the skill manager."""

from pathlib import Path

import pytest

from dendrophis.skills.manager import SkillManager


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    return tmp_path


def _write_skill(skills_dir: Path, filename: str, content: str) -> None:
    (skills_dir / filename).write_text(content, encoding="utf-8")


def test_load_skill_with_alias(skills_dir: Path) -> None:
    _write_skill(
        skills_dir,
        "py-style.md",
        "---\nname: py-style\naliases: [py-codestyle, python-style]\n"
        "description: >\n  Python style guide.\n---\n\nRules\n",
    )
    manager = SkillManager(skills_dir)
    manager.load_skills()

    assert manager.list_skills() == ["py-style"]
    assert manager.activate("py-codestyle")
    assert manager.is_active("py-style")
    assert manager.activate("python-style")
    assert manager.is_active("py-style")


def test_activate_by_name_still_works(skills_dir: Path) -> None:
    _write_skill(
        skills_dir,
        "caveman.md",
        "---\nname: caveman\ndescription: >\n  Caveman mode.\n---\n\nRespond terse.\n",
    )
    manager = SkillManager(skills_dir)
    manager.load_skills()

    assert manager.activate("caveman")
    assert manager.is_active("caveman")


def test_unknown_command_returns_false(skills_dir: Path) -> None:
    _write_skill(
        skills_dir,
        "caveman.md",
        "---\nname: caveman\ndescription: >\n  Caveman mode.\n---\n\nRespond terse.\n",
    )
    manager = SkillManager(skills_dir)
    manager.load_skills()

    assert not manager.activate("unknown-skill")
    assert not manager.is_active("caveman")
