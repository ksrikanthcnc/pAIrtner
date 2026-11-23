"""Tests for the memory maintenance loop and aging."""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

import aiosqlite

from pairtner.core.memory_api import MemoryAPI
from pairtner.storage.helpers import _to_iso


@pytest.fixture
async def db(tmp_path):
    from pairtner.db import init_db, create_user

    path = str(tmp_path / "test.db")
    await init_db(path)
    await create_user(path, 1, "Alice")
    return path


@pytest.fixture
def api(db):
    return MemoryAPI(db)


class TestAging:
    @pytest.mark.asyncio
    async def test_run_aging_does_not_outdate_working(self, api, db):
        """Working facts should NOT be auto-outdated. Summaries handle implicit memory."""
        old_date = _to_iso(datetime.now(timezone.utc) - timedelta(days=45))
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                "INSERT INTO facts (owner_id, text, visibility, kind, status, created_at) "
                "VALUES (1, 'Old uncited fact', 'private', 'working', 'active', ?)",
                (old_date,),
            )
            await conn.commit()

        result = await api.run_aging()
        assert result.marked_outdated == 0

        # Verify the fact is still active
        facts = await api.list_facts(1, status="active")
        assert any(f.text == "Old uncited fact" for f in facts.facts)

    @pytest.mark.asyncio
    async def test_aging_skips_core(self, api, db):
        """Core facts are never aged."""
        old_date = _to_iso(datetime.now(timezone.utc) - timedelta(days=90))
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                "INSERT INTO facts (owner_id, text, visibility, kind, status, created_at) "
                "VALUES (1, 'Core identity fact', 'private', 'core', 'active', ?)",
                (old_date,),
            )
            await conn.commit()

        await api.run_aging()

        # Core fact should still be active
        facts = await api.list_facts(1, kind="core")
        assert all(f.status == "active" for f in facts.facts)

    @pytest.mark.asyncio
    async def test_aging_skips_recently_cited(self, api, db):
        """Recently cited facts are not marked outdated even if old."""
        old_date = _to_iso(datetime.now(timezone.utc) - timedelta(days=45))
        recent_cite = _to_iso(datetime.now(timezone.utc) - timedelta(days=3))
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                "INSERT INTO facts (owner_id, text, visibility, kind, status, "
                "created_at, last_cited_at, citation_count) "
                "VALUES (1, 'Old but cited', 'private', 'working', 'active', ?, ?, 5)",
                (old_date, recent_cite),
            )
            await conn.commit()

        await api.run_aging()

        # Should still be active (recently cited)
        facts = await api.list_facts(1, status="active")
        assert any(f.text == "Old but cited" for f in facts.facts)

    @pytest.mark.asyncio
    async def test_aging_leaves_recent_alone(self, api, db):
        """Recent working facts are not touched."""
        fact = await api.add_fact(1, "Recent working fact")
        await api.run_aging()

        updated = await api.get_fact(fact.id)
        assert updated is not None
        assert updated.status == "active"


class TestRecencyReport:
    @pytest.mark.asyncio
    async def test_healthy_facts(self, api, db):
        """Facts created today are healthy."""
        await api.add_fact(1, "New fact")
        report = await api.get_recency_report(1)
        assert report.healthy == 1
        assert report.stale == 0

    @pytest.mark.asyncio
    async def test_stale_facts(self, api, db):
        """Facts older than 30 days with no citation are stale."""
        old_date = _to_iso(datetime.now(timezone.utc) - timedelta(days=45))
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                "INSERT INTO facts (owner_id, text, visibility, kind, status, created_at) "
                "VALUES (1, 'Stale fact', 'private', 'working', 'active', ?)",
                (old_date,),
            )
            await conn.commit()

        report = await api.get_recency_report(1)
        assert report.stale == 1

    @pytest.mark.asyncio
    async def test_contradiction_count(self, api, db):
        f1 = await api.add_fact(1, "Likes pizza")
        f2 = await api.add_fact(1, "Hates pizza")
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                "UPDATE facts SET contradicts_id = ? WHERE id = ?",
                (f1.id, f2.id),
            )
            await conn.commit()

        report = await api.get_recency_report(1)
        assert report.contradictions == 1
