"""PrimerManager — project context priming for sessions.

Responsibilities:
- Save project primers (file snapshots for context)
- Load project primers
- Inject primer files into session context
- Track/untrack files for primer
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dendrophis.memory.project import (
    ProjectPrimer,
    _project_id,
    detect_project_root,
    load_primer,
    save_primer,
)

if TYPE_CHECKING:
    from dendrophis.caching.understanding import UnderstandingPhaseDetector
    from dendrophis.context.manager import ContextManager


class PrimerManager:
    """Manages project primer creation, loading, and file injection for sessions.

    Project primers capture the state of a project's files at a point in time,
    allowing the LLM to have context about the codebase without needing to
    re-read files on every session.
    """

    def __init__(
        self,
        context: ContextManager,
        understanding_detector: UnderstandingPhaseDetector,
        debug_logger: Callable[[str], None] | None = None,
    ) -> None:
        self._context = context
        self._understanding_detector = understanding_detector
        self._debug_logger = debug_logger

    def _log(self, message: str) -> None:
        """Log a debug message if logger is configured."""
        if self._debug_logger:
            self._debug_logger(message)

    def save_project_primer(self) -> str | None:
        """Capture current project understanding as a primer file.

        Scans the conversation context for file references and creates a primer
        that can be reloaded in future sessions.

        Returns:
            The project ID if successful, None otherwise.
        """
        try:
            root = detect_project_root()
            if root is None:
                return None

            primer = ProjectPrimer(
                project_id=_project_id(root),
                project_root=str(root),
                project_name=root.name,
                created_at=datetime.now().isoformat(),
                updated_at=datetime.now().isoformat(),
                turn_count=self._context.get_turn_count(),
            )

            # Scan top-level dirs
            for entry in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
                if entry.is_dir() and not entry.name.startswith("."):
                    primer.key_directories.append(entry.name)

            # Helper: resolve a path to relative-to-root, or None if outside root
            def _resolve_path(fpath: str) -> str | None:
                try:
                    p = Path(fpath)
                    if not p.exists() or not p.is_file():
                        return None
                    resolved = p.resolve()
                    return str(resolved.relative_to(root))
                except (ValueError, OSError):
                    return None

            # Path 1: [File: ...] markers in user/assistant messages (from @file autocomplete)
            for msg in self._context.messages:
                content = msg.get("content", "")
                role = msg.get("role", "")

                if isinstance(content, str):
                    for line in content.splitlines():
                        if line.startswith("[File: ") and "]" in line:
                            fpath = line[7 : line.index("]")]
                            rel = _resolve_path(fpath)
                            if rel:
                                try:
                                    text = Path(fpath).read_text(errors="replace")
                                    primer.add_file(rel, text)
                                except Exception:
                                    pass

                # Path 2: Tool result messages from the `read` tool
                if role == "tool" and msg.get("name") == "read":
                    try:
                        result_data = json.loads(content) if isinstance(content, str) else content
                        if isinstance(result_data, dict):
                            fpath = result_data.get("path", "")
                            if fpath:
                                rel = _resolve_path(fpath)
                                if rel:
                                    try:
                                        text = Path(fpath).read_text(errors="replace")
                                        primer.add_file(rel, text)
                                    except Exception:
                                        pass
                    except (json.JSONDecodeError, TypeError):
                        pass

            if self._understanding_detector.is_established():
                primer.understanding = (
                    f"Project understanding established at turn "
                    f"{self._understanding_detector.get_understanding_checkpoint_turn()}"
                )

            save_primer(primer)
            self._log(f"Project primer saved: {primer.project_id}")
            return primer.project_id

        except Exception as exc:
            self._log(f"Failed to save project primer: {exc}")
            return None

    def load_project_primer(self) -> dict[str, Any] | None:
        """Load the project primer for the current working directory.

        Returns:
            Dict with primer info (project_id, project_name, file_count, etc.)
            or None if no primer exists.
        """
        try:
            root = detect_project_root()
            if root is None:
                return None

            primer = load_primer(_project_id(root))
            if primer is None:
                return None

            primer.verify_files(root)  # updates content hashes for changed files

            return {
                "project_id": primer.project_id,
                "project_name": primer.project_name,
                "file_count": len(primer.key_files),
                "turn_count": primer.turn_count,
                "understanding": primer.understanding,
            }

        except Exception as exc:
            self._log(f"Failed to load project primer: {exc}")
            return None

    def inject_primer_files(self) -> dict[str, Any]:
        """Re-read all primer-tracked files from disk and inject into context.

        After injection, the primer is saved back to disk with updated content
        hashes so no files are marked stale on the next load.

        Returns:
            Dict with 'injected' (count) and 'total' (file count).
        """
        result: dict[str, Any] = {"injected": 0, "total": 0}

        try:
            root = detect_project_root()
            if root is None:
                return result

            primer = load_primer(_project_id(root))
            if primer is None:
                return result

            # Verify files to detect stale ones and update hashes
            primer.verify_files(root)
            result["total"] = len(primer.key_files)

            for entry in primer.key_files:
                full_path = root / entry.path
                if not full_path.exists():
                    continue  # File was deleted — skip

                try:
                    text = full_path.read_text(errors="replace")
                    self._context.append_file(entry.path, text)
                    result["injected"] += 1
                except Exception:
                    pass

            # Save primer back with updated hashes so nothing is stale next time
            primer.mark_all_fresh()
            save_primer(primer)
            return result

        except Exception as exc:
            self._log(f"Failed to inject primer files: {exc}")
            return result

    def track_file(self, path: str) -> bool:
        """Add a file to the project primer.

        Args:
            path: Path to the file (relative to project root).

        Returns:
            True on success, False otherwise.
        """
        try:
            root = detect_project_root()
            if root is None:
                return False

            primer = load_primer(_project_id(root))
            if primer is None:
                return False

            full_path = root / path
            if not full_path.exists() or not full_path.is_file():
                return False

            text = full_path.read_text(errors="replace")
            primer.add_file(path, text)
            primer.mark_all_fresh()
            save_primer(primer)
            self._log(f"Tracked file: {path}")
            return True

        except Exception as exc:
            self._log(f"Failed to track file {path}: {exc}")
            return False

    def untrack_file(self, path: str) -> bool:
        """Remove a file from the project primer.

        Args:
            path: Path to the file (relative to project root).

        Returns:
            True on success, False otherwise.
        """
        try:
            root = detect_project_root()
            if root is None:
                return False

            primer = load_primer(_project_id(root))
            if primer is None:
                return False

            primer.remove_file(path)
            save_primer(primer)
            self._log(f"Untracked file: {path}")
            return True

        except Exception as exc:
            self._log(f"Failed to untrack file {path}: {exc}")
            return False
