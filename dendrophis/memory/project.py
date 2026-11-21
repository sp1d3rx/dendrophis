"""Project primer — captures project understanding for reuse across sessions."""

from __future__ import annotations

import configparser
import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from dendrophis.utils import _hash_content

logger = logging.getLogger(__name__)

_PRIMER_DIR = Path.home() / ".config" / "dendrophis" / "primers"


def _resolve_git_dir(root: Path) -> Path | None:
    """Resolve `.git` directory, supporting worktrees/submodules where `.git` is a gitfile."""
    dot_git = root / ".git"
    if dot_git.is_dir():
        return dot_git
    if dot_git.is_file():
        try:
            first_line = dot_git.read_text(encoding="utf-8", errors="replace").splitlines()[0].strip()
            if first_line.startswith("gitdir:"):
                gitdir_raw = first_line.partition(":")[2].strip()
                resolved_gitdir = (root / gitdir_raw).resolve()
                if (resolved_gitdir / "commondir").is_file():
                    common_rel = (resolved_gitdir / "commondir").read_text(encoding="utf-8").strip()
                    return (resolved_gitdir / common_rel).resolve()
                return resolved_gitdir
        except Exception as gitfile_error:
            logger.debug("Failed to resolve gitfile at %s: %s", dot_git, gitfile_error)
    return None


def _normalize_remote_url(raw_url: str) -> str:
    url = raw_url.strip()
    for prefix in ("https://", "http://", "git@", "ssh://"):
        if url.startswith(prefix):
            url = url[len(prefix) :]
    return url.replace(":", "/").removesuffix(".git")


def _project_id(root: Path) -> str:
    """Derive a stable identifier for a project root.

    Uses the git remote `origin` URL if available (falling back to the first remote),
    otherwise hashes the absolute path.
    """
    git_dir = _resolve_git_dir(root)
    if git_dir is not None:
        head = git_dir / "HEAD"
        if head.exists():
            config_path = git_dir / "config"
            if config_path.exists():
                try:
                    parser = configparser.ConfigParser()
                    parser.read(config_path, encoding="utf-8")
                    if parser.has_section('remote "origin"') and parser.has_option('remote "origin"', "url"):
                        return _normalize_remote_url(parser.get('remote "origin"', "url"))
                    for section_name in parser.sections():
                        if section_name.startswith("remote ") and parser.has_option(section_name, "url"):
                            return _normalize_remote_url(parser.get(section_name, "url"))
                except Exception as parse_error:
                    logger.debug("Failed to parse git config at %s: %s", config_path, parse_error)
        return f"local:{root.resolve()}"

    raw = str(root.resolve()).encode()
    return f"anon:{hashlib.sha256(raw).hexdigest()[:12]}"


def _hash_file(path: Path) -> str | None:
    """Hash a file on disk. Returns None if the file doesn't exist."""
    try:
        return _hash_content(path.read_text(errors="replace"))
    except Exception:
        return None


@dataclass
class FileEntry:
    """A tracked file in the project primer."""

    path: str
    content_hash: str
    language: str = ""
    summary: str = ""
    size_bytes: int = 0


@dataclass
class ProjectPrimer:
    """Serialisable project understanding snapshot."""

    project_id: str
    project_root: str
    project_name: str = ""
    created_at: str = ""
    updated_at: str = ""
    turn_count: int = 0

    key_directories: list[str] = field(default_factory=list)
    key_files: list[FileEntry] = field(default_factory=list)

    understanding: str = ""
    patterns: list[str] = field(default_factory=list)
    tech_stack: list[str] = field(default_factory=list)

    _stale_files: set[str] = field(default_factory=set, repr=False)

    def to_dict(self) -> dict[str, Any]:
        primer_data = asdict(self)
        primer_data["key_files"] = [asdict(entry) for entry in self.key_files]
        primer_data.pop("_stale_files", None)
        return primer_data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectPrimer:
        payload = dict(data)
        file_entries = [FileEntry(**entry) for entry in payload.pop("key_files", [])]
        payload.pop("_stale_files", None)
        primer = cls(**payload)
        primer.key_files = file_entries
        return primer

    def verify_files(self, root: Path) -> list[str]:
        """Re-hash all tracked files and return paths that have changed."""
        changed: list[str] = []
        for entry in self.key_files:
            full_path = root / entry.path
            current_hash = _hash_file(full_path)
            if current_hash is None:
                changed.append(entry.path)
                self._stale_files.add(entry.path)
            elif current_hash != entry.content_hash:
                changed.append(entry.path)
                self._stale_files.add(entry.path)
                entry.content_hash = current_hash
        return changed

    def has_stale_files(self) -> bool:
        return len(self._stale_files) > 0

    def stale_file_paths(self) -> list[str]:
        return sorted(self._stale_files)

    def mark_fresh(self, path: str) -> None:
        """Mark a file as re-read and up-to-date."""
        self._stale_files.discard(path)

    def mark_all_fresh(self) -> None:
        self._stale_files.clear()

    def add_file(self, path: str, content: str, summary: str = "") -> None:
        """Add or update a tracked file."""
        ext = Path(path).suffix.lstrip(".")
        lang = _EXTENSION_MAP.get(ext, ext)
        entry = FileEntry(
            path=path,
            content_hash=_hash_content(content),
            language=lang,
            summary=summary,
            size_bytes=len(content.encode()),
        )
        for existing_index, existing_entry in enumerate(self.key_files):
            if existing_entry.path == path:
                self.key_files[existing_index] = entry
                return
        self.key_files.append(entry)

    def remove_file(self, path: str) -> None:
        self.key_files = [file_entry for file_entry in self.key_files if file_entry.path != path]
        self._stale_files.discard(path)


_EXTENSION_MAP = {
    "py": "python",
    "js": "javascript",
    "ts": "typescript",
    "tsx": "typescript-react",
    "jsx": "javascript-react",
    "rs": "rust",
    "go": "go",
    "java": "java",
    "kt": "kotlin",
    "swift": "swift",
    "rb": "ruby",
    "php": "php",
    "c": "c",
    "cpp": "cpp",
    "h": "c-header",
    "hpp": "cpp-header",
    "cs": "csharp",
    "scala": "scala",
    "r": "r",
    "m": "objective-c",
    "mm": "objective-cpp",
    "sh": "shell",
    "bash": "shell",
    "zsh": "shell",
    "fish": "shell",
    "ps1": "powershell",
    "sql": "sql",
    "html": "html",
    "css": "css",
    "scss": "scss",
    "less": "less",
    "json": "json",
    "yaml": "yaml",
    "yml": "yaml",
    "toml": "toml",
    "xml": "xml",
    "md": "markdown",
    "rst": "restructuredtext",
    "tex": "latex",
    "dockerfile": "dockerfile",
    "tf": "terraform",
    "proto": "protobuf",
}


def _primer_path(project_id: str) -> Path:
    """Return the filesystem path for a given project ID."""
    safe = project_id.replace("/", "_").replace(":", "_").replace("\\", "_")
    return _PRIMER_DIR / f"{safe}.primer.json"


def save_primer(primer: ProjectPrimer) -> Path:
    """Persist a primer to disk. Returns the path written."""
    _PRIMER_DIR.mkdir(parents=True, exist_ok=True)
    path = _primer_path(primer.project_id)
    primer_data = primer.to_dict()
    path.write_text(json.dumps(primer_data, indent=2, ensure_ascii=False))
    return path


def load_primer(project_id: str) -> ProjectPrimer | None:
    """Load a primer from disk. Returns None if not found or corrupt."""
    path = _primer_path(project_id)
    if not path.exists():
        return None
    try:
        raw_data = json.loads(path.read_text())
        return ProjectPrimer.from_dict(raw_data)
    except Exception as exc:
        logger.debug("Failed to load primer from %s: %s", path, exc)
        return None


def delete_primer(project_id: str) -> bool:
    """Delete a primer file. Returns True if it existed."""
    path = _primer_path(project_id)
    if path.exists():
        path.unlink()
        return True
    return False


def list_primers() -> list[tuple[str, str, str]]:
    """Return (project_id, project_name, updated_at) for all saved primers."""
    if not _PRIMER_DIR.exists():
        return []
    results: list[tuple[str, str, str]] = []
    sorted_files = sorted(
        _PRIMER_DIR.glob("*.primer.json"),
        key=lambda primer_path: primer_path.stat().st_mtime,
        reverse=True,
    )
    for primer_file in sorted_files:
        try:
            primer_data = json.loads(primer_file.read_text())
            results.append(
                (
                    primer_data.get("project_id", "?"),
                    primer_data.get("project_name", primer_data.get("project_id", "?")),
                    primer_data.get("updated_at", ""),
                )
            )
        except Exception as exc:
            logger.debug("Skipping unreadable primer file %s: %s", primer_file, exc)
            continue
    return results


def detect_project_root(path: str | Path | None = None) -> Path | None:
    """Walk up from *path* (or CWD) to find a git root, or return the directory itself."""
    start = Path(path or Path.cwd()).resolve()
    if not start.is_dir():
        start = start.parent

    for candidate in [start, *list(start.parents)]:
        if (candidate / ".git").exists():
            return candidate

    return start
