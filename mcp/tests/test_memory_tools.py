"""The memory tools end to end: tools → HTTP client → server → SQLite."""

import json
import os

import pytest

from aim_mcp.client import AimServerError
from aim_mcp.user_config import NotRegisteredError

pytestmark = pytest.mark.anyio

KEY_A = "conversation-alpha-0001"
KEY_B = "conversation-beta-0002"


async def register(tools, key=KEY_A, name="Nova"):
    return await tools.register(key, name, "chat", "claude", machine="PC-EXAMPLE")


async def test_store_search_and_is_mine(tools, other_tools):
    await register(tools)
    await register(other_tools, key=KEY_B, name="Other")

    mine = await tools.store_memory(
        KEY_A, "FACT", "Supabase is the backend", tags=["Backend", "database"],
        metadata={"why": "beta"},
    )
    assert mine["memory_id"] == 1
    assert mine["is_mine"] is True
    assert mine["tags"] == ["backend", "database"]
    assert mine["creator_id"] == tools.config.identity_for(KEY_A).participant_id

    theirs = await other_tools.store_memory(
        KEY_B, "DECISION", "QR validation uses SHA-256", tags=["qr"], confidence=0.8,
    )
    assert theirs["is_mine"] is True

    found = await tools.search_memories(KEY_A, tags=["backend"])
    assert found["total_results"] == 1
    assert found["memories"][0]["memory_id"] == 1
    assert found["memories"][0]["is_mine"] is True
    assert found["framing"]

    everything = await tools.search_memories(KEY_A, memory_types=["FACT", "DECISION"])
    assert [m["is_mine"] for m in everything["memories"]] == [False, True]  # newest first

    confident = await tools.search_memories(KEY_A, min_confidence=0.9)
    assert [m["memory_id"] for m in confident["memories"]] == [1]


async def test_get_update_supersede_dispute_and_context(tools):
    await register(tools)
    old = await tools.store_memory(KEY_A, "DECISION", "We use Redis for caching")
    new = await tools.store_memory(KEY_A, "DECISION", "We use PostgreSQL caching")
    fact = await tools.store_memory(KEY_A, "FACT", "The beta ships on Friday", project_id=7)

    updated = await tools.update_memory(KEY_A, fact["memory_id"], confidence=0.5, tags=["beta"])
    assert updated["confidence"] == 0.5
    assert updated["tags"] == ["beta"]

    result = await tools.supersede_memory(
        KEY_A, old["memory_id"], new["memory_id"], reason="simpler ops"
    )
    assert result["superseded"]["status"] == "SUPERSEDED"
    assert result["superseded"]["is_mine"] is True
    assert result["superseding"]["supersedes"] == old["memory_id"]

    fetched = await tools.get_memory(KEY_A, old["memory_id"])
    assert fetched["superseded_by"] == new["memory_id"]
    assert fetched["framing"]

    disputed = await tools.dispute_memory(
        KEY_A, new["memory_id"], "ops disagree", conflicting_memory_id=fact["memory_id"]
    )
    assert disputed["status"] == "DISPUTED"

    unscoped = await tools.project_context(KEY_A)
    assert unscoped["total_memories"] == 1  # the superseded one is history
    assert [m["memory_id"] for m in unscoped["key_decisions"]] == [new["memory_id"]]
    assert unscoped["key_decisions"][0]["is_mine"] is True
    assert unscoped["framing"]

    scoped = await tools.project_context(KEY_A, project_id=7)
    assert [m["memory_id"] for m in scoped["key_facts"]] == [fact["memory_id"]]


async def test_server_refusals_reach_the_agent_as_actionable_errors(tools):
    await register(tools)
    memory = await tools.store_memory(KEY_A, "FACT", "x")

    with pytest.raises(AimServerError) as missing:
        await tools.get_memory(KEY_A, 999)
    assert missing.value.code == "unknown_memory"
    assert "never reused" in str(missing.value)

    with pytest.raises(AimServerError) as conflict:
        await tools.supersede_memory(KEY_A, memory["memory_id"], memory["memory_id"])
    assert conflict.value.code == "memory_conflict"
    assert "itself" in str(conflict.value)

    with pytest.raises(AimServerError) as bogus:
        await tools.store_memory(KEY_A, "FACT", "x", source_message_id=999)
    assert "source_message_id 999" in str(bogus.value)


async def test_memory_calls_need_a_registered_identity(tools):
    with pytest.raises(NotRegisteredError):
        await tools.search_memories(KEY_A)


async def test_the_mcp_server_exposes_the_memory_tools(monkeypatch):
    monkeypatch.setenv("AIM_SERVER_URL", "http://aim.test")
    from aim_mcp.server import mcp

    names = {tool.name for tool in await mcp.list_tools()}
    assert {
        "aim_store_memory", "aim_search_memories", "aim_get_memory",
        "aim_update_memory", "aim_supersede_memory", "aim_dispute_memory",
        "aim_project_context",
    } <= names

    store = next(tool for tool in await mcp.list_tools() if tool.name == "aim_store_memory")
    schema = store.inputSchema
    assert "client_session_key" in schema["required"]
    assert "memory_type" in schema["required"]
    memory_type = schema["properties"]["memory_type"]
    assert set(memory_type.get("enum", [])) == {"FACT", "DECISION", "CONTEXT", "KNOWLEDGE"}
    assert os.environ["AIM_SERVER_URL"]  # the import must not have needed a real server
    json.dumps(schema)  # serializable, as the protocol requires
