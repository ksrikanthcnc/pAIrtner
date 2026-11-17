"""Tests for memory consolidation module."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta

from pairtner import db as db_mod
from pairtner.consolidation import (
    run_consolidation,
    _find_consolidation_candidates,
    _group_by_period,
    DEFAULT_CONFIG,
)


# ---------------------------------------------------------------------------
# _group_by_period
# ---------------------------------------------------------------------------

class TestGroupByPeriod:
    def test_groups_by_month(self):
        facts = [
            {"created_at": "2026-01-15 10:00:00"},
            {"created_at": "2026-01-20 12:00:00"},
            {"created_at": "2026-02-05 08:00:00"},
        ]
        groups = _group_by_period(facts)
        assert "2026-01" in groups
        assert "2026-02" in groups
        assert len(groups["2026-01"]) == 2
        assert len(groups["2026-02"]) == 1

    def test_handles_missing_dates(self):
        facts = [{"created_at": ""}, {"text": "no date"}]
        groups = _group_by_period(facts)
        assert "unknown" in groups


# ---------------------------------------------------------------------------
# _find_consolidation_candidates
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_candidates_skip_core(test_db):
    """Core memories should never be consolidation candidates."""
    await db_mod.ensure_user(test_db, 1001, "Alice")

    # Add a core fact with old timestamp
    fid = await db_mod.add_fact(test_db, 1001, "I'm an introvert", "private")
    await db_mod.promote_to_core(test_db, fid)

    # Manually set old created_at
    import aiosqlite
    old_date = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    async with aiosqlite.connect(test_db) as db:
        await db.execute("UPDATE facts SET created_at = ?, status = 'resolved' WHERE id = ?", (old_date, fid))
        await db.commit()

    candidates = await _find_consolidation_candidates(test_db, 1001, DEFAULT_CONFIG)
    assert len(candidates) == 0  # Core is excluded even if resolved + old


@pytest.mark.asyncio
async def test_candidates_resolved_old(test_db):
    """Resolved memories older than threshold should be candidates."""
    await db_mod.ensure_user(test_db, 1001, "Alice")

    fid = await db_mod.add_fact(test_db, 1001, "Mother visited for a month", "private")

    import aiosqlite
    old_date = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    async with aiosqlite.connect(test_db) as db:
        await db.execute(
            "UPDATE facts SET created_at = ?, status = 'resolved' WHERE id = ?",
            (old_date, fid),
        )
        await db.commit()

    candidates = await _find_consolidation_candidates(test_db, 1001, DEFAULT_CONFIG)
    assert len(candidates) == 1
    assert candidates[0]["id"] == fid


@pytest.mark.asyncio
async def test_candidates_recent_excluded(test_db):
    """Recent resolved memories should not be candidates yet."""
    await db_mod.ensure_user(test_db, 1001, "Alice")

    fid = await db_mod.add_fact(test_db, 1001, "Had a fight yesterday", "private")

    import aiosqlite
    async with aiosqlite.connect(test_db) as db:
        await db.execute("UPDATE facts SET status = 'resolved' WHERE id = ?", (fid,))
        await db.commit()

    candidates = await _find_consolidation_candidates(test_db, 1001, DEFAULT_CONFIG)
    assert len(candidates) == 0  # Too recent


# ---------------------------------------------------------------------------
# run_consolidation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consolidation_not_enough_candidates(test_db):
    """Too few candidates -> no episodes created."""
    await db_mod.ensure_user(test_db, 1001, "Alice")
    fid = await db_mod.add_fact(test_db, 1001, "Old fact", "private")

    import aiosqlite
    old_date = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    async with aiosqlite.connect(test_db) as db:
        await db.execute(
            "UPDATE facts SET created_at = ?, status = 'resolved' WHERE id = ?",
            (old_date, fid),
        )
        await db.commit()

    from tests.conftest import MockLLMClient
    llm = MockLLMClient()
    episodes = await run_consolidation(test_db, llm, 1001)
    assert len(episodes) == 0
    assert len(llm.calls) == 0  # No LLM call


@pytest.mark.asyncio
async def test_consolidation_creates_episode(test_db):
    """With enough old resolved memories, should create an episode and archive sources."""
    await db_mod.ensure_user(test_db, 1001, "Alice")

    import aiosqlite
    old_date = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%d 10:00:00")
    fact_ids = []
    for i in range(4):
        fid = await db_mod.add_fact(test_db, 1001, f"Resolved memory {i}", "private")
        fact_ids.append(fid)
        async with aiosqlite.connect(test_db) as db:
            await db.execute(
                "UPDATE facts SET created_at = ?, status = 'resolved' WHERE id = ?",
                (old_date, fid),
            )
            await db.commit()

    from tests.conftest import MockLLMClient
    llm = MockLLMClient(responses=[{
        "reply": "In late 2025, Alice dealt with four resolved situations.",
        "new_facts": [],
    }])

    episodes = await run_consolidation(test_db, llm, 1001)
    assert len(episodes) == 1
    assert episodes[0]["source_count"] == 4
    assert "resolved situations" in episodes[0]["summary"]

    # Source memories should be archived
    active = await db_mod.get_active_facts(test_db, 1001)
    source_ids = set(fact_ids)
    active_ids = {f["id"] for f in active}
    assert not source_ids.intersection(active_ids), "Source memories should be archived"

    # Episode fact should exist and be active
    episode_facts = [f for f in active if f.get("kind") == "episode"]
    assert len(episode_facts) == 1
