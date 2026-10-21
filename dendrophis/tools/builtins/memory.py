"""Built-in memory tools: save_memory, search_memory, recall_memory, delete_memory."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from dendrophis.tools.base import BaseTool

if TYPE_CHECKING:
    from dendrophis.memory import MemoryStore


# Default memory database path
DEFAULT_MEMORY_DB = Path.home() / ".config" / "dendrophis" / "memory.db"


class SaveMemoryTool(BaseTool):
    """Save a memory entry for later retrieval."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    @property
    def name(self) -> str:
        return "save_memory"

    @property
    def description(self) -> str:
        return (
            "Save a piece of information to long-term memory. "
            "The memory will be searchable later. "
            "Use this to remember important context, preferences, or facts. "
            "Memories are scoped to the current project by default so they do not "
            "leak into other projects."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "REQUIRED. The text content to remember.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags for categorizing the memory (e.g., ['preference', 'python']).",
                },
                "project_id": {
                    "type": "string",
                    "description": (
                        "Optional scope for this memory. Defaults to the current project "
                        "(auto-detected from the working directory). Pass 'global' for "
                        "cross-project memories such as user preferences that apply everywhere."
                    ),
                },
            },
            "required": ["content"],
        }

    async def execute(self, content: str, tags: list[str] | None = None, project_id: str = "") -> dict[str, Any]:
        try:
            from dendrophis.memory.scope import current_project_id

            # Auto-scope to the current project unless the caller chose a scope explicitly.
            effective_project = project_id if project_id else current_project_id()

            # Compute embedding for the content
            embedding = self._store.compute_embedding(content)

            entry = self._store.save_memory(
                content=content,
                tags=tags or [],
                source="auto",
                project_id=effective_project,
                embedding=embedding,
            )
            return {
                "success": True,
                "memory_id": entry.id,
                "project_id": effective_project,
                "content": content[:100] + "...",
            }
        except Exception as memory_error:
            return {"error": f"Failed to save memory: {memory_error}"}


class SearchMemoryTool(BaseTool):
    """Search saved memories by query, tags, or project."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    @property
    def name(self) -> str:
        return "search_memory"

    @property
    def description(self) -> str:
        return (
            "Search your saved memories for relevant information. "
            "Use this to recall previously remembered context, preferences, or facts. "
            "By default searches the current project plus global (cross-project) memories."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional search query (natural language or keywords).",
                },
                "tag": {
                    "type": "string",
                    "description": "Optional tag to filter memories by.",
                },
                "project_id": {
                    "type": "string",
                    "description": (
                        "Optional project scope to search. Defaults to the current project "
                        "plus global memories. Pass a specific project ID to search only "
                        "that project, or 'global' for cross-project memories only."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default: 5).",
                    "minimum": 1,
                    "maximum": 20,
                },
            },
        }

    async def execute(
        self,
        query: str | None = None,
        tag: str | None = None,
        project_id: str | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        try:
            from dendrophis.memory.scope import default_scope, scope_for_project
            from dendrophis.memory.search import MemorySearcher

            # Default visibility: current project + global tier. An explicit
            # project_id narrows to that exact scope.
            scope = scope_for_project(project_id) if project_id else default_scope()

            searcher = MemorySearcher(self._store)
            if not query:
                if tag:
                    results = searcher.search_by_tag(
                        tag=tag,
                        limit=limit,
                        project_ids=scope,
                    )
                elif project_id:
                    results = searcher.search_by_project(
                        project_id=project_id,
                        limit=limit,
                    )
                else:
                    from dendrophis.memory.models import MemorySearchResult

                    candidates = self._store.list_memories(limit=limit, project_ids=scope)
                    results = [MemorySearchResult(memory=entry, score=1.0, method="list") for entry in candidates]
            else:
                results = searcher.search(
                    query=query,
                    limit=limit,
                    tag=tag,
                    project_ids=scope,
                )
            return {
                "success": True,
                "results": [
                    {
                        "memory_id": result.memory.id,
                        "project_id": result.memory.project_id,
                        "summary": (
                            result.memory.summary
                            or (
                                result.memory.content[:200] + "..."
                                if len(result.memory.content) > 200
                                else result.memory.content
                            )
                        ),
                        "tags": result.memory.tags,
                        "source": result.memory.source,
                        "score": result.score,
                        "method": result.method,
                    }
                    for result in results
                ],
                "count": len(results),
            }
        except Exception as error:
            return {"error": f"Failed to search memory: {error}"}


class DeleteMemoryTool(BaseTool):
    """Delete a memory entry. Requires user confirmation."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    @property
    def name(self) -> str:
        return "delete_memory"

    @property
    def description(self) -> str:
        return (
            "Delete a previously saved memory entry. "
            "This action cannot be undone. "
            "Requires user confirmation before execution."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "REQUIRED. The ID of the memory entry to delete.",
                },
            },
            "required": ["memory_id"],
        }

    async def execute(self, memory_id: str) -> dict[str, Any]:
        try:
            success = self._store.delete_memory(memory_id)
            if success:
                return {"success": True, "memory_id": memory_id, "message": "Memory deleted successfully."}
            return {"success": False, "memory_id": memory_id, "message": "Memory not found."}
        except Exception as memory_error:
            return {"error": f"Failed to delete memory: {memory_error}"}


class RecallMemoryTool(BaseTool):
    """Recall/retrieve the full content of a specific memory entry into context."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    @property
    def name(self) -> str:
        return "recall_memory"

    @property
    def description(self) -> str:
        return (
            "Retrieve the full content of a previously saved memory entry. "
            "This brings the memory into my context so I can reference it. "
            "Use this after finding a memory via search to see complete details."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "REQUIRED. The ID of the memory entry to display.",
                },
            },
            "required": ["memory_id"],
        }

    async def execute(self, memory_id: str) -> dict[str, Any]:
        try:
            entry = self._store.get_memory(memory_id)
            if entry is None:
                return {"success": False, "memory_id": memory_id, "message": "Memory not found."}
            return {
                "success": True,
                "memory_id": entry.id,
                "content": entry.content,
                "tags": entry.tags,
                "source": entry.source,
                "created_at": entry.created_at,
            }
        except Exception as memory_error:
            return {"error": f"Failed to display memory: {memory_error}"}
