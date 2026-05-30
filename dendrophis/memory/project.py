"""Project primer — captures project understanding for reuse across sessions.

A primer stores:
- Project structure overview (key dirs, key files)
- Content hashes for change detection
- Natural-language summary of project understanding
- Coding patterns and conventions observed

On load, files are re-hashed to detect changes — stale entries are flagged
so the agent knows to re-read them.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from dendrophis.utils import _hash_content

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PRIMER_DIR = Path.home() / ".config" / "dendrophis" / "primers"


def _project_id(root: Path) -> str:
    """Derive a stable identifier for a project root.

    Uses the git remote origin if available, otherwise hashes the absolute path.
    """
    git_dir = root / ".git"
    if git_dir.exists():
        # Try to get the remote origin URL for a human-readable identifier
        head = git_dir / "HEAD"
        if head.exists():
            try:
                # Try to get remote origin
                config_path = git_dir / "config"
                if config_path.exists():
                    for line in config_path.read_text().splitlines():
                        if "url = " in line:
                            url = line.split("url = ", 1)[1].strip()
                            # Normalise: strip protocol and .git suffix
                            for prefix in ("https://", "http://", "git@", "ssh://"):
                                if url.startswith(prefix):
                                    url = url[len(prefix) :]
                            return url.replace(":", "/").removesuffix(".git")
            except Exception:
                pass
        # Fallback: use repo root path as identifier
        return f"local:{root.resolve()}"

    # Non-git project: hash the absolute path
    raw = str(root.resolve()).encode()
    return f"anon:{hashlib.sha256(raw).hexdigest()[:12]}"


def _hash_file(path: Path) -> str | None:
    """Hash a file on disk. Returns None if the file doesn't exist."""
    try:
        return _hash_content(path.read_text(errors="replace"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class FileEntry:
    """A tracked file in the project primer."""

    path: str  # Relative to project root
    content_hash: str  # SHA-256 of last-known content
    language: str = ""  # Guessed language (extension-based)
    summary: str = ""  # Optional human summary
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

    # Structure
    key_directories: list[str] = field(default_factory=list)
    key_files: list[FileEntry] = field(default_factory=list)

    # Understanding
    understanding: str = ""  # Free-text summary of what the project does
    patterns: list[str] = field(default_factory=list)  # Observed conventions
    tech_stack: list[str] = field(default_factory=list)  # Languages / frameworks

    # Tracks which files have been invalidated since last save
    _stale_files: set[str] = field(default_factory=set, repr=False)

    # ── Serialisation ──────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        primer_data = asdict(self)
        primer_data["key_files"] = [asdict(entry) for entry in self.key_files]
        primer_data.pop("_stale_files", None)
        return primer_data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectPrimer:
        file_entries = [FileEntry(**entry) for entry in data.pop("key_files", [])]
        data.pop("_stale_files", None)
        primer = cls(**data)
        primer.key_files = file_entries
        return primer

    # ── Change detection ───────────────────────────────────────────

    def verify_files(self, root: Path) -> list[str]:
        """Re-hash all tracked files and return paths that have changed.

        Also updates _stale_files so callers can inspect which entries
        are no longer trustworthy.
        """
        changed: list[str] = []
        for entry in self.key_files:
            full_path = root / entry.path
            current_hash = _hash_file(full_path)
            if current_hash is None:
                # File was deleted
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

    # ── Build helpers ──────────────────────────────────────────────

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
        # Replace existing entry if present
        for i, existing in enumerate(self.key_files):
            if existing.path == path:
                self.key_files[i] = entry
                return
        self.key_files.append(entry)

    def remove_file(self, path: str) -> None:
        self.key_files = [f for f in self.key_files if f.path != path]
        self._stale_files.discard(path)


# Extension → language map (subset, enough for most projects)
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


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def _primer_path(project_id: str) -> Path:
    """Return the filesystem path for a given project ID."""
    # Sanitise: replace path separators and colons with safe chars
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
    except Exception:
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
    for primer_file in sorted(_PRIMER_DIR.glob("*.primer.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            primer_data = json.loads(primer_file.read_text())
            results.append(
                (
                    primer_data.get("project_id", "?"),
                    primer_data.get("project_name", primer_data.get("project_id", "?")),
                    primer_data.get("updated_at", ""),
                )
            )
        except Exception:
            continue
    return results


# ---------------------------------------------------------------------------
# Auto-detect project root
# ---------------------------------------------------------------------------


def detect_project_root(path: str | Path | None = None) -> Path | None:
    """Walk up from *path* (or CWD) to find a git root, or return the directory itself."""
    start = Path(path or Path.cwd()).resolve()
    if not start.is_dir():
        start = start.parent

    # Walk up looking for .git
    for candidate in [start, *list(start.parents)]:
        if (candidate / ".git").exists():
            return candidate

    # No git repo — use the starting directory
    return start
