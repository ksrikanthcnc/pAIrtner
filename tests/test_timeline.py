"""Tests for relationship timeline (FEAT-004 batch4)."""

from __future__ import annotations

import pytest
import pytest_asyncio

from pairtner import db as db_mod
from pairtner.config import Config
from pairtner.db import (
    add_timeline_event,
    create_user,
    get_timeline_events,
    get_timeline_events_range,
    get_timeline_summary,
    init_db,
    set_partner,
)
from pairtner.handlers import BotService

from tests.helpers import MockLLMClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def tl_db(tmp_path) -> str:
    db_path = str(tmp_path / "tl_test.db")
    await init_db(db_path)
    return db_path


@pytest_asyncio.fixture
async def tl_paired(tl_db) -> tuple[str, int, int]:
    """Create paired users and return (db_path, user_a, user_b)."""
    a, b = 2001, 2002
    await create_user(tl_db, a, "Ravi")
    await create_user(tl_db, b, "Priya")
    await set_partner(tl_db, a, b)
    return tl_db, a, b


@pytest_asyncio.fixture
async def tl_service(tl_db) -> BotService:
    cfg = Config(
        telegram_token="test", gemini_api_key="test-key", db_path=tl_db,
    )
    return BotService(config=cfg, db_path=tl_db, llm=MockLLMClient())


# ---------------------------------------------------------------------------
# DB CRUD tests
# ---------------------------------------------------------------------------

class TestTimelineCRUD:
    async def test_add_and_get_events(self, tl_paired):
        db, a, b = tl_paired
        cid = db_mod.make_couple_id(a, b)

        eid = await add_timeline_event(db, cid, "paired", "Paired up")
        assert eid > 0

        await add_timeline_event(db, cid, "goal", "Set a goal")
        await add_timeline_event(db, cid, "relay", "First relay sent")

        events = await get_timeline_events(db, cid, limit=10)
        assert len(events) == 3
        # Most recent first by default
        assert events[0]["event_type"] == "relay"

    async def test_get_events_with_type_filter(self, tl_paired):
        db, a, b = tl_paired
        cid = db_mod.make_couple_id(a, b)

        await add_timeline_event(db, cid, "paired", "Paired")
        await add_timeline_event(db, cid, "goal", "Goal 1")
        await add_timeline_event(db, cid, "goal", "Goal 2")

        goals = await get_timeline_events(db, cid, event_type="goal")
        assert len(goals) == 2
        assert all(e["event_type"] == "goal" for e in goals)

    async def test_get_events_range(self, tl_paired):
        db, a, b = tl_paired
        cid = db_mod.make_couple_id(a, b)

        await add_timeline_event(db, cid, "paired", "Paired")
        await add_timeline_event(db, cid, "relay", "Relay sent")

        # Get all events (broad range)
        events = await get_timeline_events_range(
            db, cid, "2000-01-01", "2099-12-31",
        )
        assert len(events) == 2
        # Chronological order (ASC)
        assert events[0]["event_type"] == "paired"

    async def test_get_timeline_summary(self, tl_paired):
        db, a, b = tl_paired
        cid = db_mod.make_couple_id(a, b)

        await add_timeline_event(db, cid, "paired", "P")
        await add_timeline_event(db, cid, "goal", "G1")
        await add_timeline_event(db, cid, "goal", "G2")
        await add_timeline_event(db, cid, "milestone", "M1")

        summary = await get_timeline_summary(db, cid)
        assert summary["paired"] == 1
        assert summary["goal"] == 2
        assert summary["milestone"] == 1

    async def test_invalid_event_type_defaults(self, tl_paired):
        db, a, b = tl_paired
        cid = db_mod.make_couple_id(a, b)

        eid = await add_timeline_event(db, cid, "invalid_type", "Test")
        events = await get_timeline_events(db, cid)
        assert events[0]["event_type"] == "milestone"  # default fallback

    async def test_metadata_stored(self, tl_paired):
        db, a, b = tl_paired
        cid = db_mod.make_couple_id(a, b)

        import json
        meta = json.dumps({"source": "test"})
        await add_timeline_event(db, cid, "relay", "Relay", metadata=meta)

        events = await get_timeline_events(db, cid)
        assert events[0]["metadata"] == meta

    async def test_limit_respected(self, tl_paired):
        db, a, b = tl_paired
        cid = db_mod.make_couple_id(a, b)

        for i in range(10):
            await add_timeline_event(db, cid, "relay", f"Relay {i}")

        events = await get_timeline_events(db, cid, limit=3)
        assert len(events) == 3


# ---------------------------------------------------------------------------
# Auto-population hook tests
# ---------------------------------------------------------------------------

class TestTimelineAutoPopulation:
    async def test_pairing_creates_timeline_event(self, tl_db, tl_service):
        """Pairing via /join should record a 'paired' timeline event."""
        db = tl_db
        svc = tl_service

        # Start (creates user + code)
        resp = await svc.handle_start(3001, "Anu")
        code = None
        for r in resp:
            if "pairing code" in r.text.lower() or "<b>" in r.text:
                # Extract code from the response
                import re
                m = re.search(r"<b>([A-F0-9]+)</b>", r.text)
                if m:
                    code = m.group(1)
        assert code is not None

        # Create second user
        await create_user(db, 3002, "Kiran")

        # Join
        await svc.handle_join(3002, "Kiran", code)

        # Check timeline
        cid = db_mod.make_couple_id(3001, 3002)
        events = await get_timeline_events(db, cid)
        assert len(events) >= 1
        paired = [e for e in events if e["event_type"] == "paired"]
        assert len(paired) == 1
        assert "Kiran" in paired[0]["summary"]

    async def test_goal_creates_timeline_event(self, tl_db, tl_service):
        """Creating a goal should record a 'goal' timeline event."""
        db = tl_db
        await create_user(db, 4001, "Ram")
        await create_user(db, 4002, "Sita")
        await set_partner(db, 4001, 4002)

        svc = tl_service
        await svc.handle_goal(4001, "Be more patient")

        cid = db_mod.make_couple_id(4001, 4002)
        events = await get_timeline_events(db, cid)
        goal_events = [e for e in events if e["event_type"] == "goal"]
        assert len(goal_events) == 1
        assert "Be more patient" in goal_events[0]["summary"]

    async def test_shared_goal_creates_timeline_event(self, tl_db, tl_service):
        """Creating a shared goal should record a 'goal' timeline event."""
        db = tl_db
        await create_user(db, 4003, "Ram")
        await create_user(db, 4004, "Sita")
        await set_partner(db, 4003, 4004)

        svc = tl_service
        await svc.handle_goal(4003, "shared Weekly date night")

        cid = db_mod.make_couple_id(4003, 4004)
        events = await get_timeline_events(db, cid)
        goal_events = [e for e in events if e["event_type"] == "goal"]
        assert len(goal_events) == 1
        assert "Shared goal" in goal_events[0]["summary"]

    async def test_goal_achieved_creates_milestone_event(self, tl_db, tl_service):
        """Achieving a goal should record a 'milestone' timeline event."""
        db = tl_db
        await create_user(db, 4005, "Dev")
        await create_user(db, 4006, "Lata")
        await set_partner(db, 4005, 4006)

        goal_id = await db_mod.add_user_goal(db, 4005, "Read more books")

        svc = tl_service
        await svc.handle_callback(4005, f"ga:{goal_id}")

        cid = db_mod.make_couple_id(4005, 4006)
        events = await get_timeline_events(db, cid)
        milestones = [e for e in events if e["event_type"] == "milestone"]
        assert len(milestones) == 1
        assert "Read more books" in milestones[0]["summary"]

    async def test_shared_goal_achieved_creates_milestone(self, tl_db, tl_service):
        """Achieving a shared goal should record a 'milestone' event."""
        db = tl_db
        await create_user(db, 4007, "Arun")
        await create_user(db, 4008, "Meena")
        await set_partner(db, 4007, 4008)

        cid = db_mod.make_couple_id(4007, 4008)
        sg_id = await db_mod.add_shared_goal(db, cid, 4007, "Save for vacation")

        svc = tl_service
        await svc.handle_callback(4007, f"sg_a:{sg_id}")

        events = await get_timeline_events(db, cid)
        milestones = [e for e in events if e["event_type"] == "milestone"]
        assert len(milestones) == 1
        assert "Save for vacation" in milestones[0]["summary"]


# ---------------------------------------------------------------------------
# /timeline command tests
# ---------------------------------------------------------------------------

class TestTimelineCommand:
    async def test_timeline_not_paired(self, tl_db, tl_service):
        db = tl_db
        await create_user(db, 5001, "Alone")
        svc = tl_service
        resp = await svc.handle_timeline(5001, None)
        assert len(resp) == 1
        assert "pair" in resp[0].text.lower()

    async def test_timeline_empty(self, tl_db, tl_service):
        db = tl_db
        await create_user(db, 5002, "A")
        await create_user(db, 5003, "B")
        await set_partner(db, 5002, 5003)
        svc = tl_service
        resp = await svc.handle_timeline(5002, None)
        assert len(resp) == 1
        assert "timeline_empty" in resp[0].text or "beginning" in resp[0].text.lower()

    async def test_timeline_with_events(self, tl_db, tl_service):
        db = tl_db
        await create_user(db, 5004, "X")
        await create_user(db, 5005, "Y")
        await set_partner(db, 5004, 5005)
        cid = db_mod.make_couple_id(5004, 5005)

        await add_timeline_event(db, cid, "paired", "Paired up")
        await add_timeline_event(db, cid, "goal", "First goal set")

        svc = tl_service
        resp = await svc.handle_timeline(5004, None)
        assert len(resp) == 1
        text = resp[0].text
        assert "✨" in text  # paired emoji
        assert "🎯" in text  # goal emoji
        assert "Paired up" in text

    async def test_timeline_month_filter(self, tl_db, tl_service):
        db = tl_db
        await create_user(db, 5006, "M")
        await create_user(db, 5007, "N")
        await set_partner(db, 5006, 5007)
        cid = db_mod.make_couple_id(5006, 5007)

        await add_timeline_event(db, cid, "relay", "Monthly relay")

        svc = tl_service
        resp = await svc.handle_timeline(5006, "month")
        assert len(resp) == 1
        text = resp[0].text
        assert "💌" in text  # relay emoji
        assert "Monthly relay" in text

    async def test_timeline_month_empty(self, tl_db, tl_service):
        db = tl_db
        await create_user(db, 5008, "P")
        await create_user(db, 5009, "Q")
        await set_partner(db, 5008, 5009)

        svc = tl_service
        resp = await svc.handle_timeline(5008, "month")
        assert len(resp) == 1
        assert "empty" in resp[0].text.lower() or "beginning" in resp[0].text.lower()
