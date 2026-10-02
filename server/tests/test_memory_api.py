"""The Memory Layer over HTTP: identified calls, provenance, framing, and
the store's refusals mapped to explicit statuses."""

import pytest

from aim_server.main import EMPTY_MEMORIES_NOTICE, FRAMING, create_app
from test_api import AuthenticatingClient, register


@pytest.fixture()
def client(tmp_path):
    app = create_app(str(tmp_path / "memory-api.db"))
    with AuthenticatingClient(app) as test_client:
        yield test_client


def store(client, pid, content="Supabase is the backend", memory_type="FACT", **extra):
    response = client.post(
        "/memories",
        json={"participant_id": pid, "memory_type": memory_type, "content": content, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_storing_records_the_caller_as_creator_and_needs_the_token(client):
    nova = register(client)["participant_id"]

    memory = store(client, nova, tags=["Backend", "backend"], metadata={"why": "beta"})

    assert memory["memory_id"] == 1
    assert memory["creator_id"] == nova
    assert memory["status"] == "ACTIVE"
    assert memory["tags"] == ["backend"]
    assert memory["metadata"] == {"why": "beta"}
    assert memory["server_version"]

    # The numeric ID is public; without the token nobody writes as #1 (§4.8).
    refused = client.post(
        "/memories",
        json={"participant_id": nova, "memory_type": "FACT", "content": "forged"},
        headers={"X-AIM-Token": "wrong"},
    )
    assert refused.status_code == 401
    assert refused.json()["detail"]["code"] == "token_invalid"


def test_the_source_message_must_exist(client):
    nova = register(client)["participant_id"]
    client.post("/chats", json={"participant_id": nova, "name": "general"})
    sent = client.post(
        "/chats/1/messages", json={"sender_id": nova, "text": "we chose Supabase"}
    ).json()

    memory = store(client, nova, source_message_id=sent["id"])
    assert memory["source_message_id"] == sent["id"]

    bogus = client.post(
        "/memories",
        json={"participant_id": nova, "memory_type": "FACT", "content": "x", "source_message_id": 999},
    )
    assert bogus.status_code == 422
    assert "source_message_id 999" in bogus.json()["detail"]


def test_unknown_fields_and_bad_values_are_refused_not_ignored(client):
    nova = register(client)["participant_id"]
    for body in (
        {"participant_id": nova, "memory_type": "FACT", "content": "x", "importance": 5},
        {"participant_id": nova, "memory_type": "OPINION", "content": "x"},
        {"participant_id": nova, "memory_type": "FACT", "content": "x", "confidence": 1.5},
        {"participant_id": nova, "memory_type": "FACT", "content": "x", "tags": ["ok", " "]},
    ):
        assert client.post("/memories", json=body).status_code == 422, body


def test_search_filters_and_frames_agent_written_content(client):
    nova = register(client)["participant_id"]
    store(client, nova, "Supabase is the backend", tags=["backend", "database"])
    store(client, nova, "QR validation uses SHA-256", memory_type="DECISION", tags=["qr"])
    store(client, nova, "100% sure about PostgreSQL", confidence=0.4, project_id=7)

    everything = client.get("/memories", params={"participant_id": nova}).json()
    assert everything["total_results"] == 3
    assert everything["count"] == 3
    assert everything["framing"] == FRAMING
    assert everything["notice"] is None
    assert [m["memory_id"] for m in everything["memories"]] == [3, 2, 1]  # newest first

    by_tags = client.get(
        "/memories", params={"participant_id": nova, "tag": ["Backend", "database"]}
    ).json()
    assert [m["memory_id"] for m in by_tags["memories"]] == [1]

    by_type = client.get(
        "/memories", params={"participant_id": nova, "memory_type": ["DECISION"]}
    ).json()
    assert [m["memory_id"] for m in by_type["memories"]] == [2]

    literal = client.get("/memories", params={"participant_id": nova, "query": "100%"}).json()
    assert [m["memory_id"] for m in literal["memories"]] == [3]

    confident = client.get(
        "/memories", params={"participant_id": nova, "min_confidence": 0.5}
    ).json()
    assert confident["total_results"] == 2

    scoped = client.get("/memories", params={"participant_id": nova, "project_id": 7}).json()
    assert [m["memory_id"] for m in scoped["memories"]] == [3]

    page = client.get(
        "/memories", params={"participant_id": nova, "limit": 2, "offset": 2}
    ).json()
    assert page["total_results"] == 3
    assert [m["memory_id"] for m in page["memories"]] == [1]

    nothing = client.get("/memories", params={"participant_id": nova, "query": "zzz"}).json()
    assert nothing["notice"] == EMPTY_MEMORIES_NOTICE

    # An unknown filter is version skew, never silently ignored (§7.4).
    skew = client.get("/memories", params={"participant_id": nova, "embedding": "x"})
    assert skew.status_code == 422
    assert "embedding" in skew.json()["detail"]


def test_get_update_and_the_404_a_client_can_recognize(client):
    nova = register(client)["participant_id"]
    memory = store(client, nova, tags=["a"])

    fetched = client.get(f"/memories/{memory['memory_id']}", params={"participant_id": nova}).json()
    assert fetched["content"] == "Supabase is the backend"
    assert fetched["framing"] == FRAMING

    updated = client.patch(
        f"/memories/{memory['memory_id']}",
        json={"participant_id": nova, "confidence": 0.5, "tags": ["b", "B"]},
    ).json()
    assert updated["confidence"] == 0.5
    assert updated["tags"] == ["b"]
    assert updated["updated_at"] > memory["updated_at"]

    for response in (
        client.get("/memories/999", params={"participant_id": nova}),
        client.patch("/memories/999", json={"participant_id": nova, "content": "x"}),
    ):
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "unknown_memory"
        assert response.json()["detail"]["memory_id"] == 999


def test_supersession_keeps_history_and_refuses_nonsense(client):
    nova = register(client)["participant_id"]
    old = store(client, nova, "We use Redis for caching", memory_type="DECISION")
    new = store(client, nova, "We use PostgreSQL caching", memory_type="DECISION")

    result = client.post(
        f"/memories/{old['memory_id']}/supersede",
        json={"participant_id": nova, "superseding_memory_id": new["memory_id"], "reason": "simpler ops"},
    )
    assert result.status_code == 200
    assert result.json()["superseded"]["status"] == "SUPERSEDED"
    assert result.json()["superseded"]["superseded_by"] == new["memory_id"]
    assert result.json()["superseding"]["supersedes"] == old["memory_id"]

    # Demoted out of the default search, reachable by explicit status.
    current = client.get("/memories", params={"participant_id": nova}).json()
    assert [m["memory_id"] for m in current["memories"]] == [new["memory_id"]]
    history = client.get(
        "/memories", params={"participant_id": nova, "status": "SUPERSEDED"}
    ).json()
    assert [m["memory_id"] for m in history["memories"]] == [old["memory_id"]]

    itself = client.post(
        f"/memories/{new['memory_id']}/supersede",
        json={"participant_id": nova, "superseding_memory_id": new["memory_id"]},
    )
    assert itself.status_code == 409
    assert itself.json()["detail"]["code"] == "memory_conflict"

    missing = client.post(
        f"/memories/{new['memory_id']}/supersede",
        json={"participant_id": nova, "superseding_memory_id": 999},
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "unknown_memory"


def test_dispute_and_project_context(client):
    nova = register(client)["participant_id"]
    other = register(client, name="Other")["participant_id"]
    supabase = store(client, nova, "Supabase is the backend")
    postgres = store(client, other, "PostgreSQL is the backend")
    store(client, nova, "Ship the beta on Friday", memory_type="DECISION")
    store(client, nova, "Project-7 context", memory_type="CONTEXT", project_id=7)

    disputed = client.post(
        f"/memories/{supabase['memory_id']}/dispute",
        json={
            "participant_id": other,
            "conflicting_memory_id": postgres["memory_id"],
            "reason": "which one is it?",
        },
    )
    assert disputed.status_code == 200
    assert disputed.json()["status"] == "DISPUTED"

    context = client.get("/memories/context", params={"participant_id": nova}).json()
    assert context["project_id"] is None
    assert context["total_memories"] == 3  # the project-7 memory is another scope
    assert [m["memory_id"] for m in context["key_facts"]] == [postgres["memory_id"], supabase["memory_id"]]
    assert len(context["key_decisions"]) == 1
    assert context["active_context"] == []
    assert "**Key Decisions:**" in context["summary"]
    assert context["framing"] == FRAMING

    scoped = client.get(
        "/memories/context", params={"participant_id": nova, "project_id": 7}
    ).json()
    assert scoped["total_memories"] == 1
    assert [m["content"] for m in scoped["active_context"]] == ["Project-7 context"]

    empty = client.get(
        "/memories/context", params={"participant_id": nova, "project_id": 8}
    ).json()
    assert empty["total_memories"] == 0
    assert empty["notice"] == EMPTY_MEMORIES_NOTICE


def test_memory_reads_are_identified_too(client):
    nova = register(client)["participant_id"]
    store(client, nova)
    for response in (
        client.get("/memories", params={"participant_id": nova}, headers={"X-AIM-Token": "nope"}),
        client.get("/memories/1", params={"participant_id": nova}, headers={"X-AIM-Token": "nope"}),
        client.get("/memories/context", params={"participant_id": nova}, headers={"X-AIM-Token": "nope"}),
    ):
        assert response.status_code == 401
