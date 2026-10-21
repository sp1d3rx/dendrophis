"""Tests for project-scoped memory: scope helpers, store/searcher filters,
and the save/search/association tools' default scoping behavior."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from dendrophis.memory.memory import MemoryStore
from dendrophis.memory.project import _project_id
from dendrophis.memory.scope import (
    GLOBAL_PROJECT_ID,
    current_project_id,
    default_scope,
    scope_for_project,
)
from dendrophis.memory.search import MemorySearcher


def make_git_repo(base: Path, name: str, remote_url: str) -> Path:
    """Create a minimal fake git repo (just enough for _project_id)."""
    repo = base / name
    git_dir = repo / ".git"
    git_dir.mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    (git_dir / "config").write_text(f'[remote "origin"]\n    url = {remote_url}\n')
    return repo


@pytest.fixture
def store(tmp_path):
    db_file = tmp_path / "scope_test.db"
    return MemoryStore(str(db_file), nlp=None)


@pytest.fixture
def repo_a(tmp_path):
    return make_git_repo(tmp_path, "repo_a", "https://github.com/testuser/proj-a.git")


@pytest.fixture
def repo_b(tmp_path):
    return make_git_repo(tmp_path, "repo_b", "https://github.com/testuser/proj-b.git")


class TestScopeHelpers:
    """current_project_id, default_scope, scope_for_project."""

    def test_current_project_id_from_git_remote(self, repo_a, monkeypatch):
        monkeypatch.chdir(repo_a)
        assert current_project_id() == "github.com/testuser/proj-a"
        assert current_project_id() == _project_id(repo_a)

    def test_default_scope_includes_current_global_and_legacy(self, repo_a, monkeypatch):
        monkeypatch.chdir(repo_a)
        scope = default_scope()
        assert "github.com/testuser/proj-a" in scope
        assert GLOBAL_PROJECT_ID in scope
        assert "" in scope  # legacy unscoped rows stay visible

    def test_scope_for_project_global(self):
        assert scope_for_project(GLOBAL_PROJECT_ID) == [GLOBAL_PROJECT_ID, ""]

    def test_scope_for_project_exact(self):
        assert scope_for_project("some/project") == ["some/project"]


class TestStoreProjectIdsFilter:
    """MemoryStore.list_memories project_ids IN-filter."""

    def test_project_ids_filters_to_set(self, store):
        store.save_memory(content="alpha memory", project_id="proj_a")
        store.save_memory(content="beta memory", project_id="proj_b")
        store.save_memory(content="gamma memory", project_id="proj_c")

        results = store.list_memories(project_ids=["proj_a", "proj_b"])
        contents = {r.content for r in results}
        assert contents == {"alpha memory", "beta memory"}

    def test_project_ids_empty_list_matches_nothing(self, store):
        store.save_memory(content="alpha memory", project_id="proj_a")
        assert store.list_memories(project_ids=[]) == []

    def test_project_ids_includes_legacy_empty(self, store):
        store.save_memory(content="legacy unscoped", project_id="")
        store.save_memory(content="scoped", project_id="proj_a")

        results = store.list_memories(project_ids=["proj_a", ""])
        contents = {r.content for r in results}
        assert contents == {"legacy unscoped", "scoped"}

    def test_project_ids_takes_precedence_over_project_id(self, store):
        store.save_memory(content="alpha memory", project_id="proj_a")
        store.save_memory(content="beta memory", project_id="proj_b")

        results = store.list_memories(project_id="proj_a", project_ids=["proj_b"])
        assert [r.content for r in results] == ["beta memory"]


class TestSearcherProjectIds:
    """MemorySearcher search/search_by_tag project_ids passthrough."""

    def test_search_scoped_to_project_ids(self, store):
        store.save_memory(content="python programming language", project_id="proj_a", tags=["python"])
        store.save_memory(content="python web framework", project_id="proj_b", tags=["python"])

        searcher = MemorySearcher(store)
        results = searcher.search("python programming", project_ids=["proj_a"])
        assert len(results) == 1
        assert results[0].memory.project_id == "proj_a"

        # Unscoped search still sees everything (backward compatible)
        all_results = searcher.search("python programming")
        assert len(all_results) == 2

    def test_search_by_tag_scoped_to_project_ids(self, store):
        store.save_memory(content="python one", project_id="proj_a", tags=["lang"])
        store.save_memory(content="python two", project_id="proj_b", tags=["lang"])

        searcher = MemorySearcher(store)
        results = searcher.search_by_tag("lang", project_ids=["proj_b"])
        assert len(results) == 1
        assert results[0].memory.project_id == "proj_b"


class TestSaveMemoryToolScoping:
    """SaveMemoryTool auto-scopes to the current project."""

    def test_save_defaults_to_current_project(self, store, repo_a, monkeypatch):
        from dendrophis.tools.builtins.memory import SaveMemoryTool

        monkeypatch.chdir(repo_a)
        tool = SaveMemoryTool(store)
        result = asyncio.run(tool.execute(content="a project fact"))

        assert result["success"] is True
        assert result["project_id"] == "github.com/testuser/proj-a"
        entry = store.get_memory(result["memory_id"])
        assert entry is not None
        assert entry.project_id == "github.com/testuser/proj-a"

    def test_save_global_override(self, store, repo_a, monkeypatch):
        from dendrophis.tools.builtins.memory import SaveMemoryTool

        monkeypatch.chdir(repo_a)
        tool = SaveMemoryTool(store)
        result = asyncio.run(tool.execute(content="user preference", project_id="global"))

        assert result["project_id"] == GLOBAL_PROJECT_ID
        entry = store.get_memory(result["memory_id"])
        assert entry is not None
        assert entry.project_id == GLOBAL_PROJECT_ID

    def test_save_explicit_project_override(self, store, repo_a, monkeypatch):
        from dendrophis.tools.builtins.memory import SaveMemoryTool

        monkeypatch.chdir(repo_a)
        tool = SaveMemoryTool(store)
        result = asyncio.run(tool.execute(content="other project fact", project_id="other/proj"))

        assert result["project_id"] == "other/proj"
        entry = store.get_memory(result["memory_id"])
        assert entry is not None
        assert entry.project_id == "other/proj"


class TestSearchMemoryToolScoping:
    """SearchMemoryTool defaults to current project + global tier."""

    def _populate(self, store, repo_a, repo_b):
        store.save_memory(content="alpha project fact about zebra", project_id="github.com/testuser/proj-a")
        store.save_memory(content="beta project fact about zebra", project_id="github.com/testuser/proj-b")
        store.save_memory(content="global fact about zebra", project_id=GLOBAL_PROJECT_ID)

    def test_search_from_repo_a_sees_only_a_and_global(self, store, repo_a, repo_b, monkeypatch):
        from dendrophis.tools.builtins.memory import SearchMemoryTool

        self._populate(store, repo_a, repo_b)
        monkeypatch.chdir(repo_a)
        tool = SearchMemoryTool(store)
        result = asyncio.run(tool.execute(query="zebra fact", limit=10))

        assert result["success"] is True
        projects = {r["project_id"] for r in result["results"]}
        assert "github.com/testuser/proj-a" in projects
        assert GLOBAL_PROJECT_ID in projects
        assert "github.com/testuser/proj-b" not in projects

    def test_search_from_repo_b_sees_only_b_and_global(self, store, repo_a, repo_b, monkeypatch):
        from dendrophis.tools.builtins.memory import SearchMemoryTool

        self._populate(store, repo_a, repo_b)
        monkeypatch.chdir(repo_b)
        tool = SearchMemoryTool(store)
        result = asyncio.run(tool.execute(query="zebra fact", limit=10))

        projects = {r["project_id"] for r in result["results"]}
        assert "github.com/testuser/proj-b" in projects
        assert GLOBAL_PROJECT_ID in projects
        assert "github.com/testuser/proj-a" not in projects

    def test_search_global_only(self, store, repo_a, repo_b, monkeypatch):
        from dendrophis.tools.builtins.memory import SearchMemoryTool

        self._populate(store, repo_a, repo_b)
        monkeypatch.chdir(repo_a)
        tool = SearchMemoryTool(store)
        result = asyncio.run(tool.execute(query="zebra fact", project_id="global", limit=10))

        projects = {r["project_id"] for r in result["results"]}
        assert projects == {GLOBAL_PROJECT_ID}

    def test_search_explicit_other_project(self, store, repo_a, repo_b, monkeypatch):
        from dendrophis.tools.builtins.memory import SearchMemoryTool

        self._populate(store, repo_a, repo_b)
        monkeypatch.chdir(repo_a)
        tool = SearchMemoryTool(store)
        result = asyncio.run(tool.execute(query="zebra fact", project_id="github.com/testuser/proj-b", limit=10))

        projects = {r["project_id"] for r in result["results"]}
        assert projects == {"github.com/testuser/proj-b"}

    def test_search_by_tag_scoped(self, store, repo_a, repo_b, monkeypatch):
        from dendrophis.tools.builtins.memory import SearchMemoryTool

        store.save_memory(content="alpha tagged note", project_id="github.com/testuser/proj-a", tags=["note"])
        store.save_memory(content="beta tagged note", project_id="github.com/testuser/proj-b", tags=["note"])
        store.save_memory(content="global tagged note", project_id=GLOBAL_PROJECT_ID, tags=["note"])

        monkeypatch.chdir(repo_a)
        tool = SearchMemoryTool(store)
        result = asyncio.run(tool.execute(tag="note", limit=10))

        projects = {r["project_id"] for r in result["results"]}
        assert projects == {"github.com/testuser/proj-a", GLOBAL_PROJECT_ID}


class TestAssociationScoping:
    """MemoryAssociationGenerator only surfaces current project + global memories."""

    def _force_trigger(self, generator):
        generator._turns_since_last = 0
        generator._next_trigger = 1

    def test_no_association_when_only_other_project_memories(self, store, repo_a, repo_b, monkeypatch):
        from dendrophis.memory.association import MemoryAssociationGenerator

        store.save_memory(content="python programming language notes", project_id="github.com/testuser/proj-b")
        monkeypatch.chdir(repo_a)

        generator = MemoryAssociationGenerator(store)
        self._force_trigger(generator)
        assert generator.on_turn("python programming language") is None

    def test_association_from_current_project(self, store, repo_a, monkeypatch):
        from dendrophis.memory.association import MemoryAssociationGenerator

        store.save_memory(content="python programming language notes", project_id="github.com/testuser/proj-a")
        monkeypatch.chdir(repo_a)

        generator = MemoryAssociationGenerator(store)
        self._force_trigger(generator)
        event = generator.on_turn("python programming language")
        assert event is not None
        assert "python programming language notes" in event.memory_content

    def test_random_association_scoped(self, store, repo_a, repo_b, monkeypatch):
        from dendrophis.memory.association import MemoryAssociationGenerator

        # Only an unrelated (no ngram overlap) memory in the OTHER project.
        store.save_memory(content="quantum entanglement research", project_id="github.com/testuser/proj-b")
        # And a scoped memory in the current project.
        store.save_memory(content="zebra habitat facts", project_id="github.com/testuser/proj-a")
        monkeypatch.chdir(repo_a)

        generator = MemoryAssociationGenerator(store)
        self._force_trigger(generator)
        event = generator.on_turn("completely different topic xyz")
        # The random fallback must pick from the current project, not proj-b.
        assert event is not None
        assert "zebra habitat facts" in event.memory_content
