"""Tests for memory consolidation — archive stale facts (no episodes)."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta

from pairtner import db as db_mod
from pairtner.core.consolidation import (
    run_consolidation,
    run_cascading_consolidation,
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
# run_consolidation (archives stale facts, no episodes, no LLM)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consolidation_archives_resolved(test_db):
    """Resolved facts get archived, no LLM call needed."""
    await db_mod.ensure_user(test_db, 1001, "Alice")
    fid = await db_mod.add_fact(test_db, 1001, "Old fact", "private")

    import aiosqlite
    async with aiosqlite.connect(test_db) as db:
        await db.execute("UPDATE facts SET status = 'resolved' WHERE id = ?", (fid,))
        await db.commit()

    from tests.conftest import MockLLMClient
    llm = MockLLMClient()
    archived = await run_consolidation(test_db, llm, 1001)
    assert len(archived) == 1
    assert archived[0]["id"] == fid
    # No LLM call -- just archiving
    assert len(llm.calls) == 0

    # Fact should be archived (not in active facts)
    active = await db_mod.get_active_facts(test_db, 1001)
    assert fid not in {f["id"] for f in active}


@pytest.mark.asyncio
async def test_consolidation_archives_multiple(test_db):
    """Multiple resolved/outdated facts all get archived."""
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

    from tests.conftest import MockLLMClient
    llm = MockLLMClient()
    archived = await run_consolidation(test_db, llm, 1001)
    assert len(archived) == 4

    # All should be gone from active facts
    active = await db_mod.get_active_facts(test_db, 1001)
    active_ids = {f["id"] for f in active}
    for fid in fact_ids:
        assert fid not in active_ids


@pytest.mark.asyncio
async def test_consolidation_no_candidates(test_db):
    """No candidates -> empty result."""
    await db_mod.ensure_user(test_db, 1001, "Alice")
    await db_mod.add_fact(test_db, 1001, "Active recent fact", "private")

    from tests.conftest import MockLLMClient
    llm = MockLLMClient()
    result = await run_consolidation(test_db, llm, 1001)
    assert result == []


# ---------------------------------------------------------------------------
# run_cascading_consolidation (no-op, replaced by rolling summaries)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cascading_is_noop(test_db):
    """Cascading consolidation returns empty -- replaced by rolling summaries."""
    await db_mod.ensure_user(test_db, 1001, "Alice")

    from tests.conftest import MockLLMClient
    llm = MockLLMClient()
    result = await run_cascading_consolidation(test_db, llm, 1001)
    assert result == {"monthly": [], "quarterly": []}
    assert len(llm.calls) == 0
