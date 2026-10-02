"""Memory Layer: the store's contract, its atomicity, and its schema's
behaviour against the rest of the server (retention, chat deletion,
participant merge)."""

import sqlite3 as sq

import pytest
from pydantic import ValidationError

from aim_server.db import SCHEMA_VERSION, connect, init_db, now_utc, purge_old_messages
from aim_server.memory_models import (
    DisputeMemoryRequest,
    MemoryStatus,
    MemoryType,
    SearchMemoriesRequest,
    StoreMemoryRequest,
    SupersedeMemoryRequest,
    UpdateMemoryRequest,
)
from aim_server.memory_store import MemoryStore


@pytest.fixture()
def conn(tmp_path):
    db_path = str(tmp_path / "memory.db")
    init_db(db_path)
    connection = connect(db_path)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture()
def store(conn):
    return MemoryStore(conn)


def fact(store, content="a fact", **kwargs):
    return store.store_memory(
        StoreMemoryRequest(memory_type=MemoryType.FACT, content=content, **kwargs),
        creator_id=None,
    )


def seed_message(conn, created_at=None) -> tuple[int, int]:
    """One participant, one chat, one message; returns (participant_id, message_id)."""
    now = now_utc()
    participant = conn.execute(
        "INSERT INTO participants (name, machine, client_type, agent_type, "
        "registered_at, token_hash) VALUES ('Nova', 'PC', 'chat', 'claude', ?, 'h')",
        (now,),
    ).lastrowid
    chat = conn.execute(
        "INSERT INTO chats (name, description, created_by, created_at) "
        "VALUES ('general', '', ?, ?)",
        (participant, now),
    ).lastrowid
    message = conn.execute(
        "INSERT INTO messages (chat_id, sender_id, text, is_introduction, created_at) "
        "VALUES (?, ?, 'hello', 0, ?)",
        (chat, participant, created_at or now),
    ).lastrowid
    conn.commit()
    return participant, message


# ------------------------------------------------------------ provenance

def test_a_memory_survives_the_retention_purge_of_its_source_message(conn, store):
    """Provenance is a pointer, not a lifeline. Before ON DELETE SET NULL the
    first memory pointing at an old message made the purge fail with a
    FOREIGN KEY error — caught and logged by the sweeper, so retention
    silently stopped for good while /health kept declaring it."""
    participant, message = seed_message(conn, created_at="2000-01-01T00:00:00.000000Z")
    memory = store.store_memory(
        StoreMemoryRequest(memory_type=MemoryType.FACT, content="old", source_message_id=message),
        creator_id=participant,
    )

    assert purge_old_messages(conn, "2001-01-01T00:00:00.000000Z") == 1

    kept = store._fetch_memory(memory.memory_id)
    assert kept.content == "old"
    assert kept.source_message_id is None


def test_deleting_a_chat_or_a_participant_keeps_their_memories(conn, store):
    participant, message = seed_message(conn)
    memory = store.store_memory(
        StoreMemoryRequest(memory_type=MemoryType.FACT, content="kept", source_message_id=message),
        creator_id=participant,
    )

    with conn:
        # What delete_chat does (§10.6) ...
        conn.execute("DELETE FROM messages WHERE chat_id = ?", (1,))
        conn.execute("DELETE FROM chats WHERE id = ?", (1,))
        # ... and what merge_participants ends with (§11.4), once the
        # participant's own rows are gone.
        conn.execute("DELETE FROM participants WHERE id = ?", (participant,))

    row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory.memory_id,)).fetchone()
    assert row["content"] == "kept"
    assert row["source_message_id"] is None
    assert row["creator_id"] is None


# ----------------------------------------------------------------- writes

def test_tags_are_normalized_and_deduplicated_on_store_and_update(store):
    memory = fact(store, tags=[" Backend", "backend", "DB "])
    assert memory.tags == ["backend", "db"]

    updated = store.update_memory(
        UpdateMemoryRequest(memory_id=memory.memory_id, tags=["b", "B", " b "])
    )
    assert updated.tags == ["b"]


def test_blank_or_oversized_tags_and_metadata_are_refused():
    with pytest.raises(ValidationError):
        StoreMemoryRequest(memory_type=MemoryType.FACT, content="x", tags=["ok", "  "])
    with pytest.raises(ValidationError):
        StoreMemoryRequest(memory_type=MemoryType.FACT, content="x", tags=["t" * 65])
    with pytest.raises(ValidationError):
        StoreMemoryRequest(memory_type=MemoryType.FACT, content="x", tags=[str(i) for i in range(21)])
    with pytest.raises(ValidationError):
        StoreMemoryRequest(memory_type=MemoryType.FACT, content="x", metadata={"blob": "A" * 9000})
    with pytest.raises(ValidationError):
        UpdateMemoryRequest(memory_id=1, metadata={"blob": "A" * 9000})


def test_tag_only_and_metadata_only_updates_are_applied(store):
    memory = fact(store, content="Original content", tags=["original"], metadata={"source": "test"})

    tagged = store.update_memory(UpdateMemoryRequest(memory_id=memory.memory_id, tags=["updated"]))
    assert tagged.tags == ["updated"]
    assert tagged.metadata == {"source": "test"}

    with_metadata = store.update_memory(
        UpdateMemoryRequest(memory_id=memory.memory_id, metadata={"source": "updated", "reviewed": True})
    )
    assert with_metadata.tags == ["updated"]
    assert with_metadata.metadata == {"source": "updated", "reviewed": True}


def test_an_empty_update_changes_nothing_not_even_updated_at(store):
    memory = fact(store)
    same = store.update_memory(UpdateMemoryRequest(memory_id=memory.memory_id))
    assert same == memory


def test_writes_against_a_missing_memory_raise_and_leave_no_open_transaction(conn, store):
    memory = fact(store)
    attempts = (
        lambda: store.update_memory(UpdateMemoryRequest(memory_id=999, content="x")),
        lambda: store.dispute_memory(
            DisputeMemoryRequest(memory_id=memory.memory_id, conflicting_memory_id=999, reason="r")
        ),
        lambda: store.dispute_memory(DisputeMemoryRequest(memory_id=999, reason="r")),
        lambda: store.supersede_memory(
            SupersedeMemoryRequest(superseded_memory_id=memory.memory_id, superseding_memory_id=999)
        ),
        lambda: store.supersede_memory(
            SupersedeMemoryRequest(superseded_memory_id=999, superseding_memory_id=memory.memory_id)
        ),
    )
    for attempt in attempts:
        with pytest.raises(ValueError):
            attempt()
        assert not conn.in_transaction

    # The failed dispute above ran its UPDATE before the FK check could ever
    # bite; the memory must not have been left DISPUTED by an unrelated
    # commit afterwards.
    fact(store, content="unrelated write")
    assert store._fetch_memory(memory.memory_id).status is MemoryStatus.ACTIVE
    assert conn.execute("SELECT COUNT(*) FROM memory_disputes").fetchone()[0] == 0


def test_supersession_is_validated_and_recorded_once(store):
    old = fact(store, content="old")
    new = fact(store, content="new")
    other = fact(store, content="other")

    with pytest.raises(ValueError, match="itself"):
        store.supersede_memory(
            SupersedeMemoryRequest(superseded_memory_id=old.memory_id, superseding_memory_id=old.memory_id)
        )

    superseded, superseding = store.supersede_memory(
        SupersedeMemoryRequest(
            superseded_memory_id=old.memory_id, superseding_memory_id=new.memory_id, reason="evolved"
        )
    )
    assert superseded.status is MemoryStatus.SUPERSEDED
    assert superseded.superseded_by == new.memory_id
    assert superseded.updated_at > old.updated_at
    assert superseding.supersedes == old.memory_id
    assert superseding.status is MemoryStatus.ACTIVE

    # One successor per memory: superseded_by is never ambiguous.
    with pytest.raises(ValueError, match="already superseded"):
        store.supersede_memory(
            SupersedeMemoryRequest(superseded_memory_id=old.memory_id, superseding_memory_id=other.memory_id)
        )
    # A dead memory cannot replace a live one.
    with pytest.raises(ValueError, match="SUPERSEDED"):
        store.supersede_memory(
            SupersedeMemoryRequest(superseded_memory_id=other.memory_id, superseding_memory_id=old.memory_id)
        )


def test_dispute_marks_the_memory_and_records_the_conflict(conn, store):
    one = fact(store, content="Supabase is the backend")
    two = fact(store, content="PostgreSQL is the backend")

    with pytest.raises(ValueError, match="itself"):
        store.dispute_memory(
            DisputeMemoryRequest(memory_id=one.memory_id, conflicting_memory_id=one.memory_id, reason="r")
        )

    disputed = store.dispute_memory(
        DisputeMemoryRequest(memory_id=one.memory_id, conflicting_memory_id=two.memory_id, reason="which?")
    )
    assert disputed.status is MemoryStatus.DISPUTED
    assert disputed.updated_at > one.updated_at
    row = conn.execute("SELECT * FROM memory_disputes").fetchone()
    assert (row["memory_id"], row["conflicting_id"], row["reason"]) == (one.memory_id, two.memory_id, "which?")


# ----------------------------------------------------------------- search

def test_search_status_filter_includes_historical_memories(store):
    memories = [fact(store, content=status.value) for status in MemoryStatus]
    for memory, status in zip(memories, MemoryStatus):
        store.update_memory(UpdateMemoryRequest(memory_id=memory.memory_id, status=status))

    default_count, default_results = store.search_memories(SearchMemoriesRequest())
    assert default_count == 2
    assert {memory.status for memory in default_results} == {MemoryStatus.ACTIVE, MemoryStatus.DISPUTED}

    for status in (MemoryStatus.SUPERSEDED, MemoryStatus.ARCHIVED):
        count, results = store.search_memories(SearchMemoriesRequest(status=status))
        assert count == 1
        assert [memory.status for memory in results] == [status]


def test_search_by_tags_requires_all_of_them_and_tolerates_duplicates(store):
    both = fact(store, content="both", tags=["backend", "database"])
    fact(store, content="one", tags=["backend"])

    count, results = store.search_memories(SearchMemoriesRequest(tags=["Backend", "database", "backend"]))
    assert count == 1
    assert [memory.memory_id for memory in results] == [both.memory_id]

    count, _ = store.search_memories(SearchMemoriesRequest(tags=["backend"]))
    assert count == 2


def test_search_text_is_a_literal_substring_not_a_like_pattern(store):
    fact(store, content="100% sure")
    fact(store, content="under_score")
    fact(store, content="plain")

    assert store.search_memories(SearchMemoriesRequest(query="%"))[0] == 1
    assert store.search_memories(SearchMemoriesRequest(query="_"))[0] == 1
    assert store.search_memories(SearchMemoriesRequest(query="100% s"))[0] == 1
    assert store.search_memories(SearchMemoriesRequest(query="zzz"))[0] == 0


def test_search_pages_through_the_total_newest_first(store):
    ids = [fact(store, content=f"m{i}").memory_id for i in range(5)]

    total, page = store.search_memories(SearchMemoriesRequest(limit=2))
    assert total == 5
    assert [m.memory_id for m in page] == ids[::-1][:2]

    total, page = store.search_memories(SearchMemoriesRequest(limit=2, offset=4))
    assert total == 5
    assert [m.memory_id for m in page] == [ids[0]]


def test_search_by_type_project_and_confidence(store):
    fact(store, content="high", project_id=7, confidence=0.9)
    fact(store, content="low", project_id=7, confidence=0.3)
    store.store_memory(
        StoreMemoryRequest(memory_type=MemoryType.DECISION, content="decision", project_id=7),
        creator_id=None,
    )

    count, results = store.search_memories(
        SearchMemoriesRequest(memory_types=[MemoryType.FACT], project_id=7, min_confidence=0.5)
    )
    assert count == 1
    assert results[0].content == "high"


# -------------------------------------------------------- project context

def test_project_context_treats_zero_as_a_project_scope(store):
    fact(store, content="Project zero memory", project_id=0)
    fact(store, content="Unscoped memory")

    context = store.get_project_context(project_id=0)

    assert context.total_memories == 1
    assert [memory.content for memory in context.key_facts] == ["Project zero memory"]


def test_project_context_summary_only_truncates_what_is_long(store):
    fact(store, content="short")
    fact(store, content="L" * 150)

    lines = store.get_project_context().summary.splitlines()

    assert "- short" in lines
    assert "- " + "L" * 100 + "..." in lines


# -------------------------------------------------------------- migration

def test_a_v4_database_gets_its_memory_references_rebuilt_and_keeps_everything(tmp_path):
    """v4 (the first cut of this layer) referenced messages and participants
    with no ON DELETE. SQLite cannot alter a constraint, so the table is
    rebuilt: rows, IDs, tags, lineage and the AUTOINCREMENT high-water mark
    must survive, and the rebuild must not cascade through the child tables."""
    db_path = str(tmp_path / "v4.db")
    init_db(db_path)
    old = sq.connect(db_path)
    old.executescript(
        """
        DROP TABLE memory_disputes; DROP TABLE memory_tags; DROP TABLE memory_lineage; DROP TABLE memories;
        CREATE TABLE memories (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_type        TEXT NOT NULL CHECK (memory_type IN ('FACT', 'DECISION', 'CONTEXT', 'KNOWLEDGE')),
            content            TEXT NOT NULL,
            status             TEXT NOT NULL DEFAULT 'ACTIVE'
                CHECK (status IN ('ACTIVE', 'SUPERSEDED', 'ARCHIVED', 'DISPUTED')),
            confidence         REAL NOT NULL DEFAULT 0.95 CHECK (confidence >= 0.0 AND confidence <= 1.0),
            source_message_id  INTEGER REFERENCES messages(id),
            project_id         INTEGER,
            creator_id         INTEGER REFERENCES participants(id),
            metadata           TEXT,
            created_at         TEXT NOT NULL,
            updated_at         TEXT NOT NULL
        );
        CREATE TABLE memory_lineage (
            superseded_id  INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
            superseding_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
            reason TEXT, recorded_at TEXT NOT NULL, PRIMARY KEY (superseded_id, superseding_id)
        );
        CREATE TABLE memory_tags (
            memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
            tag TEXT NOT NULL, PRIMARY KEY (memory_id, tag)
        );
        CREATE TABLE memory_disputes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
            conflicting_id INTEGER REFERENCES memories(id) ON DELETE SET NULL,
            reason TEXT NOT NULL, created_at TEXT NOT NULL
        );
        INSERT INTO memories (id, memory_type, content, created_at, updated_at)
            VALUES (1, 'FACT', 'one', 't', 't'), (2, 'DECISION', 'two', 't', 't'), (3, 'FACT', 'three', 't', 't');
        DELETE FROM memories WHERE id = 3;  -- high-water mark is 3, rows stop at 2
        INSERT INTO memory_tags VALUES (1, 'alpha'), (2, 'beta');
        INSERT INTO memory_lineage VALUES (1, 2, 'evolved', 't');
        INSERT INTO memory_disputes (memory_id, conflicting_id, reason, created_at) VALUES (2, 1, 'r', 't');
        PRAGMA user_version = 4;
        """
    )
    old.commit()
    old.close()

    init_db(db_path)

    conn = connect(db_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'memories'").fetchone()["sql"]
        assert sql.count("ON DELETE SET NULL") == 2
        assert [r["content"] for r in conn.execute("SELECT content FROM memories ORDER BY id")] == ["one", "two"]
        assert conn.execute("SELECT COUNT(*) FROM memory_tags").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM memory_lineage").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM memory_disputes").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        # IDs are never reused: the next memory is 4, not 3.
        assert MemoryStore(conn).store_memory(
            StoreMemoryRequest(memory_type=MemoryType.FACT, content="four"), creator_id=None
        ).memory_id == 4
        assert MemoryStore(conn)._fetch_memory(1).superseded_by == 2
    finally:
        conn.close()
