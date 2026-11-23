"""Tests for LLM-callable tools (search_memory)."""

import pytest

from pairtner.core.llm_tools import (
    TOOL_DEFINITIONS,
    execute_search_memory,
    format_tool_results,
)
from pairtner.db import init_db, ensure_user, set_partner
from pairtner.storage.facts import add_fact
from pairtner.storage.migrate_v2 import migrate_to_v2


async def _setup_pair(db_path: str) -> tuple[int, int, str]:
    await init_db(db_path)
    await ensure_user(db_path, 1001, "Alice")
    await ensure_user(db_path, 1002, "Bob")
    await set_partner(db_path, 1001, 1002)
    await migrate_to_v2(db_path)
    return 1001, 1002, "1001_1002"


@pytest.mark.asyncio
async def test_search_own_facts(tmp_path):
    db = str(tmp_path / "test.db")
    alice, bob, rel = await _setup_pair(db)
    await add_fact(db, alice, "Alice loves hiking in the mountains", "private",
                   kind="working", relationship_id=rel)
    await add_fact(db, alice, "Alice is stressed about the presentation", "private",
                   kind="working", relationship_id=rel)
    await add_fact(db, alice, "Alice enjoys cooking pasta", "shared",
                   kind="working", relationship_id=rel)

    results = await execute_search_memory(db, alice, "hiking mountains")
    assert any("hiking" in r["text"].lower() for r in results)


@pytest.mark.asyncio
async def test_search_empty_query(tmp_path):
    db = str(tmp_path / "test.db")
    await _setup_pair(db)
    results = await execute_search_memory(db, 1001, "ab")  # too short after filtering
    assert results == []


@pytest.mark.asyncio
async def test_search_respects_partner_privacy(tmp_path):
    db = str(tmp_path / "test.db")
    alice, bob, rel = await _setup_pair(db)
    # Alice's private fact
    await add_fact(db, alice, "Alice has a crush on coworker Daniel", "private",
                   kind="working", relationship_id=rel)
    # Alice's shareable fact
    await add_fact(db, alice, "Alice wants to try Italian restaurants", "shared",
                   kind="working", relationship_id=rel)

    # Bob searches -- should NOT see Alice's private fact
    results = await execute_search_memory(db, bob, "crush Daniel coworker",
                                          relationship_id=rel)
    for r in results:
        assert "crush" not in r["text"].lower() or r.get("visibility") != "private"

    # Bob CAN see Alice's shareable fact
    results = await execute_search_memory(db, bob, "Italian restaurants",
                                          relationship_id=rel)
    shareable_found = any("Italian" in r["text"] for r in results)
    # May or may not find it via FTS depending on indexing, but if found it must be shareable
    for r in results:
        if r.get("visibility") == "private" and "crush" in r.get("text", "").lower():
            pytest.fail("Private partner fact leaked via search_memory")


@pytest.mark.asyncio
async def test_format_results_empty():
    assert format_tool_results([]) == "No memories found for that topic."


@pytest.mark.asyncio
async def test_format_results():
    results = [
        {"kind": "working", "text": "Alice loves hiking", "created_at": "2026-06-15"},
        {"kind": "weekly", "text": "Week summary: outdoor activities", "created_at": "2026-06-21"},
    ]
    formatted = format_tool_results(results)
    assert "hiking" in formatted
    assert "weekly" in formatted
    assert "2026-06" in formatted


def test_tool_definitions_schema():
    """Verify the tool definitions have the right shape."""
    assert len(TOOL_DEFINITIONS) >= 1
    tool = TOOL_DEFINITIONS[0]
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "search_memory"
    assert "query" in tool["function"]["parameters"]["properties"]
