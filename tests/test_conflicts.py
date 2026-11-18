"""Tests for conflict resolution tracking (FEAT-002)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from pairtner import db as db_mod
from pairtner.config import Config
from pairtner.features.conflict_detector import (
    check_conflict_resolutions,
    detect_conflict_opening,
    detect_conflict_resolution,
    extract_conflict_theme,
)
from pairtner.handlers import BotService

from tests.helpers import MockLLMClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def conflict_db(tmp_path) -> str:
    db_path = str(tmp_path / "conflict_test.db")
    await db_mod.init_db(db_path)
    # Set up paired users
    await db_mod.create_user(db_path, 1001, "Alice")
    await db_mod.create_user(db_path, 1002, "Bob")
    await db_mod.set_partner(db_path, 1001, 1002)
    return db_path


@pytest.fixture
def couple_id() -> str:
    return db_mod.make_couple_id(1001, 1002)


@pytest.fixture
def conflict_config() -> Config:
    return Config(
        telegram_token="test-token",
        gemini_api_key="test-key",
        db_path=":memory:",
        conflicts_enabled=True,
        conflicts_auto_detect=True,
        conflicts_resolution_timeout_hours=48,
    )


# ---------------------------------------------------------------------------
# DB CRUD tests
# ---------------------------------------------------------------------------


class TestConflictCRUD:
    """Test conflict table CRUD operations."""

    async def test_create_conflict(self, conflict_db, couple_id):
        cid = await db_mod.create_conflict(conflict_db, couple_id, "finances")
        assert cid > 0

        conflict = await db_mod.get_conflict_by_id(conflict_db, cid)
        assert conflict is not None
        assert conflict["couple_id"] == couple_id
        assert conflict["theme"] == "finances"
        assert conflict["status"] == "open"
        assert conflict["relay_ids"] is None

    async def test_get_open_conflicts(self, conflict_db, couple_id):
        await db_mod.create_conflict(conflict_db, couple_id, "finances")
        await db_mod.create_conflict(conflict_db, couple_id, "chores")

        open_conflicts = await db_mod.get_open_conflicts(conflict_db, couple_id)
        assert len(open_conflicts) == 2
        themes = {c["theme"] for c in open_conflicts}
        assert themes == {"finances", "chores"}

    async def test_get_open_conflicts_excludes_resolved(self, conflict_db, couple_id):
        c1 = await db_mod.create_conflict(conflict_db, couple_id, "finances")
        await db_mod.create_conflict(conflict_db, couple_id, "chores")
        await db_mod.update_conflict_status(
            conflict_db, c1, "resolved",
            resolved_at=datetime.now(timezone.utc).isoformat(),
        )

        open_conflicts = await db_mod.get_open_conflicts(conflict_db, couple_id)
        assert len(open_conflicts) == 1
        assert open_conflicts[0]["theme"] == "chores"

    async def test_update_conflict_status(self, conflict_db, couple_id):
        cid = await db_mod.create_conflict(conflict_db, couple_id, "finances")
        now = datetime.now(timezone.utc).isoformat()
        await db_mod.update_conflict_status(conflict_db, cid, "resolved", resolved_at=now)

        conflict = await db_mod.get_conflict_by_id(conflict_db, cid)
        assert conflict["status"] == "resolved"
        assert conflict["resolved_at"] is not None

    async def test_add_relay_to_conflict(self, conflict_db, couple_id):
        cid = await db_mod.create_conflict(conflict_db, couple_id, "finances")

        await db_mod.add_relay_to_conflict(conflict_db, cid, 10)
        await db_mod.add_relay_to_conflict(conflict_db, cid, 20)
        # Duplicate should not be added
        await db_mod.add_relay_to_conflict(conflict_db, cid, 10)

        conflict = await db_mod.get_conflict_by_id(conflict_db, cid)
        relay_ids = json.loads(conflict["relay_ids"])
        assert relay_ids == [10, 20]

    async def test_get_conflict_summary(self, conflict_db, couple_id):
        c1 = await db_mod.create_conflict(conflict_db, couple_id, "finances")
        await db_mod.create_conflict(conflict_db, couple_id, "chores")
        await db_mod.create_conflict(conflict_db, couple_id, "kids")

        await db_mod.update_conflict_status(conflict_db, c1, "resolved")

        summary = await db_mod.get_conflict_summary(conflict_db, couple_id)
        assert summary["open"] == 2
        assert summary["resolved"] == 1

    async def test_get_conflict_summary_empty(self, conflict_db, couple_id):
        summary = await db_mod.get_conflict_summary(conflict_db, couple_id)
        assert summary == {}

    async def test_get_recent_pending_count(self, conflict_db):
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).strftime("%Y-%m-%d %H:%M:%S")

        # Create some pending rows
        await db_mod.create_pending(conflict_db, 1001, 1002, "msg 1")
        await db_mod.create_pending(conflict_db, 1001, 1002, "msg 2")
        await db_mod.create_pending(conflict_db, 1001, 1002, "msg 3")

        count = await db_mod.get_recent_pending_count(conflict_db, 1001, cutoff)
        assert count == 3

    async def test_get_recent_pending_count_excludes_old(self, conflict_db):
        # A future cutoff should exclude everything
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        await db_mod.create_pending(conflict_db, 1001, 1002, "old msg")
        count = await db_mod.get_recent_pending_count(conflict_db, 1001, future)
        assert count == 0


# ---------------------------------------------------------------------------
# Conflict detection tests
# ---------------------------------------------------------------------------


class TestConflictDetection:
    """Test automatic conflict opening detection."""

    async def test_detect_harsh_tone_angry_mood(self, conflict_db, couple_id):
        response = {
            "reply": "ok",
            "detected_mood": "angry",
            "partner_themes": ["communication breakdown"],
        }
        is_conflict, theme = await detect_conflict_opening(
            conflict_db, couple_id, 1001, response,
        )
        assert is_conflict is True
        assert theme == "communication breakdown"

    async def test_detect_harsh_tone_horsemen(self, conflict_db, couple_id):
        response = {
            "reply": "ok",
            "detected_mood": "neutral",
            "horsemen_detected": [
                {"type": "criticism", "evidence": "you always do this"},
            ],
            "partner_themes": [],
        }
        is_conflict, theme = await detect_conflict_opening(
            conflict_db, couple_id, 1001, response,
        )
        assert is_conflict is True
        assert theme == "criticism"

    async def test_detect_relay_spike(self, conflict_db, couple_id):
        # Create >3 pending rows in the last hour
        for i in range(4):
            await db_mod.create_pending(conflict_db, 1001, 1002, f"msg {i}")

        response = {
            "reply": "ok",
            "detected_mood": "neutral",
            "partner_themes": ["heated discussion"],
        }
        is_conflict, theme = await detect_conflict_opening(
            conflict_db, couple_id, 1001, response,
        )
        assert is_conflict is True
        assert theme == "heated discussion"

    async def test_no_conflict_on_normal_message(self, conflict_db, couple_id):
        response = {
            "reply": "sounds good",
            "detected_mood": "happy",
            "horsemen_detected": [],
            "partner_themes": [],
            "patterns_observed": [],
        }
        is_conflict, theme = await detect_conflict_opening(
            conflict_db, couple_id, 1001, response,
        )
        assert is_conflict is False
        assert theme is None

    async def test_no_duplicate_conflict_same_theme(self, conflict_db, couple_id):
        # Create an existing open conflict
        await db_mod.create_conflict(conflict_db, couple_id, "finances")

        response = {
            "reply": "ok",
            "detected_mood": "angry",
            "partner_themes": ["finances"],
        }
        is_conflict, theme = await detect_conflict_opening(
            conflict_db, couple_id, 1001, response,
        )
        # Should not create a duplicate
        assert is_conflict is False

    async def test_new_conflict_different_theme(self, conflict_db, couple_id):
        await db_mod.create_conflict(conflict_db, couple_id, "finances")

        response = {
            "reply": "ok",
            "detected_mood": "angry",
            "partner_themes": ["parenting"],
        }
        is_conflict, theme = await detect_conflict_opening(
            conflict_db, couple_id, 1001, response,
        )
        assert is_conflict is True
        assert theme == "parenting"


# ---------------------------------------------------------------------------
# Conflict resolution tests
# ---------------------------------------------------------------------------


class TestConflictResolution:
    """Test conflict resolution detection."""

    async def test_resolve_on_positive_mood_shift(self, conflict_db, couple_id):
        cid = await db_mod.create_conflict(conflict_db, couple_id, "finances")

        response = {
            "reply": "feeling better",
            "detected_mood": "happy",
            "horsemen_detected": [],
        }
        resolved = await detect_conflict_resolution(
            conflict_db, cid, llm_response=response,
        )
        assert resolved is True

    async def test_no_resolve_on_angry_mood(self, conflict_db, couple_id):
        cid = await db_mod.create_conflict(conflict_db, couple_id, "finances")

        response = {
            "reply": "still upset",
            "detected_mood": "angry",
            "horsemen_detected": [],
        }
        resolved = await detect_conflict_resolution(
            conflict_db, cid, llm_response=response,
        )
        assert resolved is False

    async def test_resolve_on_timeout(self, conflict_db, couple_id):
        cid = await db_mod.create_conflict(conflict_db, couple_id, "finances")

        # Manually set opened_at to 49 hours ago
        old_time = (datetime.now(timezone.utc) - timedelta(hours=49)).isoformat()
        await db_mod._execute(
            conflict_db,
            "UPDATE conflicts SET opened_at = ? WHERE id = ?",
            (old_time, cid),
        )

        resolved = await detect_conflict_resolution(conflict_db, cid)
        assert resolved is True

    async def test_no_resolve_before_timeout(self, conflict_db, couple_id):
        cid = await db_mod.create_conflict(conflict_db, couple_id, "finances")

        # opened_at is now, timeout is 48h — should not resolve
        resolved = await detect_conflict_resolution(conflict_db, cid)
        assert resolved is False

    async def test_no_resolve_on_positive_with_horsemen(self, conflict_db, couple_id):
        cid = await db_mod.create_conflict(conflict_db, couple_id, "finances")

        response = {
            "reply": "maybe ok",
            "detected_mood": "happy",
            "horsemen_detected": [{"type": "criticism"}],
        }
        resolved = await detect_conflict_resolution(
            conflict_db, cid, llm_response=response,
        )
        assert resolved is False

    async def test_check_conflict_resolutions_batch(self, conflict_db, couple_id):
        c1 = await db_mod.create_conflict(conflict_db, couple_id, "finances")
        c2 = await db_mod.create_conflict(conflict_db, couple_id, "chores")

        # Make c1 old enough to timeout
        old_time = (datetime.now(timezone.utc) - timedelta(hours=49)).isoformat()
        await db_mod._execute(
            conflict_db,
            "UPDATE conflicts SET opened_at = ? WHERE id = ?",
            (old_time, c1),
        )

        resolved_count = await check_conflict_resolutions(conflict_db, couple_id)
        assert resolved_count == 1

        # Verify c1 is resolved, c2 still open
        conflict1 = await db_mod.get_conflict_by_id(conflict_db, c1)
        assert conflict1["status"] == "resolved"
        conflict2 = await db_mod.get_conflict_by_id(conflict_db, c2)
        assert conflict2["status"] == "open"

    async def test_resolve_already_resolved_returns_false(self, conflict_db, couple_id):
        cid = await db_mod.create_conflict(conflict_db, couple_id, "finances")
        await db_mod.update_conflict_status(conflict_db, cid, "resolved")

        resolved = await detect_conflict_resolution(conflict_db, cid)
        assert resolved is False


# ---------------------------------------------------------------------------
# extract_conflict_theme tests
# ---------------------------------------------------------------------------


class TestExtractTheme:
    """Test theme extraction from LLM responses."""

    def test_from_partner_themes(self):
        response = {"partner_themes": ["finances", "work"]}
        assert extract_conflict_theme(response) == "finances"

    def test_from_patterns_observed(self):
        response = {"partner_themes": [], "patterns_observed": ["avoidance"]}
        assert extract_conflict_theme(response) == "avoidance"

    def test_from_horsemen(self):
        response = {
            "partner_themes": [],
            "patterns_observed": [],
            "horsemen_detected": [{"type": "stonewalling"}],
        }
        assert extract_conflict_theme(response) == "stonewalling"

    def test_fallback(self):
        response = {
            "partner_themes": [],
            "patterns_observed": [],
            "horsemen_detected": [],
        }
        assert extract_conflict_theme(response) == "unresolved tension"

    def test_empty_response(self):
        assert extract_conflict_theme({}) == "unresolved tension"


# ---------------------------------------------------------------------------
# /conflicts command tests
# ---------------------------------------------------------------------------


class TestConflictsCommand:
    """Test the /conflicts command handler."""

    @pytest_asyncio.fixture
    async def cmd_service(self, conflict_db, conflict_config):
        cfg = Config(
            telegram_token=conflict_config.telegram_token,
            gemini_api_key=conflict_config.gemini_api_key,
            db_path=conflict_db,
            conflicts_enabled=True,
            conflicts_auto_detect=True,
            conflicts_resolution_timeout_hours=48,
        )
        return BotService(config=cfg, db_path=conflict_db, llm=MockLLMClient())

    async def test_conflicts_unpaired(self, cmd_service, conflict_db):
        await db_mod.create_user(conflict_db, 9999, "Solo")
        responses = await cmd_service.handle_conflicts(9999)
        assert len(responses) == 1
        assert "not_paired" in responses[0].text.lower() or "pair" in responses[0].text.lower()

    async def test_conflicts_no_open(self, cmd_service, couple_id):
        responses = await cmd_service.handle_conflicts(1001)
        assert len(responses) == 1
        assert "🌟" in responses[0].text

    async def test_conflicts_with_open(self, cmd_service, conflict_db, couple_id):
        await db_mod.create_conflict(conflict_db, couple_id, "money stuff")
        await db_mod.create_conflict(conflict_db, couple_id, "housework")

        responses = await cmd_service.handle_conflicts(1001)
        assert len(responses) == 1
        text = responses[0].text
        assert "money stuff" in text
        assert "housework" in text

    async def test_conflicts_shows_relay_count(self, cmd_service, conflict_db, couple_id):
        cid = await db_mod.create_conflict(conflict_db, couple_id, "money stuff")
        await db_mod.add_relay_to_conflict(conflict_db, cid, 1)
        await db_mod.add_relay_to_conflict(conflict_db, cid, 2)

        responses = await cmd_service.handle_conflicts(1001)
        assert "2 relays" in responses[0].text


# ---------------------------------------------------------------------------
# Integration with message flow tests
# ---------------------------------------------------------------------------


class TestConflictMessageIntegration:
    """Test conflict detection fires during message handling."""

    @pytest_asyncio.fixture
    async def int_service(self, conflict_db, conflict_config):
        cfg = Config(
            telegram_token=conflict_config.telegram_token,
            gemini_api_key=conflict_config.gemini_api_key,
            db_path=conflict_db,
            conflicts_enabled=True,
            conflicts_auto_detect=True,
            conflicts_resolution_timeout_hours=48,
        )
        mock_llm = MockLLMClient(responses=[
            {
                "reply": "I can see you're upset",
                "new_facts": [],
                "relay_draft": "I need to talk about this",
                "detected_mood": "angry",
                "horsemen_detected": [{"type": "criticism", "evidence": "you always"}],
                "partner_themes": ["communication breakdown"],
                "patterns_observed": [],
                "articulation_note": None,
            },
        ])
        return BotService(config=cfg, db_path=conflict_db, llm=mock_llm)

    async def test_conflict_created_on_angry_send(
        self, int_service, conflict_db, couple_id,
    ):
        responses = await int_service.handle_message(1001, "Alice", "#send you never listen to me")
        # Verify a conflict was created
        conflicts = await db_mod.get_open_conflicts(conflict_db, couple_id)
        assert len(conflicts) >= 1
        assert conflicts[0]["theme"] == "communication breakdown"

    async def test_no_conflict_on_normal_send(self, conflict_db, conflict_config):
        cfg = Config(
            telegram_token=conflict_config.telegram_token,
            gemini_api_key=conflict_config.gemini_api_key,
            db_path=conflict_db,
            conflicts_enabled=True,
            conflicts_auto_detect=True,
            conflicts_resolution_timeout_hours=48,
        )
        mock_llm = MockLLMClient(responses=[
            {
                "reply": "That sounds nice",
                "new_facts": [],
                "relay_draft": "I love you",
                "detected_mood": "happy",
                "horsemen_detected": [],
                "partner_themes": [],
                "patterns_observed": [],
                "articulation_note": None,
            },
        ])
        svc = BotService(config=cfg, db_path=conflict_db, llm=mock_llm)
        await svc.handle_message(1001, "Alice", "#send I love you")

        couple_id = db_mod.make_couple_id(1001, 1002)
        conflicts = await db_mod.get_open_conflicts(conflict_db, couple_id)
        assert len(conflicts) == 0

    async def test_no_conflict_when_disabled(self, conflict_db, conflict_config):
        cfg = Config(
            telegram_token=conflict_config.telegram_token,
            gemini_api_key=conflict_config.gemini_api_key,
            db_path=conflict_db,
            conflicts_enabled=False,
            conflicts_auto_detect=True,
            conflicts_resolution_timeout_hours=48,
        )
        mock_llm = MockLLMClient(responses=[
            {
                "reply": "I see",
                "new_facts": [],
                "relay_draft": "harsh message",
                "detected_mood": "angry",
                "horsemen_detected": [{"type": "criticism"}],
                "partner_themes": ["conflict topic"],
                "patterns_observed": [],
                "articulation_note": None,
            },
        ])
        svc = BotService(config=cfg, db_path=conflict_db, llm=mock_llm)
        await svc.handle_message(1001, "Alice", "#send you never listen")

        couple_id = db_mod.make_couple_id(1001, 1002)
        conflicts = await db_mod.get_open_conflicts(conflict_db, couple_id)
        assert len(conflicts) == 0


# ---------------------------------------------------------------------------
# Weekly check-in integration tests
# ---------------------------------------------------------------------------


class TestConflictCheckinIntegration:
    """Test conflict summary in weekly check-in."""

    async def test_checkin_includes_conflict_summary(self, conflict_db, couple_id):
        # Create some conflicts
        c1 = await db_mod.create_conflict(conflict_db, couple_id, "finances")
        await db_mod.create_conflict(conflict_db, couple_id, "chores")
        await db_mod.update_conflict_status(
            conflict_db, c1, "resolved",
            resolved_at=datetime.now(timezone.utc).isoformat(),
        )

        summary = await db_mod.get_conflict_summary(conflict_db, couple_id)
        assert summary["resolved"] == 1
        assert summary["open"] == 1

    async def test_checkin_no_conflicts(self, conflict_db, couple_id):
        summary = await db_mod.get_conflict_summary(conflict_db, couple_id)
        assert summary == {}
