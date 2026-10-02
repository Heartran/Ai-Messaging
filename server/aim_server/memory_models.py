"""Memory Layer data models for structured knowledge representation.

Implements the Memory Layer as proposed in Issue #11: a structured classification
system for facts, decisions, context, and knowledge with full provenance tracking.

Architecture:
    Agency / agents -> AIM -> Memory Layer -> EmbeddingGemma / Vector DB -> semantic retrieval

Memory Classification:
    - Facts: Stable and objective information
    - Decisions: Decisions made and reasons behind them
    - Context: Useful but temporary information
    - Knowledge: Derived and reflected knowledge from the project
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Bounds that keep one memory from swallowing the database. `content` was
# capped from the start; tags and metadata were not, and an unbounded JSON
# blob is still a row in the same table.
MAX_TAGS = 20
MAX_TAG_LENGTH = 64
MAX_METADATA_BYTES = 8000


def normalize_tags(tags: Optional[list[str]]) -> Optional[list[str]]:
    """Canonical tag list: trimmed, lower-cased, de-duplicated, order kept.

    'Backend' and 'backend' are one tag, not two: a search by tag is only as
    useful as the writers' consistency, and agents are not consistent.
    Empty tags are rejected rather than dropped — a blank tag is a client
    bug, and a silently-dropped parameter is the failure mode this server
    refuses everywhere else (§7.4).
    """
    if tags is None:
        return None
    seen: dict[str, None] = {}
    for raw in tags:
        tag = raw.strip().lower()
        if not tag:
            raise ValueError("tags must not be empty or whitespace")
        if len(tag) > MAX_TAG_LENGTH:
            raise ValueError(f"tag {tag[:MAX_TAG_LENGTH]!r}… exceeds {MAX_TAG_LENGTH} characters")
        seen.setdefault(tag, None)
    if len(seen) > MAX_TAGS:
        raise ValueError(f"at most {MAX_TAGS} distinct tags per memory")
    return list(seen)


def check_metadata_size(metadata: Optional[dict]) -> Optional[dict]:
    """Refuse metadata whose JSON form exceeds MAX_METADATA_BYTES."""
    if metadata is None:
        return None
    size = len(json.dumps(metadata, ensure_ascii=False).encode("utf-8"))
    if size > MAX_METADATA_BYTES:
        raise ValueError(f"metadata is {size} bytes serialized; the limit is {MAX_METADATA_BYTES}")
    return metadata


class MemoryType(str, Enum):
    """Memory classification categories."""
    FACT = "FACT"
    DECISION = "DECISION"
    CONTEXT = "CONTEXT"
    KNOWLEDGE = "KNOWLEDGE"


class MemoryStatus(str, Enum):
    """Lifecycle status of a memory."""
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"
    DISPUTED = "DISPUTED"


class StrictMemoryModel(BaseModel):
    """Base model for memory operations. Unknown fields are rejected."""
    model_config = ConfigDict(extra="forbid")


class StoreMemoryRequest(StrictMemoryModel):
    """Store a new memory in the Memory Layer."""

    memory_type: MemoryType = Field(
        description="Classification: FACT, DECISION, CONTEXT, or KNOWLEDGE"
    )
    content: str = Field(
        min_length=1,
        max_length=4000,
        description="The memory content"
    )
    source_message_id: Optional[int] = Field(
        default=None,
        description="Reference to the AIM message ID where this memory originated"
    )
    project_id: Optional[int] = Field(
        default=None,
        description="Optional project context if this memory is project-scoped"
    )
    confidence: float = Field(
        default=0.95,
        ge=0.0,
        le=1.0,
        description="Confidence level (0.0 to 1.0) of this memory being correct"
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Search tags for semantic retrieval (e.g. 'backend', 'database'). "
        "Trimmed, lower-cased and de-duplicated; at most 20."
    )
    metadata: dict = Field(
        default_factory=dict,
        description=f"Optional structured metadata (JSON, at most {MAX_METADATA_BYTES} bytes)"
    )

    @field_validator("tags")
    @classmethod
    def _tags(cls, value: list[str]) -> list[str]:
        return normalize_tags(value) or []

    @field_validator("metadata")
    @classmethod
    def _metadata(cls, value: dict) -> dict:
        return check_metadata_size(value) or {}


class UpdateMemoryRequest(StrictMemoryModel):
    """Update an existing memory's content or metadata."""

    memory_id: int = Field(description="Memory ID to update")
    content: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=4000,
        description="Updated memory content"
    )
    confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Updated confidence level"
    )
    status: Optional[MemoryStatus] = Field(
        default=None,
        description="Updated status"
    )
    tags: Optional[list[str]] = Field(
        default=None,
        description="Updated tags (replaces the whole set; same normalization as on store)"
    )
    metadata: Optional[dict] = Field(
        default=None,
        description="Updated metadata (replaces the whole object)"
    )

    @field_validator("tags")
    @classmethod
    def _tags(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        return normalize_tags(value)

    @field_validator("metadata")
    @classmethod
    def _metadata(cls, value: Optional[dict]) -> Optional[dict]:
        return check_metadata_size(value)


class SupersedeMemoryRequest(StrictMemoryModel):
    """Mark a memory as superseded by another memory."""

    superseded_memory_id: int = Field(
        description="Memory ID being superseded"
    )
    superseding_memory_id: int = Field(
        description="Memory ID that supersedes the other"
    )
    reason: Optional[str] = Field(
        default=None,
        max_length=1000,
        description="Reason for supersession (contradiction, evolution, etc)"
    )


class DisputeMemoryRequest(StrictMemoryModel):
    """Flag a memory as contradictory or disputed."""

    memory_id: int = Field(description="Memory ID being disputed")
    conflicting_memory_id: Optional[int] = Field(
        default=None,
        description="ID of the conflicting memory if known"
    )
    reason: str = Field(
        min_length=1,
        max_length=1000,
        description="Explanation of the contradiction"
    )


class SearchMemoriesRequest(StrictMemoryModel):
    """Search for memories by type, tags, or free text."""

    query: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Free text search query"
    )
    memory_types: Optional[list[MemoryType]] = Field(
        default=None,
        description="Filter by memory types"
    )
    tags: Optional[list[str]] = Field(
        default=None,
        description="Filter by tags (must match all; same normalization as on store)"
    )
    project_id: Optional[int] = Field(
        default=None,
        description="Filter by project scope"
    )
    status: Optional[MemoryStatus] = Field(
        default=None,
        description="Filter by status"
    )
    min_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Minimum confidence threshold"
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum results to return"
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Results to skip, for paging through total_results"
    )

    @field_validator("tags")
    @classmethod
    def _tags(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        return normalize_tags(value)


class MemoryResponse(StrictMemoryModel):
    """Response containing a memory record."""

    memory_id: int
    memory_type: MemoryType
    content: str
    status: MemoryStatus
    confidence: float
    tags: list[str]
    source_message_id: Optional[int]
    project_id: Optional[int]
    created_at: str
    updated_at: str
    superseded_by: Optional[int]
    supersedes: Optional[int]
    metadata: dict


class SearchMemoriesResponse(StrictMemoryModel):
    """Response containing search results."""

    query: str
    total_results: int
    results: list[MemoryResponse]


class ProjectContextResponse(StrictMemoryModel):
    """Compact project context built from memories."""

    project_id: Optional[int]
    summary: str
    key_decisions: list[MemoryResponse]
    key_facts: list[MemoryResponse]
    active_context: list[MemoryResponse]
    derived_knowledge: list[MemoryResponse]
    total_memories: int
    last_updated: str
