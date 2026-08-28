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


def test_parse_folded_yaml_description(skills_dir: Path) -> None:
    _write_skill(
        skills_dir,
        "caveman.md",
        (
            "---\nname: caveman\ndescription: >\n"
            "  Ultra-compressed communication mode.\n  Cuts token usage ~75%.\n"
            "---\n\nTerse rules.\n"
        ),
    )
    manager = SkillManager(skills_dir)
    manager.load_skills()

    assert "caveman" in manager._all_skills
    caveman_skill = manager._all_skills["caveman"]
    assert "Ultra-compressed communication mode." in caveman_skill.description
    assert ">" not in caveman_skill.description
    assert manager.activate("caveman")
    instructions = manager.get_instructions()
    assert "Terse rules." in instructions


def test_deactivate_skill(skills_dir: Path) -> None:
    _write_skill(
        skills_dir,
        "caveman.md",
        "---\nname: caveman\ndescription: Terse mode\n---\n\nTerse rules.\n",
    )
    manager = SkillManager(skills_dir)
    manager.load_skills()

    assert manager.activate("caveman")
    assert manager.is_active("caveman")
    assert manager.deactivate("caveman")
    assert not manager.is_active("caveman")
    assert manager.get_instructions() == ""
