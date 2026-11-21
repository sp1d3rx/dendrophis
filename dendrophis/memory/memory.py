"""MemoryStore — SQLite-backed persistent memory with pluggable embeddings."""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from dendrophis.memory.embedder import BaseEmbedder, NullEmbedder, SpacyEmbedder
from dendrophis.memory.models import MemoryEntry, MemoryStats
from dendrophis.utils import ensure_secure_dir, ensure_secure_file

if TYPE_CHECKING:
    from spacy.language import Language


logger = logging.getLogger(__name__)

_UPDATABLE_COLUMNS = frozenset(
    {"tags", "content", "summary", "score", "source", "project_id", "session_id", "embedding"}
)


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
        ensure_secure_dir(self._db_path.parent)

        if embedder is None and nlp is not None:
            embedder = SpacyEmbedder(nlp)
        if embedder is None:
            embedder = NullEmbedder()

        self._embedder = embedder
        self._lock = threading.Lock()
        self._init_db()
        ensure_secure_file(self._db_path)

    @property
    def embedder(self) -> BaseEmbedder:
        return self._embedder

    @embedder.setter
    def embedder(self, value: BaseEmbedder) -> None:
        self._embedder = value

    @property
    def nlp(self) -> Language | None:
        if hasattr(self._embedder, "_nlp"):
            return self._embedder._nlp  # type: ignore[attr-defined]
        return None

    @nlp.setter
    def nlp(self, value: Language) -> None:
        if value is not None:
            self._embedder = SpacyEmbedder(value)
        else:
            self._embedder = NullEmbedder()

    @contextmanager
    def _connect(self):
        """Get a thread-local SQLite connection with auto-commit."""
        conn = sqlite3.connect(str(self._db_path), timeout=3.0)
        cursor = conn.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()
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
            cursor = conn.cursor()
            try:
                cursor.executescript("""
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
                with contextlib.suppress(sqlite3.OperationalError):
                    cursor.execute("ALTER TABLE memories ADD COLUMN summary TEXT NOT NULL DEFAULT ''")

                cursor.executescript("""
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
            finally:
                cursor.close()

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
        if not content or not isinstance(content, str):
            raise ValueError("Content must be a non-empty string")

        if len(content) > 1000000:
            raise ValueError("Content exceeds maximum size of 1MB")

        if tags is not None and not isinstance(tags, list):
            raise ValueError("Tags must be a list or None")

        if tags and len(tags) > 100:
            raise ValueError("Maximum of 100 tags allowed")

        if tags:
            for tag in tags:
                if not isinstance(tag, str):
                    raise ValueError("All tags must be strings")
                if len(tag) > 255:
                    raise ValueError(f"Tag '{tag[:20]}...' exceeds maximum length of 255 characters")

        if embedding is not None and not isinstance(embedding, np.ndarray):
            raise ValueError("Embedding must be a numpy array or None")

        if embedding is not None and embedding.size > 10000:
            raise ValueError("Embedding vector too large (max 10000 dimensions)")

        try:
            now = datetime.now().isoformat()
            entry_id = uuid.uuid4().hex[:16]
            deduped_tags = list(dict.fromkeys(tags)) if tags else []

            if embedding is None:
                embedding = self.compute_embedding(content)

            with self._lock, self._connect() as conn:
                cursor = conn.cursor()
                try:
                    try:
                        cursor.execute(
                            "INSERT OR REPLACE INTO memories"
                            " (id,content,summary,embedding,tags,source,"
                            "project_id,session_id,created_at,updated_at,score)"
                            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                entry_id,
                                content,
                                "",
                                _vector_to_blob(embedding) if embedding is not None else None,
                                json.dumps(deduped_tags),
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

                    for tag in deduped_tags:
                        try:
                            cursor.execute(
                                """INSERT INTO tags (name, memory_count) VALUES (?, 1)
                                       ON CONFLICT(name) DO UPDATE SET memory_count = memory_count + 1""",
                                (tag,),
                            )
                        except sqlite3.Error as tag_error:
                            logger.warning("Failed to upsert tag %r for memory %s: %s", tag, entry_id, tag_error)
                        try:
                            cursor.execute(
                                "INSERT OR IGNORE INTO tag_memories (tag_name, memory_id) VALUES (?, ?)",
                                (tag, entry_id),
                            )
                        except sqlite3.Error as tag_error:
                            logger.warning("Failed to link tag %r for memory %s: %s", tag, entry_id, tag_error)
                finally:
                    cursor.close()

            final_score = 0.0
            try:
                self.increment_score(entry_id)
                final_score = 1.0
            except sqlite3.Error as score_error:
                logger.warning("Failed to increment score for memory %s: %s", entry_id, score_error)

            return MemoryEntry(
                id=entry_id,
                content=content,
                tags=deduped_tags,
                source=source,
                project_id=project_id,
                session_id=session_id,
                created_at=now,
                updated_at=now,
                score=final_score,
            )
        except Exception as save_error:
            raise RuntimeError(f"Failed to save memory: {save_error!s}") from save_error

    def get_memory(self, memory_id: str) -> MemoryEntry | None:
        """Retrieve a memory by ID."""
        if not memory_id or not isinstance(memory_id, str):
            raise ValueError("memory_id must be a non-empty string")

        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                try:
                    row = cursor.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
                except sqlite3.Error as db_error:
                    raise RuntimeError(f"Database query failed: {db_error!s}") from db_error
                finally:
                    cursor.close()

                if row is None:
                    return None

                try:
                    return self._row_to_entry(row)
                except Exception as conversion_error:
                    raise RuntimeError(
                        f"Failed to convert database row to memory entry: {conversion_error!s}"
                    ) from conversion_error
        except Exception as retrieval_error:
            raise RuntimeError(f"Failed to retrieve memory: {retrieval_error!s}") from retrieval_error

    def update_memory(self, memory_id: str, **fields: Any) -> MemoryEntry | None:
        """Update fields of an existing memory. Returns the updated entry or None."""
        unknown_columns = set(fields.keys()) - _UPDATABLE_COLUMNS
        if unknown_columns:
            raise ValueError(f"Unsupported memory field(s): {', '.join(sorted(unknown_columns))}")

        now = datetime.now().isoformat()

        if "content" in fields and "embedding" not in fields and isinstance(fields["content"], str):
            recomputed = self.compute_embedding(fields["content"])
            fields["embedding"] = _vector_to_blob(recomputed) if recomputed is not None else None
        elif "embedding" in fields and isinstance(fields["embedding"], np.ndarray):
            fields["embedding"] = _vector_to_blob(fields["embedding"])

        with self._lock, self._connect() as conn:
            cursor = conn.cursor()
            try:
                if "tags" in fields:
                    old_tags = self._get_memory_tags(memory_id, conn=conn)
                    new_tags = list(dict.fromkeys(fields["tags"] or []))
                    for tag in old_tags:
                        if tag not in new_tags:
                            cursor.execute(
                                "DELETE FROM tag_memories WHERE tag_name = ? AND memory_id = ?",
                                (tag, memory_id),
                            )
                            self._decrement_tag_count(tag, conn=conn)
                    for tag in new_tags:
                        if tag not in old_tags:
                            self._increment_tag_count(tag, conn=conn)
                            cursor.execute(
                                "INSERT OR IGNORE INTO tag_memories (tag_name, memory_id) VALUES (?, ?)",
                                (tag, memory_id),
                            )
                    fields["tags"] = json.dumps(new_tags)

                updates: list[str] = []
                params: list[Any] = []

                for column_name, column_value in fields.items():
                    if column_name == "score":
                        updates.append("score = score + ?")
                    else:
                        updates.append(f"{column_name} = ?")
                    params.append(column_value)

                if not updates:
                    return self.get_memory(memory_id)

                updates.append("updated_at = ?")
                params.append(now)
                params.append(memory_id)

                cursor.execute(
                    f"UPDATE memories SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
            finally:
                cursor.close()

        return self.get_memory(memory_id)

    def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory. Returns True if it existed."""
        with self._lock, self._connect() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT tags FROM memories WHERE id = ?", (memory_id,))
                row = cursor.fetchone()
                if row is None:
                    return False
                tags = json.loads(row["tags"])
                cursor.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
                cursor.execute("DELETE FROM tag_memories WHERE memory_id = ?", (memory_id,))
                for tag in tags:
                    self._decrement_tag_count(tag, conn=conn)
            finally:
                cursor.close()
        return True

    def list_memories(
        self,
        project_id: str | None = None,
        source: str | None = None,
        tag: str | None = None,
        limit: int = 50,
        offset: int = 0,
        project_ids: list[str] | None = None,
    ) -> list[MemoryEntry]:
        """List memories with optional filters."""
        conditions: list[str] = []
        params: list[Any] = []

        if project_ids is not None:
            if project_ids:
                placeholders = ",".join("?" * len(project_ids))
                conditions.append(f"project_id IN ({placeholders})")
                params.extend(project_ids)
            else:
                conditions.append("1=0")
        elif project_id:
            conditions.append("project_id = ?")
            params.append(project_id)
        if source:
            conditions.append("source = ?")
            params.append(source)
        if tag:
            subquery = "SELECT memory_id FROM tag_memories WHERE tag_name = ?"
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
            cursor = conn.cursor()
            try:
                stats.total_memories = cursor.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
                stats.total_projects = cursor.execute(
                    "SELECT COUNT(DISTINCT project_id) FROM memories WHERE project_id != ''"
                ).fetchone()[0]
                stats.total_tags = cursor.execute("SELECT COUNT(*) FROM tags").fetchone()[0]

                rows = cursor.execute("SELECT source, COUNT(*) as cnt FROM memories GROUP BY source").fetchall()
                stats.memories_by_source = {row["source"]: row["cnt"] for row in rows}

                rows = cursor.execute(
                    "SELECT name, memory_count FROM tags ORDER BY memory_count DESC LIMIT 20"
                ).fetchall()
                stats.top_tags = [(row["name"], row["memory_count"]) for row in rows]
            finally:
                cursor.close()
        return stats

    # ------------------------------------------------------------------
    # Tag helpers
    # ------------------------------------------------------------------

    def _get_memory_tags(self, memory_id: str, conn: sqlite3.Connection | None = None) -> list[str]:
        if conn:
            cursor = conn.cursor()
            try:
                rows = cursor.execute("SELECT tag_name FROM tag_memories WHERE memory_id = ?", (memory_id,)).fetchall()
                return [row["tag_name"] for row in rows]
            finally:
                cursor.close()
        with self._connect() as local_conn:
            cursor = local_conn.cursor()
            try:
                rows = cursor.execute("SELECT tag_name FROM tag_memories WHERE memory_id = ?", (memory_id,)).fetchall()
                return [row["tag_name"] for row in rows]
            finally:
                cursor.close()

    def _increment_tag_count(self, tag: str, conn: sqlite3.Connection | None = None) -> None:
        if conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """INSERT INTO tags (name, memory_count) VALUES (?, 1)
                       ON CONFLICT(name) DO UPDATE SET memory_count = memory_count + 1""",
                    (tag,),
                )
            finally:
                cursor.close()
        else:
            with self._connect() as local_conn:
                cursor = local_conn.cursor()
                try:
                    cursor.execute(
                        """INSERT INTO tags (name, memory_count) VALUES (?, 1)
                           ON CONFLICT(name) DO UPDATE SET memory_count = memory_count + 1""",
                        (tag,),
                    )
                finally:
                    cursor.close()

    def _decrement_tag_count(self, tag: str, conn: sqlite3.Connection | None = None) -> None:
        if conn:
            cursor = conn.cursor()
            try:
                cursor.execute("UPDATE tags SET memory_count = memory_count - 1 WHERE name = ?", (tag,))
                cursor.execute("DELETE FROM tags WHERE name = ? AND memory_count <= 0", (tag,))
            finally:
                cursor.close()
        else:
            with self._connect() as local_conn:
                cursor = local_conn.cursor()
                try:
                    cursor.execute("UPDATE tags SET memory_count = memory_count - 1 WHERE name = ?", (tag,))
                    cursor.execute("DELETE FROM tags WHERE name = ? AND memory_count <= 0", (tag,))
                finally:
                    cursor.close()

    def increment_score(self, memory_id: str, amount: float = 1.0) -> None:
        """Increment the usage score of a memory (boosts it in search results)."""
        with self._lock, self._connect() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("UPDATE memories SET score = score + ? WHERE id = ?", (amount, memory_id))
            finally:
                cursor.close()

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
            embedding_blob=row["embedding"],
        )

    # ------------------------------------------------------------------
    # Embedding helpers
    # ------------------------------------------------------------------

    def compute_embedding(self, text: str) -> np.ndarray | None:
        """Compute an embedding for text using the configured embedder."""
        return self._embedder.embed(text)

    def cosine_similarity(self, vector_a: np.ndarray, vector_b: np.ndarray) -> float:
        """Compute cosine similarity between two vectors, returning 0.0 on shape mismatch or zero norm."""
        if vector_a.shape != vector_b.shape:
            return 0.0
        norm_a = np.linalg.norm(vector_a)
        norm_b = np.linalg.norm(vector_b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(vector_a, vector_b) / (norm_a * norm_b))
