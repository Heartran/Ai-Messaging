# Memory Layer Prototype - Issue #11

**Status:** Working Prototype - Classification & Storage (storage layer only: no HTTP endpoints or MCP tools yet)
**Date:** 2026-09-18, revised 2026-10-02

## Overview

This prototype implements the **Memory Layer** for AIM (AI Messaging), transforming it from a simple communication channel into a true cognitive memory system. The prototype focuses on memory classification, provenance tracking, and selective retrieval.

### Architecture

```
Agency / Agents → AIM → Memory Layer → EmbeddingGemma / Vector DB → Semantic Retrieval
```

## Features Implemented

### 1. Memory Classification
Four distinct memory types for organizing information:

- **FACT**: Stable and objective information
  - Example: "Supabase is the backend for the beta release"
  - Confidence: High (typically 0.90-0.99)

- **DECISION**: Decisions made and reasons behind them
  - Example: "QR validation uses SHA-256 + HMAC"
  - Includes reasoning metadata
  - Traceable back to source message

- **CONTEXT**: Temporary but useful information
  - Example: "Currently working on QR code integration"
  - Short-lived, can be archived
  - Used to avoid infinite chat context growth

- **KNOWLEDGE**: Derived and reflected knowledge
  - Example: "TicketOne and VivaTicket have different isActive semantics"
  - Extracted from experience/analysis
  - Used for pattern recognition

### 2. Provenance & Lifecycle

Each memory records complete lineage:

```json
{
  "memory_id": 42,
  "memory_type": "DECISION",
  "content": "Use PostgreSQL for primary database",
  "status": "ACTIVE",
  "confidence": 0.95,
  "source_message_id": 1234,
  "creator_id": 5,
  "created_at": "2026-09-18T10:54:31.000Z",
  "updated_at": "2026-09-18T10:54:31.000Z",
  "metadata": {
    "reasoning": "Better transaction support",
    "decision_date": "2026-09-18",
    "impact": "critical"
  }
}
```

### 3. Supersession (Evolution of Decisions)

Track how decisions evolve without losing history:

```
MEM-182: "We use Redis for caching"
           ↓ (superseded by)
MEM-431: "We use PostgreSQL caching with connection pooling"
           (reason: "Simpler ops, better integration")
```

Key benefits:
- Maintains decision history
- Clear lineage of reasoning
- Can revert to previous decisions if needed

### 4. Dispute Handling

Flag contradictory memories for resolution:

```
MEM-1: "Supabase is the backend"
     ↔ (disputes)
MEM-6: "PostgreSQL is the backend"
     (reason: "Unclear whether Supabase or PostgreSQL")
```

Status changes to `DISPUTED` to require investigation.

### 5. Compact Project Context

Retrieve 10-20 relevant memories instead of entire chat history:

```python
context = store.get_project_context(project_id=1)
# Returns, newest first, ACTIVE and DISPUTED only:
# - Top 5 decisions
# - Top 5 facts (stable information)
# - Top 5 context items (current work)
# - Top 5 knowledge items (patterns learned)
```

Benefits:
- Reduced token consumption (20 items vs 1000+ messages)
- Better signal-to-noise ratio
- Clearer decision rationale
- Easier continuation after context window resets

### 6. Semantic Retrieval (Foundation for Vector Search)

Multiple search methods implemented:

**By Type:**
```python
search = SearchMemoriesRequest(
    memory_types=[MemoryType.DECISION, MemoryType.FACT]
)
```

**By Tags:**
```python
search = SearchMemoriesRequest(tags=["backend", "database"])
```

**By Text (literal substring; `%` and `_` are not wildcards):**
```python
search = SearchMemoriesRequest(query="PostgreSQL")
```

**By Project:**
```python
search = SearchMemoriesRequest(project_id=1)
```

**By Confidence:**
```python
search = SearchMemoriesRequest(min_confidence=0.90)
```

## Database Schema

### Core Tables

**memories**
- `id` - Unique memory ID
- `memory_type` - FACT, DECISION, CONTEXT, KNOWLEDGE
- `content` - Text content (up to 4000 chars)
- `status` - ACTIVE, SUPERSEDED, ARCHIVED, DISPUTED
- `confidence` - 0.0-1.0 belief level
- `source_message_id` - Reference to original AIM message (set to NULL when the message is purged or its chat deleted)
- `project_id` - Optional project scope
- `creator_id` - Agent who created this memory (set to NULL when the participant is merged away)
- `metadata` - JSON for custom fields
- `created_at` / `updated_at` - Timestamps

**memory_lineage**
- Tracks supersession relationships
- `superseded_id` → `superseding_id` mapping (one successor per memory)
- Includes `reason` for evolution explanation

**memory_tags**
- For efficient tag search
- Tags are trimmed, lower-cased and de-duplicated on write; at most 20 per memory, 64 characters each
- Tag-based filtering (all requested tags must be present)

**memory_disputes**
- Records contradictions between memories
- Links conflicting memories
- Stores dispute reason and timestamp

## API Models

### Request Models

**StoreMemoryRequest**
```python
memory_type: MemoryType
content: str
source_message_id: Optional[int]
project_id: Optional[int]
confidence: float = 0.95
tags: list[str] = []          # normalized; max 20
metadata: dict = {}           # max 8000 bytes serialized
```

**SearchMemoriesRequest**
```python
query: Optional[str]
memory_types: Optional[list[MemoryType]]
tags: Optional[list[str]]
project_id: Optional[int]
status: Optional[MemoryStatus]
min_confidence: float = 0.0
limit: int = 20
offset: int = 0               # page through total_results
```

**SupersedeMemoryRequest**
```python
superseded_memory_id: int
superseding_memory_id: int
reason: Optional[str]
```

**DisputeMemoryRequest**
```python
memory_id: int
conflicting_memory_id: Optional[int]
reason: str
```

### Response Models

**MemoryResponse**
```python
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
```

**ProjectContextResponse**
```python
project_id: Optional[int]
summary: str
key_decisions: list[MemoryResponse]
key_facts: list[MemoryResponse]
active_context: list[MemoryResponse]
derived_knowledge: list[MemoryResponse]
total_memories: int
last_updated: str
```

## Running the Prototype

```bash
cd server
python demo_memory_layer.py
```

Output shows:
1. Storing 4 different memory types
2. Searching memories by type, tags, and content
3. Supersession of decisions
4. Dispute handling
5. Compact project context extraction
6. Memory updates
7. Statistics by memory type

## Files Created

```
server/
├── demo_memory_layer.py              # Runnable prototype demo (not packaged)
├── aim_server/
│   ├── memory_models.py              # Pydantic models for API
│   ├── memory_store.py               # Service layer (store, search, update)
│   └── db.py                         # (Modified) Memory Layer schema + v5 migration
└── tests/
    └── test_memory_store.py          # Store contract, atomicity, schema behaviour
```

## Design Decisions

### 0. Every write is one transaction
- `store`, `update`, `supersede` and `dispute` each run inside a single transaction
- A validation or constraint failure rolls everything back and leaves the connection clean
- Supersession is validated: both memories must exist, a memory cannot supersede itself, a memory has one successor, and a SUPERSEDED or ARCHIVED memory cannot supersede another

### 1. Confidence Levels (0.0-1.0)
- Allows agents to express belief strength
- Enables filtering by confidence threshold
- Distinguishes between "confirmed fact" and "hypothesis"

### 2. JSON Metadata
- Flexible, extensible custom fields
- Supports reasoning, impact, dates, etc.
- No schema migration needed for new fields

### 3. Separate Status Tracks
- `ACTIVE` - Current, usable memories
- `SUPERSEDED` - Old version, kept for history
- `ARCHIVED` - Permanently removed from active search
- `DISPUTED` - Requires investigation/resolution

### 4. Tags for Semantic Foundation
- Enables compound searches ("backend" AND "database")
- Foundation for vector embedding integration
- Human-readable compared to raw embeddings

### 5. Source Message Tracking
- Links memories back to original AIM messages
- Enables audit trail and verification
- Not required (agent can create standalone memories)
- A pointer, not a lifeline: the memory survives the purge of its source message (retention), the deletion of its chat, and the merge of its author. Without `ON DELETE SET NULL` the first memory pointing at an old message made the retention sweep fail forever.

## Next Steps

### Phase 2: Vector Embeddings
- Integrate EmbeddingGemma for semantic search
- Add cosine similarity ranking
- Implement hybrid search (tags + embeddings)

### Phase 3: API Integration
- Add REST endpoints to HTTP server
- Integrate with MCP protocol
- Add authentication/authorization

### Phase 4: Memory Scoping
- Separate agent memory (personal preferences/rules)
- Project memory (shared knowledge)
- AIM memory (communication history)

### Phase 5: Advanced Features
- Temporal queries (memories from last week)
- Memory coalescence (merge similar memories)
- Automatic contradiction detection
- Memory importance scoring

## Testing

`server/tests/test_memory_store.py` covers:
- ✓ Memories surviving the retention purge, chat deletion and participant deletion
- ✓ Tag normalization and the tag/metadata size limits
- ✓ Atomicity: a failed write leaves no open transaction and no half-applied status
- ✓ Supersession validation and lineage
- ✓ Dispute recording
- ✓ Search by status, type, project, confidence, tags (all-of), literal text, and paging
- ✓ Project context, including project 0 and summary truncation
- ✓ The v4 → v5 rebuild of the memories table

The demo script exercises the same features end to end.

## Performance Considerations

Current implementation uses SQLite with indexes on:
- `(memory_type, status)` - Fast type filtering
- `(project_id, status)` - Fast project filtering
- `(created_at DESC)` - Chronological ordering
- Tags table - Efficient compound tag search

Reads are batched: a search page costs three queries however many memories it returns.

For production:
- Consider PostgreSQL for concurrency
- Add full-text search (FTS5)
- Implement caching layer for hot memories

## Security

Current prototype doesn't implement:
- Access control (assumes trusted environment)
- Encryption (developer mode)
- Rate limiting
- Input sanitization beyond Pydantic validation

For production, add:
- Role-based access control
- Encryption at rest
- API rate limiting
- SQL injection prevention (prepared statements used ✓)

## Compatibility

- **Python**: 3.10+
- **Database**: SQLite 3.40+
- **Dependencies**: Pydantic, sqlite3 (stdlib)
- **Schema Version**: 5 (from 3; a v4 database created from an earlier revision of this branch is rebuilt in place)
- **Backward Compatibility**: Yes (migration path provided)

## References

- Issue #11: AIM Memory Layer
- Design document: `docs/design.md` (referenced throughout)
- Database design: `server/aim_server/db.py`
