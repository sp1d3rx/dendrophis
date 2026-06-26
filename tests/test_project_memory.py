"""Tests for the project primer system."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from dendrophis.memory.project import (
    _EXTENSION_MAP,
    FileEntry,
    ProjectPrimer,
    _hash_file,
    _project_id,
    delete_primer,
    detect_project_root,
    list_primers,
    load_primer,
    save_primer,
)

# Fixtures


@pytest.fixture
def temp_primer_dir():
    """Create a temporary primer directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Monkey-patch the primer directory
        original = Path.home() / ".config" / "dendrophis" / "primers"
        import dendrophis.memory.project as project_module

        project_module._PRIMER_DIR = Path(tmpdir)
        yield Path(tmpdir)
        project_module._PRIMER_DIR = original


# Tests for _project_id


class TestProjectId:
    """Tests for project ID generation."""

    def test_project_id_git_repo(self, tmp_path):
        """Git repo uses remote origin as ID."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        config_path = git_dir / "config"
        config_path.write_text('[remote "origin"]\n    url = https://github.com/user/repo.git\n')
        head = git_dir / "HEAD"
        head.write_text("ref: refs/heads/main\n")

        project_id = _project_id(tmp_path)
        assert project_id == "github.com/user/repo"

    def test_project_id_git_repo_ssh(self, tmp_path):
        """Git repo with SSH URL parsed correctly."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        config_path = git_dir / "config"
        config_path.write_text('[remote "origin"]\n    url = git@github.com:user/repo.git\n')
        head = git_dir / "HEAD"
        head.write_text("ref: refs/heads/main\n")

        project_id = _project_id(tmp_path)
        assert project_id == "github.com/user/repo"

    def test_project_id_non_git(self, tmp_path):
        """Non-git project uses hashed path."""
        project_id = _project_id(tmp_path)
        assert project_id.startswith("anon:")
        assert len(project_id) == 5 + 12  # "anon:" + 12 hex chars = 17


# Tests for _hash_file


class TestHashFile:
    """Tests for file hashing."""

    def test_hash_file(self, tmp_path):
        """File hashing returns consistent hash."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")
        hash1 = _hash_file(test_file)
        hash2 = _hash_file(test_file)
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 hex

    def test_hash_file_nonexistent(self):
        """Nonexistent file returns None."""
        assert _hash_file(Path("/nonexistent/file.txt")) is None

    def test_hash_file_different_content(self, tmp_path):
        """Different content produces different hash."""
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        file1.write_text("content 1")
        file2.write_text("content 2")
        assert _hash_file(file1) != _hash_file(file2)


# Tests for FileEntry


class TestFileEntry:
    """Tests for FileEntry dataclass."""

    def test_file_entry_defaults(self):
        """FileEntry has sensible defaults."""
        entry = FileEntry(path="test.py", content_hash="abc123")
        assert entry.path == "test.py"
        assert entry.content_hash == "abc123"
        assert entry.language == ""
        assert entry.summary == ""
        assert entry.size_bytes == 0

    def test_file_entry_with_all_fields(self):
        """FileEntry stores all fields."""
        entry = FileEntry(
            path="src/main.py",
            content_hash="def456",
            language="python",
            summary="Main entry point",
            size_bytes=1024,
        )
        assert entry.path == "src/main.py"
        assert entry.language == "python"
        assert entry.size_bytes == 1024


# Tests for ProjectPrimer


class TestProjectPrimer:
    """Tests for ProjectPrimer dataclass."""

    def test_primer_defaults(self):
        """ProjectPrimer has sensible defaults."""
        primer = ProjectPrimer(project_id="test", project_root="/path/to/test")
        assert primer.project_id == "test"
        assert primer.project_root == "/path/to/test"
        assert primer.project_name == ""
        assert primer.key_directories == []
        assert primer.key_files == []
        assert primer.understanding == ""
        assert primer.patterns == []
        assert primer.tech_stack == []
        assert primer.turn_count == 0

    def test_to_dict(self):
        """Serialization excludes internal fields."""
        primer = ProjectPrimer(
            project_id="test",
            project_root="/path/to/test",
            key_files=[FileEntry(path="a.py", content_hash="hash1")],
        )
        d = primer.to_dict()
        assert d["project_id"] == "test"
        assert "_stale_files" not in d
        assert d["key_files"] == [
            {"path": "a.py", "content_hash": "hash1", "language": "", "summary": "", "size_bytes": 0}
        ]

    def test_from_dict(self):
        """Deserialization works correctly."""
        data = {
            "project_id": "test",
            "project_root": "/path/to/test",
            "key_files": [
                {"path": "a.py", "content_hash": "hash1", "language": "python", "summary": "", "size_bytes": 0}
            ],
        }
        primer = ProjectPrimer.from_dict(data)
        assert primer.project_id == "test"
        assert len(primer.key_files) == 1
        assert primer.key_files[0].path == "a.py"


# Tests for file change detection


class TestChangeDetection:
    """Tests for file change detection in primers."""

    def test_verify_files_no_changes(self, tmp_path):
        """No changes detected when files are unchanged."""
        primer = ProjectPrimer(project_id="test", project_root=str(tmp_path))
        primer.add_file("test.txt", "hello world")
        # File exists with same content
        (tmp_path / "test.txt").write_text("hello world")
        changed = primer.verify_files(tmp_path)
        assert changed == []
        assert not primer.has_stale_files()

    def test_verify_files_content_changed(self, tmp_path):
        """Changed content is detected."""
        primer = ProjectPrimer(project_id="test", project_root=str(tmp_path))
        primer.add_file("test.txt", "hello world")
        # File exists with different content
        (tmp_path / "test.txt").write_text("changed content")
        changed = primer.verify_files(tmp_path)
        assert "test.txt" in changed
        assert primer.has_stale_files()
        assert "test.txt" in primer.stale_file_paths()

    def test_verify_files_deleted(self, tmp_path):
        """Deleted files are detected."""
        primer = ProjectPrimer(project_id="test", project_root=str(tmp_path))
        primer.add_file("test.txt", "hello world")
        # File doesn't exist
        changed = primer.verify_files(tmp_path)
        assert "test.txt" in changed
        assert primer.has_stale_files()

    def test_mark_fresh(self, tmp_path):
        """mark_fresh clears stale flag."""
        primer = ProjectPrimer(project_id="test", project_root=str(tmp_path))
        primer.add_file("test.txt", "hello")
        (tmp_path / "test.txt").write_text("changed")
        primer.verify_files(tmp_path)
        assert primer.has_stale_files()
        primer.mark_fresh("test.txt")
        assert "test.txt" not in primer._stale_files

    def test_mark_all_fresh(self, tmp_path):
        """mark_all_fresh clears all stale flags."""
        primer = ProjectPrimer(project_id="test", project_root=str(tmp_path))
        primer.add_file("a.txt", "content a")
        primer.add_file("b.txt", "content b")
        (tmp_path / "a.txt").write_text("changed a")
        (tmp_path / "b.txt").write_text("changed b")
        primer.verify_files(tmp_path)
        assert len(primer._stale_files) == 2
        primer.mark_all_fresh()
        assert len(primer._stale_files) == 0

    def test_remove_file(self):
        """remove_file removes from key_files and stale set."""
        primer = ProjectPrimer(project_id="test", project_root="/path")
        primer.add_file("test.txt", "content")
        primer._stale_files.add("test.txt")
        primer.remove_file("test.txt")
        assert len(primer.key_files) == 0
        assert "test.txt" not in primer._stale_files


# Tests for add_file


class TestAddFile:
    """Tests for add_file method."""

    def test_add_file_new(self):
        """Add new file to primer."""
        primer = ProjectPrimer(project_id="test", project_root="/path")
        primer.add_file("test.txt", "content", summary="A test file")
        assert len(primer.key_files) == 1
        assert primer.key_files[0].path == "test.txt"
        assert primer.key_files[0].summary == "A test file"
        # .txt extension maps to "txt" (no mapping in _EXTENSION_MAP)
        assert primer.key_files[0].language == "txt"

    def test_add_file_update_existing(self):
        """Updating existing file replaces entry."""
        primer = ProjectPrimer(project_id="test", project_root="/path")
        primer.add_file("test.txt", "content 1")
        primer.add_file("test.txt", "content 2")
        assert len(primer.key_files) == 1
        assert primer.key_files[0].content_hash != _hash_file(Path("/nonexistent"))  # New hash

    def test_add_file_language_detection(self):
        """Language is detected from extension."""
        primer = ProjectPrimer(project_id="test", project_root="/path")
        primer.add_file("test.py", "print('hello')")
        assert primer.key_files[0].language == "python"

        primer.add_file("test.js", "console.log('hello')")
        assert primer.key_files[1].language == "javascript"

        primer.add_file("test.rs", "fn main() {}")
        assert primer.key_files[2].language == "rust"


# Tests for _EXTENSION_MAP


class TestExtensionMap:
    """Tests for extension to language mapping."""

    def test_common_extensions(self):
        """Common extensions are mapped correctly."""
        assert _EXTENSION_MAP["py"] == "python"
        assert _EXTENSION_MAP["js"] == "javascript"
        assert _EXTENSION_MAP["ts"] == "typescript"
        assert _EXTENSION_MAP["rs"] == "rust"
        assert _EXTENSION_MAP["go"] == "go"
        assert _EXTENSION_MAP["java"] == "java"

    def test_unknown_extension_fallback(self):
        """Unknown extension returns the extension itself."""
        assert _EXTENSION_MAP.get("xyz", "xyz") == "xyz"


# Tests for detect_project_root


class TestDetectProjectRoot:
    """Tests for project root detection."""

    def test_detect_git_root(self, tmp_path):
        """Detects git repository root."""
        git_dir = tmp_path / "subdir" / ".git"
        git_dir.mkdir(parents=True)
        result = detect_project_root(str(tmp_path / "subdir" / "file.txt"))
        assert result == tmp_path / "subdir"

    def test_detect_non_git_root(self, tmp_path):
        """Non-git directory returns the starting directory."""
        start = tmp_path / "subdir"
        start.mkdir()
        result = detect_project_root(str(start / "file.txt"))
        # With no .git found, returns the start directory (subdir)
        assert result == start

    def test_detect_default_cwd(self, tmp_path, monkeypatch):
        """Default detection uses current directory."""
        monkeypatch.chdir(tmp_path)
        result = detect_project_root()
        assert result == tmp_path


# Tests for save_primer and load_primer


class TestPrimerIO:
    """Tests for primer save/load operations."""

    def test_save_and_load_primer(self, tmp_path):
        """Save and load primer works."""
        # Monkey-patch the primer directory
        import dendrophis.memory.project as project_module

        original = project_module._PRIMER_DIR
        project_module._PRIMER_DIR = tmp_path
        try:
            primer = ProjectPrimer(
                project_id="test_proj",
                project_root="/path/to/test",
                project_name="Test Project",
            )
            primer.add_file("main.py", "print('hello')")
            saved_path = save_primer(primer)
            assert saved_path.exists()

            loaded = load_primer("test_proj")
            assert loaded is not None
            assert loaded.project_id == "test_proj"
            assert loaded.project_name == "Test Project"
            assert len(loaded.key_files) == 1
            assert loaded.key_files[0].path == "main.py"
        finally:
            project_module._PRIMER_DIR = original

    def test_load_nonexistent_primer(self, tmp_path):
        """Load returns None for nonexistent primer."""
        import dendrophis.memory.project as project_module

        original = project_module._PRIMER_DIR
        project_module._PRIMER_DIR = tmp_path
        try:
            assert load_primer("nonexistent") is None
        finally:
            project_module._PRIMER_DIR = original


# Tests for delete_primer


class TestDeletePrimer:
    """Tests for delete_primer."""

    def test_delete_primer(self, tmp_path):
        """Delete removes primer file."""
        import dendrophis.memory.project as project_module

        original = project_module._PRIMER_DIR
        project_module._PRIMER_DIR = tmp_path
        try:
            primer = ProjectPrimer(project_id="to_delete", project_root="/path")
            save_primer(primer)
            assert (tmp_path / "to_delete.primer.json").exists()

            result = delete_primer("to_delete")
            assert result is True
            assert not (tmp_path / "to_delete.primer.json").exists()

            result = delete_primer("nonexistent")
            assert result is False
        finally:
            project_module._PRIMER_DIR = original


# Tests for list_primers


class TestListPrimers:
    """Tests for list_primers."""

    def test_list_primers(self, tmp_path):
        """List returns all primers."""
        import dendrophis.memory.project as project_module

        original = project_module._PRIMER_DIR
        project_module._PRIMER_DIR = tmp_path
        try:
            # Save multiple primers
            for i in range(3):
                primer = ProjectPrimer(
                    project_id=f"proj{i}",
                    project_root=f"/path/{i}",
                    project_name=f"Project {i}",
                    updated_at=f"2024-01-{i + 1}",
                )
                save_primer(primer)

            results = list_primers()
            assert len(results) == 3
            # Sorted by mtime descending
            project_ids = [r[0] for r in results]
            assert "proj2" in project_ids
        finally:
            project_module._PRIMER_DIR = original

    def test_list_primers_empty(self, tmp_path):
        """List returns empty for no primers."""
        import dendrophis.memory.project as project_module

        original = project_module._PRIMER_DIR
        project_module._PRIMER_DIR = tmp_path
        try:
            results = list_primers()
            assert results == []
        finally:
            project_module._PRIMER_DIR = original
