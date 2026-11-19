"""Tests for proactive context injection.

Date detection is LLM-driven via the ``event_date`` / ``recurring`` columns —
no regex parsing of prose.  Tests insert structured data directly.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone, date

import aiosqlite

from pairtner.core.proactive import (
    get_proactive_injections,
    _find_date_facts,
    _find_stale_active,
)
from pairtner.storage.helpers import _to_iso


@pytest.fixture
async def db(tmp_path):
    from pairtner.db import init_db, create_user
    path = str(tmp_path / "test.db")
    await init_db(path)
    await create_user(path, 1, "Alice")
    return path


class TestDateFacts:
    @pytest.mark.asyncio
    async def test_finds_birthday_via_event_date(self, db):
        """Date facts are found by the structured event_date column, not regex."""
        tomorrow = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                "INSERT INTO facts (owner_id, text, visibility, kind, status, event_date, recurring) "
                "VALUES (1, 'Partner birthday', 'private', 'core', 'active', ?, 1)",
                (tomorrow,),
            )
            await conn.commit()

        today = date.today()
        results = await _find_date_facts(db, 1, today, lookahead_days=7)
        assert len(results) == 1
        assert results[0][0] == "Partner birthday"

    @pytest.mark.asyncio
    async def test_recurring_wraps_to_next_year(self, db):
        """A recurring date that already passed this year returns next year."""
        past = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                "INSERT INTO facts (owner_id, text, visibility, kind, status, event_date, recurring) "
                "VALUES (1, 'Anniversary', 'private', 'core', 'active', ?, 1)",
                (past,),
            )
            await conn.commit()

        today = date.today()
        results = await _find_date_facts(db, 1, today, lookahead_days=7)
        # Past recurring date wraps to next year — outside 7-day window
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_one_time_deadline(self, db):
        """Non-recurring dates (deadlines) use the exact date."""
        in_3_days = (date.today() + timedelta(days=3)).strftime("%Y-%m-%d")
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                "INSERT INTO facts (owner_id, text, visibility, kind, status, event_date, recurring) "
                "VALUES (1, 'Project deadline', 'private', 'working', 'active', ?, 0)",
                (in_3_days,),
            )
            await conn.commit()

        today = date.today()
        results = await _find_date_facts(db, 1, today, lookahead_days=7)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_ignores_archived(self, db):
        tomorrow = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                "INSERT INTO facts (owner_id, text, visibility, kind, status, event_date, recurring, archived_at) "
                "VALUES (1, 'Old birthday', 'private', 'core', 'active', ?, 1, ?)",
                (tomorrow, _to_iso(datetime.now(timezone.utc))),
            )
            await conn.commit()

        today = date.today()
        results = await _find_date_facts(db, 1, today, lookahead_days=7)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_ignores_facts_without_event_date(self, db):
        """Facts without event_date are NOT scanned (no regex fallback)."""
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                "INSERT INTO facts (owner_id, text, visibility, kind, status) "
                "VALUES (1, 'Partner birthday: 2027-01-20', 'private', 'core', 'active')",
            )
            await conn.commit()

        today = date.today()
        results = await _find_date_facts(db, 1, today, lookahead_days=7)
        # No event_date column set → not found (LLM must set it during extraction)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_today_injection(self, db):
        """A date matching today produces a TODAY marker."""
        today_str = date.today().strftime("%Y-%m-%d")
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                "INSERT INTO facts (owner_id, text, visibility, kind, status, event_date, recurring) "
                "VALUES (1, 'Wedding anniversary', 'private', 'core', 'active', ?, 1)",
                (today_str,),
            )
            await conn.commit()

        injections = await get_proactive_injections(db, 1)
        assert any("TODAY" in line for line in injections)


class TestStaleActive:
    @pytest.mark.asyncio
    async def test_finds_high_intensity_stale(self, db):
        old_date = _to_iso(datetime.now(timezone.utc) - timedelta(days=10))
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                "INSERT INTO facts (owner_id, text, visibility, kind, status, intensity, created_at) "
                "VALUES (1, 'Ongoing tension about household chores', 'private', 'working', 'active', 0.8, ?)",
                (old_date,),
            )
            await conn.commit()

        results = await _find_stale_active(db, 1, stale_days=5)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_ignores_low_intensity(self, db):
        old_date = _to_iso(datetime.now(timezone.utc) - timedelta(days=10))
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                "INSERT INTO facts (owner_id, text, visibility, kind, status, intensity, created_at) "
                "VALUES (1, 'Mentioned liking pizza', 'private', 'working', 'active', 0.3, ?)",
                (old_date,),
            )
            await conn.commit()

        results = await _find_stale_active(db, 1, stale_days=5)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_ignores_recently_cited(self, db):
        old_date = _to_iso(datetime.now(timezone.utc) - timedelta(days=10))
        recent_cite = _to_iso(datetime.now(timezone.utc) - timedelta(days=1))
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                "INSERT INTO facts (owner_id, text, visibility, kind, status, intensity, created_at, last_cited_at) "
                "VALUES (1, 'Tension discussed recently', 'private', 'working', 'active', 0.9, ?, ?)",
                (old_date, recent_cite),
            )
            await conn.commit()

        results = await _find_stale_active(db, 1, stale_days=5)
        assert len(results) == 0


class TestProactiveInjections:
    @pytest.mark.asyncio
    async def test_birthday_injection(self, db):
        tomorrow = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                "INSERT INTO facts (owner_id, text, visibility, kind, status, event_date, recurring) "
                "VALUES (1, 'Partner birthday', 'private', 'core', 'active', ?, 1)",
                (tomorrow,),
            )
            await conn.commit()

        injections = await get_proactive_injections(db, 1)
        assert any("TOMORROW" in line for line in injections)

    @pytest.mark.asyncio
    async def test_stale_conflict_injection(self, db):
        old_date = _to_iso(datetime.now(timezone.utc) - timedelta(days=7))
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                "INSERT INTO facts (owner_id, text, visibility, kind, status, intensity, created_at) "
                "VALUES (1, 'Argument about finances', 'private', 'working', 'active', 0.9, ?)",
                (old_date,),
            )
            await conn.commit()

        injections = await get_proactive_injections(db, 1, conflict_stale_days=5)
        assert any("Unresolved" in line for line in injections)

    @pytest.mark.asyncio
    async def test_no_injections_when_clean(self, db):
        injections = await get_proactive_injections(db, 1)
        assert injections == []

    @pytest.mark.asyncio
    async def test_multiple_injections(self, db):
        """Both date and conflict injections can fire together."""
        tomorrow = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
        old_date = _to_iso(datetime.now(timezone.utc) - timedelta(days=10))
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                "INSERT INTO facts (owner_id, text, visibility, kind, status, event_date, recurring) "
                "VALUES (1, 'Birthday', 'private', 'core', 'active', ?, 1)",
                (tomorrow,),
            )
            await conn.execute(
                "INSERT INTO facts (owner_id, text, visibility, kind, status, intensity, created_at) "
                "VALUES (1, 'Ongoing fight', 'private', 'working', 'active', 0.9, ?)",
                (old_date,),
            )
            await conn.commit()

        injections = await get_proactive_injections(db, 1, conflict_stale_days=5)
        assert len(injections) == 2
        assert any("TOMORROW" in line for line in injections)
        assert any("Unresolved" in line for line in injections)
