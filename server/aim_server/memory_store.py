"""Memory Store service layer - operations for the Memory Layer.

Implements CRUD operations and search for memories with provenance tracking,
supersession, and dispute handling.

Every write is one transaction (`with self.conn:`): a validation or
constraint failure rolls everything back and leaves the connection clean.
The alternative — statements executed one by one and committed at the end —
left a half-applied status change in an open transaction that the next
unrelated write would commit.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Iterable, Optional

from aim_server.db import now_utc
from aim_server.memory_models import (
    MemoryType,
    MemoryStatus,
    MemoryResponse,
    StoreMemoryRequest,
    UpdateMemoryRequest,
    SupersedeMemoryRequest,
    DisputeMemoryRequest,
    SearchMemoriesRequest,
    ProjectContextResponse,
)

# What a default search and the project context consider "current".
CURRENT_STATUSES = (MemoryStatus.ACTIVE.value, MemoryStatus.DISPUTED.value)
_CURRENT_SQL = "status IN ('ACTIVE', 'DISPUTED')"

# Characters that mean something to LIKE and must be escaped in a user query.
_LIKE_ESCAPE = "\\"

SUMMARY_SNIPPET_CHARS = 100


class MemoryRefused(ValueError):
    """A memory operation was refused. Subclasses say why, so the HTTP layer
    can answer with the right status instead of guessing from prose."""


class MemoryNotFound(MemoryRefused):
    def __init__(self, memory_id: int):
        super().__init__(f"Memory {memory_id} not found")
        self.memory_id = memory_id


class MemoryConflict(MemoryRefused):
    """The memories exist but the operation makes no sense on them."""


def _like_pattern(query: str) -> str:
    """A substring pattern in which `%`, `_` and the escape char are literal."""
    escaped = (
        query.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )
    return f"%{escaped}%"


def _scope_clause(project_id: Optional[int]) -> tuple[str, tuple]:
    """SQL for one project scope. `None` is the unscoped bucket, not a wildcard."""
    if project_id is None:
        return "project_id IS NULL", ()
    return "project_id = ?", (project_id,)


class MemoryStore:
    """Service for managing memories with provenance and semantic relationships."""

    def __init__(self, conn: sqlite3.Connection):
        """Initialize with a database connection opened by `aim_server.db.connect`."""
        self.conn = conn

    # ------------------------------------------------------------- writes

    def store_memory(
        self,
        request: StoreMemoryRequest,
        creator_id: Optional[int],
    ) -> MemoryResponse:
        """Store a new memory with classification and provenance.

        Args:
            request: StoreMemoryRequest with memory content and classification
            creator_id: Participant ID of the agent creating this memory, or
                None for a memory with no recorded author

        Returns:
            MemoryResponse with the stored memory and its ID
        """
        now = now_utc()
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO memories
                    (memory_type, content, status, confidence, source_message_id,
                     project_id, creator_id, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.memory_type.value,
                    request.content,
                    MemoryStatus.ACTIVE.value,
                    request.confidence,
                    request.source_message_id,
                    request.project_id,
                    creator_id,
                    _dump_metadata(request.metadata),
                    now,
                    now,
                ),
            )
            memory_id = cursor.lastrowid
            self._write_tags(memory_id, request.tags)
        return self._fetch_memory(memory_id)

    def update_memory(self, request: UpdateMemoryRequest) -> MemoryResponse:
        """Update an existing memory's properties.

        Fields left as None are untouched; `tags` and `metadata`, when given,
        replace the whole set. Raises MemoryNotFound if the memory does not exist.
        """
        assignments: list[str] = []
        params: list = []
        if request.content is not None:
            assignments.append("content = ?")
            params.append(request.content)
        if request.confidence is not None:
            assignments.append("confidence = ?")
            params.append(request.confidence)
        if request.status is not None:
            assignments.append("status = ?")
            params.append(request.status.value)
        if request.metadata is not None:
            assignments.append("metadata = ?")
            params.append(_dump_metadata(request.metadata))

        with self.conn:
            self._require(request.memory_id)
            if not assignments and request.tags is None:
                return self._fetch_memory(request.memory_id)  # nothing to change
            assignments.append("updated_at = ?")
            params.append(now_utc())
            params.append(request.memory_id)
            self.conn.execute(
                f"UPDATE memories SET {', '.join(assignments)} WHERE id = ?", params
            )
            if request.tags is not None:
                self.conn.execute(
                    "DELETE FROM memory_tags WHERE memory_id = ?", (request.memory_id,)
                )
                self._write_tags(request.memory_id, request.tags)
        return self._fetch_memory(request.memory_id)

    def supersede_memory(
        self, request: SupersedeMemoryRequest
    ) -> tuple[MemoryResponse, MemoryResponse]:
        """Mark one memory as superseded by another.

        Records the lineage and flips the old memory to SUPERSEDED. Refused
        (MemoryNotFound / MemoryConflict) when either memory is missing, when the two are the same
        memory, when the old one is already superseded — a memory has one
        successor, so `superseded_by` is never ambiguous — or when the new one
        is itself SUPERSEDED or ARCHIVED, since a dead memory cannot replace
        a live one.

        Returns:
            Tuple of (superseded_memory, superseding_memory)
        """
        old_id = request.superseded_memory_id
        new_id = request.superseding_memory_id
        if old_id == new_id:
            raise MemoryConflict(f"Memory {old_id} cannot supersede itself")
        with self.conn:
            old = self._require(old_id)
            new = self._require(new_id)
            if old["status"] == MemoryStatus.SUPERSEDED.value:
                raise MemoryConflict(f"Memory {old_id} is already superseded")
            if new["status"] in (
                MemoryStatus.SUPERSEDED.value,
                MemoryStatus.ARCHIVED.value,
            ):
                raise MemoryConflict(
                    f"Memory {new_id} is {new['status']} and cannot supersede another"
                )
            now = now_utc()
            self.conn.execute(
                "UPDATE memories SET status = ?, updated_at = ? WHERE id = ?",
                (MemoryStatus.SUPERSEDED.value, now, old_id),
            )
            self.conn.execute(
                """
                INSERT INTO memory_lineage (superseded_id, superseding_id, reason, recorded_at)
                VALUES (?, ?, ?, ?)
                """,
                (old_id, new_id, request.reason, now),
            )
        return self._fetch_memory(old_id), self._fetch_memory(new_id)

    def dispute_memory(self, request: DisputeMemoryRequest) -> MemoryResponse:
        """Flag a memory as disputed or contradictory.

        Records the dispute and sets the memory's status to DISPUTED so it is
        still returned by default searches but visibly needs resolution.
        Raises MemoryNotFound when the memory, or the conflicting memory if
        one is named, does not exist; MemoryConflict when a memory is
        disputed with itself.
        """
        if request.conflicting_memory_id == request.memory_id:
            raise MemoryConflict(f"Memory {request.memory_id} cannot conflict with itself")
        with self.conn:
            self._require(request.memory_id)
            if request.conflicting_memory_id is not None:
                self._require(request.conflicting_memory_id)
            now = now_utc()
            self.conn.execute(
                "UPDATE memories SET status = ?, updated_at = ? WHERE id = ?",
                (MemoryStatus.DISPUTED.value, now, request.memory_id),
            )
            self.conn.execute(
                """
                INSERT INTO memory_disputes (memory_id, conflicting_id, reason, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (request.memory_id, request.conflicting_memory_id, request.reason, now),
            )
        return self._fetch_memory(request.memory_id)

    # -------------------------------------------------------------- reads

    def search_memories(
        self, request: SearchMemoriesRequest
    ) -> tuple[int, list[MemoryResponse]]:
        """Search for memories by type, tags, text, or project.

        Returns a compact, relevant page instead of all memories: the total
        number of matches and the page selected by `limit` and `offset`.
        """
        where: list[str] = []
        params: list = []

        # Default to currently relevant memories; an explicit status filter
        # replaces that default so retained history stays reachable.
        if request.status is None:
            where.append(_CURRENT_SQL)
        else:
            where.append("status = ?")
            params.append(request.status.value)

        if request.memory_types:
            types = [t.value for t in request.memory_types]
            where.append(f"memory_type IN ({','.join('?' * len(types))})")
            params.extend(types)

        if request.project_id is not None:
            where.append("project_id = ?")
            params.append(request.project_id)

        if request.min_confidence > 0:
            where.append("confidence >= ?")
            params.append(request.min_confidence)

        if request.query:
            # Substring match for now (FTS5 is the upgrade path); the user's
            # text is data, so LIKE's own wildcards must not apply to it.
            where.append(f"content LIKE ? ESCAPE '{_LIKE_ESCAPE}'")
            params.append(_like_pattern(request.query))

        if request.tags:
            # All tags must be present. The model has already de-duplicated
            # them, so the distinct count is the length of the list.
            where.append(
                f"""id IN (
                    SELECT memory_id FROM memory_tags
                    WHERE tag IN ({','.join('?' * len(request.tags))})
                    GROUP BY memory_id
                    HAVING COUNT(DISTINCT tag) = ?
                )"""
            )
            params.extend(request.tags)
            params.append(len(request.tags))

        where_sql = " AND ".join(where)
        total = self.conn.execute(
            f"SELECT COUNT(*) AS cnt FROM memories WHERE {where_sql}", params
        ).fetchone()["cnt"]
        rows = self.conn.execute(
            f"""
            SELECT id FROM memories
            WHERE {where_sql}
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, request.limit, request.offset],
        ).fetchall()
        return total, self._fetch_memories([row["id"] for row in rows])

    def get_project_context(
        self, project_id: Optional[int] = None
    ) -> ProjectContextResponse:
        """Build a compact project context from relevant memories.

        The most recent current memories of each type for one project scope
        (`None` is the unscoped bucket): the 'compact and relevant context'
        that replaces replaying a chat history.
        """
        decisions = self._fetch_memories_by_type(MemoryType.DECISION, project_id)
        facts = self._fetch_memories_by_type(MemoryType.FACT, project_id)
        context = self._fetch_memories_by_type(MemoryType.CONTEXT, project_id)
        knowledge = self._fetch_memories_by_type(MemoryType.KNOWLEDGE, project_id)

        scope_sql, scope_params = _scope_clause(project_id)
        total = self.conn.execute(
            f"SELECT COUNT(*) AS cnt FROM memories WHERE {_CURRENT_SQL} AND {scope_sql}",
            scope_params,
        ).fetchone()["cnt"]

        return ProjectContextResponse(
            project_id=project_id,
            summary=_build_context_summary(decisions, facts, context, knowledge),
            key_decisions=decisions,
            key_facts=facts,
            active_context=context,
            derived_knowledge=knowledge,
            total_memories=total,
            last_updated=now_utc(),
        )

    def get_memory(self, memory_id: int) -> MemoryResponse:
        """One memory with its tags and lineage, or MemoryNotFound."""
        return self._fetch_memory(memory_id)

    # ----------------------------------------------------------- internals

    def _require(self, memory_id: int) -> sqlite3.Row:
        """The memory's row, or MemoryNotFound."""
        row = self.conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        if row is None:
            raise MemoryNotFound(memory_id)
        return row

    def _write_tags(self, memory_id: int, tags: Iterable[str]) -> None:
        self.conn.executemany(
            "INSERT INTO memory_tags (memory_id, tag) VALUES (?, ?)",
            [(memory_id, tag) for tag in tags],
        )

    def _fetch_memory(self, memory_id: int) -> MemoryResponse:
        """Fetch a single memory by ID with all relationships."""
        found = self._fetch_memories([memory_id])
        if not found:
            raise MemoryNotFound(memory_id)
        return found[0]

    def _fetch_memories(self, ids: list[int]) -> list[MemoryResponse]:
        """Fetch memories in the given order, with tags and lineage attached.

        Three queries for any number of memories, instead of four per memory.
        """
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        rows = {
            row["id"]: row
            for row in self.conn.execute(
                f"SELECT * FROM memories WHERE id IN ({placeholders})", ids
            ).fetchall()
        }
        tags: dict[int, list[str]] = {}
        for row in self.conn.execute(
            f"SELECT memory_id, tag FROM memory_tags "
            f"WHERE memory_id IN ({placeholders}) ORDER BY memory_id, tag",
            ids,
        ).fetchall():
            tags.setdefault(row["memory_id"], []).append(row["tag"])
        superseded_by: dict[int, int] = {}
        supersedes: dict[int, int] = {}
        for row in self.conn.execute(
            f"SELECT superseded_id, superseding_id FROM memory_lineage "
            f"WHERE superseded_id IN ({placeholders}) OR superseding_id IN ({placeholders}) "
            f"ORDER BY recorded_at, rowid",
            [*ids, *ids],
        ).fetchall():
            # A memory has at most one successor (enforced on write). It may
            # replace several predecessors; the first recorded one is shown.
            superseded_by[row["superseded_id"]] = row["superseding_id"]
            supersedes.setdefault(row["superseding_id"], row["superseded_id"])

        return [
            MemoryResponse(
                memory_id=row["id"],
                memory_type=MemoryType(row["memory_type"]),
                content=row["content"],
                status=MemoryStatus(row["status"]),
                confidence=row["confidence"],
                tags=tags.get(row["id"], []),
                source_message_id=row["source_message_id"],
                project_id=row["project_id"],
                creator_id=row["creator_id"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                superseded_by=superseded_by.get(row["id"]),
                supersedes=supersedes.get(row["id"]),
                metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            )
            for memory_id in ids
            if (row := rows.get(memory_id)) is not None
        ]

    def _fetch_memories_by_type(
        self,
        memory_type: MemoryType,
        project_id: Optional[int] = None,
        limit: int = 5,
    ) -> list[MemoryResponse]:
        """The most recent current memories of one type in one project scope."""
        scope_sql, scope_params = _scope_clause(project_id)
        rows = self.conn.execute(
            f"""
            SELECT id FROM memories
            WHERE memory_type = ? AND {_CURRENT_SQL} AND {scope_sql}
            ORDER BY created_at DESC, id DESC LIMIT ?
            """,
            (memory_type.value, *scope_params, limit),
        ).fetchall()
        return self._fetch_memories([row["id"] for row in rows])


def _dump_metadata(metadata: Optional[dict]) -> Optional[str]:
    return json.dumps(metadata) if metadata else None


def _snippet(content: str) -> str:
    if len(content) <= SUMMARY_SNIPPET_CHARS:
        return content
    return content[:SUMMARY_SNIPPET_CHARS] + "..."


def _build_context_summary(
    decisions: list[MemoryResponse],
    facts: list[MemoryResponse],
    context: list[MemoryResponse],
    knowledge: list[MemoryResponse],
) -> str:
    """A human-readable summary of the project context."""
    sections = (
        ("Key Decisions", decisions),
        ("Key Facts", facts),
        ("Active Context", context),
        ("Derived Knowledge", knowledge),
    )
    blocks = []
    for title, memories in sections:
        if memories:
            lines = [f"**{title}:**"]
            lines.extend(f"- {_snippet(mem.content)}" for mem in memories[:3])
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks) if blocks else "No memories found for this project."
