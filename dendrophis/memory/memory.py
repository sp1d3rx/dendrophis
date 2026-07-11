"""MemoryStore — SQLite-backed persistent memory with pluggable embeddings."""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from dendrophis.memory.embedder import BaseEmbedder, NullEmbedder
from dendrophis.memory.models import MemoryEntry, MemoryStats

if TYPE_CHECKING:
    from spacy.language import Language


# Vector-to-BLOB helpers (SQLite stores BLOBs, not arrays)
def _vector_to_blob(vec: np.ndarray) -> bytes:
    return vec.astype(np.float32).tobytes()


def _blob_to_vector(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


class MemoryStore:
    """SQLite-backed memory store with pluggable embedding support.

    Thread-safe via a write lock. Reads are lock-free (SQLite handles concurrency).
    Embeddings are stored as BLOBs in SQLite for persistence.
    """

    def __init__(
        self,
        db_path: str | Path,
        embedder: BaseEmbedder | None = None,
        nlp: Language | None = None,
    ) -> None:
        self._db_path = Path(db_path).expanduser()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        # Backward compatibility: if nlp is provided but no embedder, wrap it
        if embedder is None and nlp is not None:
            from dendrophis.memory.embedder import SpacyEmbedder

            embedder = SpacyEmbedder(nlp)
        if embedder is None:
            embedder = NullEmbedder()

        self._embedder = embedder
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    @property
    def embedder(self) -> BaseEmbedder:
        return self._embedder

    @embedder.setter
    def embedder(self, value: BaseEmbedder) -> None:
        self._embedder = value

    # Backward compatibility property for nlp
    @property
    def nlp(self) -> Language | None:
        # Try to extract nlp from SpacyEmbedder
        if hasattr(self._embedder, "_nlp"):
            return self._embedder._nlp  # type: ignore[attr-defined]
        return None

    @nlp.setter
    def nlp(self, value: Language) -> None:
        if value is not None:
            from dendrophis.memory.embedder import SpacyEmbedder

            self._embedder = SpacyEmbedder(value)
        else:
            self._embedder = NullEmbedder()

    @contextmanager
    def _connect(self):
        """Get a thread-local SQLite connection with auto-commit."""
        conn = sqlite3.connect(str(self._db_path), timeout=3.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Create tables and indexes if they don't exist."""
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    embedding BLOB,
                    tags TEXT NOT NULL DEFAULT '[]',
                    source TEXT NOT NULL DEFAULT 'auto',
                    project_id TEXT,
                    session_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    score REAL NOT NULL DEFAULT 0.0
                );
            """)
            # Migration: add summary column if it doesn't exist (legacy DBs)
            with contextlib.suppress(sqlite3.OperationalError):
                conn.execute("ALTER TABLE memories ADD COLUMN summary TEXT NOT NULL DEFAULT ''")

            conn.executescript("""
                CREATE TABLE IF NOT EXISTS tags (
                    name TEXT PRIMARY KEY,
                    memory_count INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project_id);
                CREATE INDEX IF NOT EXISTS idx_memories_source ON memories(source);
                CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);
                CREATE INDEX IF NOT EXISTS idx_memories_score ON memories(score);
                CREATE INDEX IF NOT EXISTS idx_memories_project_created ON memories(project_id, created_at);

                CREATE TABLE IF NOT EXISTS tag_memories (
                    tag_name TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    PRIMARY KEY (tag_name, memory_id),
                    FOREIGN KEY (tag_name) REFERENCES tags(name) ON DELETE CASCADE,
                    FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_tag_memories_tag ON tag_memories(tag_name);
                CREATE INDEX IF NOT EXISTS idx_tag_memories_memory ON tag_memories(memory_id);
            """)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def save_memory(
        self,
        content: str,
        tags: list[str] | None = None,
        source: str = "auto",
        project_id: str = "",
        session_id: str = "",
        embedding: np.ndarray | None = None,
    ) -> MemoryEntry:
        """Save a memory entry. Returns the created entry."""
        # EAFP: Validate inputs with edge case handling
        if not content or not isinstance(content, str):
            raise ValueError("Content must be a non-empty string")

        # Edge case: Limit content size to prevent database issues
        if len(content) > 1000000:  # 1MB limit
            raise ValueError("Content exceeds maximum size of 1MB")

        if tags is not None and not isinstance(tags, list):
            raise ValueError("Tags must be a list or None")

        # Edge case: Limit number of tags
        if tags and len(tags) > 100:
            raise ValueError("Maximum of 100 tags allowed")

        # Edge case: Validate tag formats
        if tags:
            for tag in tags:
                if not isinstance(tag, str):
                    raise ValueError("All tags must be strings")
                if len(tag) > 255:  # Database column limit
                    raise ValueError(f"Tag '{tag[:20]}...' exceeds maximum length of 255 characters")

        if embedding is not None and not isinstance(embedding, np.ndarray):
            raise ValueError("Embedding must be a numpy array or None")

        # Edge case: Limit embedding size
        if embedding is not None and embedding.size > 10000:
            raise ValueError("Embedding vector too large (max 10000 dimensions)")

        try:
            now = datetime.now().isoformat()
            entry_id = uuid.uuid4().hex[:16]
            tags = tags or []

            # Compute embedding if not provided
            if embedding is None:
                embedding = self.compute_embedding(content)

            with self._lock, self._connect() as conn:
                # EAFP: Handle database operation errors gracefully
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO memories"
                        " (id,content,summary,embedding,tags,source,project_id,session_id,created_at,updated_at,score)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            entry_id,
                            content,
                            "",  # summary - empty by default, can be updated later
                            _vector_to_blob(embedding) if embedding is not None else None,
                            json.dumps(tags),
                            source,
                            project_id,
                            session_id,
                            now,
                            now,
                            0.0,
                        ),
                    )
                except sqlite3.Error as db_error:
                    raise RuntimeError(f"Failed to save memory to database: {db_error!s}") from db_error

                # Upsert tags with error handling
                for tag in tags:
                    try:
                        if not isinstance(tag, str):
                            continue  # Skip invalid tags
                        conn.execute(
                            """INSERT INTO tags (name, memory_count) VALUES (?, 1)
                                   ON CONFLICT(name) DO UPDATE SET memory_count = memory_count + 1""",
                            (tag,),
                        )
                    except sqlite3.Error:
                        # Log but don't fail for individual tag errors
                        continue

                # Link tag -> memory with error handling
                for tag in tags:
                    try:
                        if not isinstance(tag, str):
                            continue  # Skip invalid tags
                        conn.execute(
                            "INSERT OR IGNORE INTO tag_memories (tag_name, memory_id) VALUES (?, ?)",
                            (tag, entry_id),
                        )
                    except sqlite3.Error:
                        # Log but don't fail for individual link errors
                        continue

            with contextlib.suppress(Exception):
                self.increment_score(entry_id)

            return MemoryEntry(
                id=entry_id,
                content=content,
                tags=tags,
                source=source,
                project_id=project_id,
                session_id=session_id,
                created_at=now,
                updated_at=now,
                score=1.0,  # Initial score after increment
            )
        except Exception as e:
            # EAFP: Provide meaningful error message for any unexpected failures
            raise RuntimeError(f"Failed to save memory: {e!s}") from e

    def get_memory(self, memory_id: str) -> MemoryEntry | None:
        """Retrieve a memory by ID."""
        # EAFP: Validate input
        if not memory_id or not isinstance(memory_id, str):
            raise ValueError("memory_id must be a non-empty string")

        try:
            with self._connect() as conn:
                # EAFP: Handle database query errors gracefully
                try:
                    row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
                except sqlite3.Error as db_error:
                    raise RuntimeError(f"Database query failed: {db_error!s}") from db_error

                if row is None:
                    return None

                try:
                    return self._row_to_entry(row)
                except Exception as conversion_error:
                    raise RuntimeError(
                        f"Failed to convert database row to memory entry: {conversion_error!s}"
                    ) from conversion_error
        except Exception as e:
            # EAFP: Provide meaningful error message for any unexpected failures
            raise RuntimeError(f"Failed to retrieve memory: {e!s}") from e

    def update_memory(self, memory_id: str, **fields: Any) -> MemoryEntry | None:
        """Update fields of an existing memory. Returns the updated entry or None."""
        now = datetime.now().isoformat()

        with self._lock, self._connect() as conn:
            # Handle tags within the same connection for atomicity
            if "tags" in fields:
                old_tags = self._get_memory_tags(memory_id, conn=conn)
                new_tags = fields["tags"]
                for tag in old_tags:
                    if tag not in new_tags:
                        self._decrement_tag_count(tag, conn=conn)
                for tag in new_tags:
                    if tag not in old_tags:
                        self._increment_tag_count(tag, conn=conn)
                fields["tags"] = json.dumps(new_tags)

            updates: list[tuple[str, Any]] = []
            params: list[Any] = []

            for key, value in fields.items():
                if key == "tags":
                    updates.append(("tags = ?", value))
                elif key == "content":
                    updates.append(("content = ?", value))
                elif key == "score":
                    updates.append(("score = score + ?", value))
                else:
                    updates.append((f"{key} = ?", value))
                params.append(value)

            if not updates:
                return self.get_memory(memory_id)

            updates.append(("updated_at = ?", now))
            params.append(now)
            params.append(memory_id)

            conn.execute(
                f"UPDATE memories SET {', '.join(u[0] for u in updates)} WHERE id = ?",
                params,
            )

        return self.get_memory(memory_id)

    def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory. Returns True if it existed."""
        with self._lock, self._connect() as conn:
            cursor = conn.execute("SELECT tags FROM memories WHERE id = ?", (memory_id,))
            try:
                row = cursor.fetchone()
            finally:
                cursor.close()

            if row is None:
                return False
            tags = json.loads(row["tags"])
            conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            conn.execute("DELETE FROM tag_memories WHERE memory_id = ?", (memory_id,))
            # Clean up tag counts
            for tag in tags:
                self._decrement_tag_count(tag, conn=conn)
        return True

    def list_memories(
        self,
        project_id: str | None = None,
        source: str | None = None,
        tag: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MemoryEntry]:
        """List memories with optional filters."""
        conditions: list[str] = []
        params: list[Any] = []

        if project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        if source:
            conditions.append("source = ?")
            params.append(source)
        if tag:
            # Subquery for tag filtering
            subquery = """
                SELECT memory_id FROM tag_memories
                WHERE tag_name = ?
            """
            conditions.append(f"id IN ({subquery})")
            params.append(tag)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM memories {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            return [self._row_to_entry(row) for row in rows]

    def get_stats(self) -> MemoryStats:
        """Return aggregate memory statistics."""
        stats = MemoryStats()
        with self._connect() as conn:
            stats.total_memories = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            stats.total_projects = conn.execute(
                "SELECT COUNT(DISTINCT project_id) FROM memories WHERE project_id != ''"
            ).fetchone()[0]
            stats.total_tags = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]

            # Memories by source
            rows = conn.execute("SELECT source, COUNT(*) as cnt FROM memories GROUP BY source").fetchall()
            stats.memories_by_source = {row["source"]: row["cnt"] for row in rows}

            # Top tags
            rows = conn.execute("SELECT name, memory_count FROM tags ORDER BY memory_count DESC LIMIT 20").fetchall()
            stats.top_tags = [(row["name"], row["memory_count"]) for row in rows]
        return stats

    # ------------------------------------------------------------------
    # Tag helpers
    # ------------------------------------------------------------------

    def _get_memory_tags(self, memory_id: str, conn: sqlite3.Connection | None = None) -> list[str]:
        if conn:
            rows = conn.execute("SELECT tag_name FROM tag_memories WHERE memory_id = ?", (memory_id,)).fetchall()
            return [row["tag_name"] for row in rows]
        with self._connect() as conn:
            rows = conn.execute("SELECT tag_name FROM tag_memories WHERE memory_id = ?", (memory_id,)).fetchall()
            return [row["tag_name"] for row in rows]

    def _increment_tag_count(self, tag: str, conn: sqlite3.Connection | None = None) -> None:
        if conn:
            conn.execute(
                """INSERT INTO tags (name, memory_count) VALUES (?, 1)
                   ON CONFLICT(name) DO UPDATE SET memory_count = memory_count + 1""",
                (tag,),
            )
        else:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO tags (name, memory_count) VALUES (?, 1)
                       ON CONFLICT(name) DO UPDATE SET memory_count = memory_count + 1""",
                    (tag,),
                )

    def _decrement_tag_count(self, tag: str, conn: sqlite3.Connection | None = None) -> None:
        if conn:
            conn.execute("UPDATE tags SET memory_count = memory_count - 1 WHERE name = ?", (tag,))
            conn.execute("DELETE FROM tags WHERE name = ? AND memory_count <= 0", (tag,))
        else:
            with self._connect() as conn:
                conn.execute("UPDATE tags SET memory_count = memory_count - 1 WHERE name = ?", (tag,))
                conn.execute("DELETE FROM tags WHERE name = ? AND memory_count <= 0", (tag,))

    def increment_score(self, memory_id: str, amount: float = 1.0) -> None:
        """Increment the usage score of a memory (boosts it in search results)."""
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE memories SET score = score + ? WHERE id = ?", (amount, memory_id))

    # ------------------------------------------------------------------
    # Row -> Entry conversion
    # ------------------------------------------------------------------

    def _row_to_entry(self, row: sqlite3.Row) -> MemoryEntry:
        return MemoryEntry(
            id=row["id"],
            content=row["content"],
            summary=row["summary"] if "summary" in row else "",  # noqa: SIM401
            tags=json.loads(row["tags"]),
            source=row["source"],
            project_id=row["project_id"] or "",
            session_id=row["session_id"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            score=row["score"],
            embedding_blob=row["embedding"],  # raw BLOB for search
        )

    # ------------------------------------------------------------------
    # Embedding helpers
    # ------------------------------------------------------------------

    def compute_embedding(self, text: str) -> np.ndarray | None:
        """Compute an embedding for text using the configured embedder.

        Returns None if no embedder is available or embedding fails.
        """
        return self._embedder.embed(text)

    def cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Compute cosine similarity between two vectors.

        Works for any dimension vectors (spaCy 300, OpenAI 1536/3072, etc.).
        """
        import numpy as np

        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))
