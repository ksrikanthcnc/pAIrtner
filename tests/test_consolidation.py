"""Tests for memory consolidation v2 module."""
from __future__ import annotations

import json
import pytest
from datetime import datetime, timezone, timedelta

from pairtner import db as db_mod
from pairtner.core.consolidation import (
    run_consolidation,
    _find_consolidation_candidates,
    DEFAULT_CONFIG,
)


# ---------------------------------------------------------------------------
# _find_consolidation_candidates
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_candidates_skip_core(test_db):
    """Core memories should never be consolidation candidates."""
    await db_mod.ensure_user(test_db, 1001, "Alice")

    fid = await db_mod.add_fact(test_db, 1001, "I'm an introvert", "private")
    await db_mod.promote_to_core(test_db, fid)

    import aiosqlite
    old_date = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    async with aiosqlite.connect(test_db) as db:
        await db.execute("UPDATE facts SET created_at = ?, status = 'resolved' WHERE id = ?", (old_date, fid))
        await db.commit()

    candidates = await _find_consolidation_candidates(test_db, 1001, DEFAULT_CONFIG)
    assert len(candidates) == 0


@pytest.mark.asyncio
async def test_candidates_resolved(test_db):
    """Resolved working facts are consolidation candidates."""
    await db_mod.ensure_user(test_db, 1001, "Alice")

    fid = await db_mod.add_fact(test_db, 1001, "Mother visited for a month", "private")
    import aiosqlite
    async with aiosqlite.connect(test_db) as db:
        await db.execute("UPDATE facts SET status = 'resolved' WHERE id = ?", (fid,))
        await db.commit()

    candidates = await _find_consolidation_candidates(test_db, 1001, DEFAULT_CONFIG)
    assert len(candidates) == 1
    assert candidates[0]["id"] == fid


@pytest.mark.asyncio
async def test_candidates_active_recent_excluded(test_db):
    """Recent active working facts are not candidates."""
    await db_mod.ensure_user(test_db, 1001, "Alice")
    await db_mod.add_fact(test_db, 1001, "Active recent fact", "private")

    candidates = await _find_consolidation_candidates(test_db, 1001, DEFAULT_CONFIG)
    assert len(candidates) == 0


@pytest.mark.asyncio
async def test_candidates_outdated(test_db):
    """Outdated working facts are candidates."""
    await db_mod.ensure_user(test_db, 1001, "Alice")
    fid = await db_mod.add_fact(test_db, 1001, "Old job schedule", "private")

    import aiosqlite
    async with aiosqlite.connect(test_db) as db:
        await db.execute("UPDATE facts SET status = 'outdated' WHERE id = ?", (fid,))
        await db.commit()

    candidates = await _find_consolidation_candidates(test_db, 1001, DEFAULT_CONFIG)
    assert len(candidates) == 1


@pytest.mark.asyncio
async def test_candidates_old_uncited_active(test_db):
    """Active working facts older than 90 days with no citations are candidates."""
    await db_mod.ensure_user(test_db, 1001, "Alice")
    fid = await db_mod.add_fact(test_db, 1001, "Very old forgotten fact", "private")

    import aiosqlite
    old_date = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    async with aiosqlite.connect(test_db) as db:
        await db.execute("UPDATE facts SET created_at = ? WHERE id = ?", (old_date, fid))
        await db.commit()

    candidates = await _find_consolidation_candidates(test_db, 1001, DEFAULT_CONFIG)
    assert len(candidates) == 1


# ---------------------------------------------------------------------------
# run_consolidation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consolidation_not_enough_candidates(test_db):
    """Too few candidates -> no episodes created, no LLM call."""
    await db_mod.ensure_user(test_db, 1001, "Alice")
    fid = await db_mod.add_fact(test_db, 1001, "Old fact", "private")

    import aiosqlite
    async with aiosqlite.connect(test_db) as db:
        await db.execute("UPDATE facts SET status = 'resolved' WHERE id = ?", (fid,))
        await db.commit()

    from tests.conftest import MockLLMClient
    llm = MockLLMClient()
    episodes = await run_consolidation(test_db, llm, 1001)
    assert len(episodes) == 0
    assert len(llm.calls) == 0


@pytest.mark.asyncio
async def test_consolidation_creates_episode(test_db):
    """With enough resolved facts, creates an episode and archives sources."""
    await db_mod.ensure_user(test_db, 1001, "Alice")

    fact_ids = []
    for i in range(4):
        fid = await db_mod.add_fact(test_db, 1001, f"Resolved memory {i}", "private")
        fact_ids.append(fid)

    import aiosqlite
    async with aiosqlite.connect(test_db) as db:
        for fid in fact_ids:
            await db.execute("UPDATE facts SET status = 'resolved' WHERE id = ?", (fid,))
        await db.commit()

    # Mock LLM returns a v2 consolidation response
    from tests.conftest import MockLLMClient
    llm = MockLLMClient(responses=[{
        "episodes": [{
            "text": "Alice dealt with four resolved situations in this period.",
            "source_ids": fact_ids,
        }],
    }])

    episodes = await run_consolidation(test_db, llm, 1001)
    assert len(episodes) == 1
    assert episodes[0]["source_count"] == 4
    assert "four resolved" in episodes[0]["text"]

    # Source facts should be archived
    active = await db_mod.get_active_facts(test_db, 1001)
    active_ids = {f["id"] for f in active}
    for fid in fact_ids:
        assert fid not in active_ids, f"Source fact {fid} should be archived"

    # Episode fact should exist and be active with kind=episode
    episode_facts = [f for f in active if f.get("kind") == "episode"]
    assert len(episode_facts) == 1
    assert "four resolved" in episode_facts[0]["text"]


@pytest.mark.asyncio
async def test_consolidation_multiple_episodes(test_db):
    """LLM can return multiple topic-grouped episodes."""
    await db_mod.ensure_user(test_db, 1001, "Alice")

    ids_a = []
    ids_b = []
    for i in range(3):
        fid = await db_mod.add_fact(test_db, 1001, f"Work issue {i}", "private")
        ids_a.append(fid)
    for i in range(3):
        fid = await db_mod.add_fact(test_db, 1001, f"Home issue {i}", "private")
        ids_b.append(fid)

    import aiosqlite
    async with aiosqlite.connect(test_db) as db:
        for fid in ids_a + ids_b:
            await db.execute("UPDATE facts SET status = 'resolved' WHERE id = ?", (fid,))
        await db.commit()

    from tests.conftest import MockLLMClient
    llm = MockLLMClient(responses=[{
        "episodes": [
            {"text": "Alice dealt with several work stress issues during this period, including deadlines and conflicts.", "source_ids": ids_a},
            {"text": "Home maintenance tasks accumulated and caused some frustration between the partners.", "source_ids": ids_b},
        ],
    }])

    episodes = await run_consolidation(test_db, llm, 1001)
    assert len(episodes) == 2
