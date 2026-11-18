"""Tests for proactive context injection."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone, date

import aiosqlite

from pairtner.core.proactive import (
    get_proactive_injections,
    _parse_date,
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


class TestDateParsing:
    def test_iso_date(self):
        d = _parse_date("1992-03-15", 2027)
        assert d is not None
        assert d.month == 3 and d.day == 15

    def test_month_name(self):
        d = _parse_date("March 15", 2027)
        assert d is not None
        assert d.month == 3 and d.day == 15

    def test_abbreviated_month(self):
        d = _parse_date("Jan 5", 2027)
        assert d is not None
        assert d.month == 1 and d.day == 5

    def test_invalid(self):
        assert _parse_date("not a date", 2027) is None

    def test_recurring_next_year(self):
        """If the date already passed this year, return next year."""
        # Use a date that's definitely in the past
        past_date = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
        d = _parse_date(past_date, date.today().year)
        assert d is not None
        assert d >= date.today()


class TestDateFacts:
    @pytest.mark.asyncio
    async def test_finds_birthday(self, db):
        # Add a core fact with a birthday date
        tomorrow = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                "INSERT INTO facts (owner_id, text, visibility, kind, status) "
                "VALUES (1, ?, 'private', 'core', 'active')",
                (f"Partner birthday: {tomorrow}",),
            )
            await conn.commit()

        results = await _find_date_facts(db, 1)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_ignores_archived(self, db):
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                "INSERT INTO facts (owner_id, text, visibility, kind, status, archived_at) "
                "VALUES (1, 'DOB: 1992-03-15', 'private', 'core', 'active', ?)",
                (_to_iso(datetime.now(timezone.utc)),),
            )
            await conn.commit()

        results = await _find_date_facts(db, 1)
        assert len(results) == 0


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
                "INSERT INTO facts (owner_id, text, visibility, kind, status) "
                "VALUES (1, ?, 'private', 'core', 'active')",
                (f"Partner birthday: {tomorrow}",),
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
