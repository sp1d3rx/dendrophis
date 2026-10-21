"""Memory scoping — per-project memory isolation with a global tier.

Every memory belongs to a *scope*: either a specific project (identified by
the stable project ID derived from the CWD's git root — the same scheme the
project primer uses) or the special ``global`` scope for cross-project
memories (user preferences, identity, tooling gotchas that apply everywhere).

Default visibility: the current project's memories plus the global tier.
This is what stops project A's memories from leaking into project B's
sessions when the user switches projects.
"""

from __future__ import annotations

from dendrophis.memory.project import _project_id, detect_project_root

#: Special scope for cross-project memories (preferences, identity, etc.).
GLOBAL_PROJECT_ID = "global"

#: Legacy unscoped marker — memories saved before project scoping existed.
#: Treated as part of the global tier so they remain visible everywhere.
LEGACY_UNSCOPED_ID = ""


def current_project_id() -> str:
    """Return the stable project ID for the current working directory.

    Uses the git root of the CWD (remote URL if available, otherwise the
    local path), matching the primer's ``_project_id`` scheme. Falls back
    to the global scope if no root can be determined.
    """
    root = detect_project_root()
    if root is None:
        return GLOBAL_PROJECT_ID
    return _project_id(root)


def default_scope() -> list[str]:
    """Return the project scopes visible by default.

    The current project plus the global tier (including legacy unscoped
    memories). Used as the default filter for searches and association
    generation so other projects' memories stay out of context.
    """
    return [current_project_id(), GLOBAL_PROJECT_ID, LEGACY_UNSCOPED_ID]


def scope_for_project(project_id: str) -> list[str]:
    """Return the scope list for an explicit project filter.

    ``"global"`` maps to the global tier (including legacy unscoped rows);
    any other ID maps to that exact project.
    """
    if project_id == GLOBAL_PROJECT_ID:
        return [GLOBAL_PROJECT_ID, LEGACY_UNSCOPED_ID]
    return [project_id]
