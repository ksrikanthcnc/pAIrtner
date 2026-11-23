"""Tests for the nudge system — generation, appreciation ratio, quiet hours, loop."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from pairtner import db as db_mod
from pairtner.features.nudges import (
    _is_quiet_hours,
    _nudge_cycle,
    check_appreciation_ratio,
    generate_appreciation_prompt,
    generate_nudge,
    nudge_loop,
)
from tests.helpers import MockLLMClient


# ---------------------------------------------------------------------------
# Quiet hours
# ---------------------------------------------------------------------------

class TestQuietHours:
    def test_within_active_hours(self):
        now = datetime(2025, 1, 1, 12, 0)
        assert _is_quiet_hours(now, "09:00", "21:00") is False

    def test_outside_active_hours_morning(self):
        now = datetime(2025, 1, 1, 7, 0)
        assert _is_quiet_hours(now, "09:00", "21:00") is True

    def test_outside_active_hours_night(self):
        now = datetime(2025, 1, 1, 22, 0)
        assert _is_quiet_hours(now, "09:00", "21:00") is True

    def test_at_boundary_start(self):
        now = datetime(2025, 1, 1, 9, 0)
        assert _is_quiet_hours(now, "09:00", "21:00") is False

    def test_at_boundary_end(self):
        now = datetime(2025, 1, 1, 21, 0)
        assert _is_quiet_hours(now, "09:00", "21:00") is True

    def test_invalid_format(self):
        now = datetime(2025, 1, 1, 12, 0)
        assert _is_quiet_hours(now, "invalid", "21:00") is False


# ---------------------------------------------------------------------------
# Appreciation ratio
# ---------------------------------------------------------------------------

class TestAppreciationRatio:
    def test_below_threshold(self):
        assert check_appreciation_ratio(3, 2, 5.0) is True

    def test_above_threshold(self):
        assert check_appreciation_ratio(10, 1, 5.0) is False

    def test_at_threshold(self):
        assert check_appreciation_ratio(5, 1, 5.0) is False

    def test_no_negatives(self):
        assert check_appreciation_ratio(10, 0, 5.0) is False

    def test_zero_positives(self):
        assert check_appreciation_ratio(0, 3, 5.0) is True


# ---------------------------------------------------------------------------
# Nudge generation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_nudge_with_positive_facts(test_db, paired_users):
    a_id, b_id = paired_users
    # Add positive shared facts
    await db_mod.add_fact(
        test_db, a_id, "We love hiking together", "shared",
        tag="relationship",
    )

    mock_llm = MockLLMClient([
        {"reply": "Remember that amazing hike? Maybe plan another one!", "new_facts": [], "relay_draft": None},
    ])
    result = await generate_nudge(test_db, mock_llm, a_id, b_id)
    assert "hike" in result.lower()
    assert len(mock_llm.calls) == 1


@pytest.mark.asyncio
async def test_generate_nudge_without_facts(test_db):
    a_id, b_id = 2001, 2002
    await db_mod.create_user(test_db, a_id, "Cara")
    await db_mod.create_user(test_db, b_id, "Dev")
    await db_mod.set_partner(test_db, a_id, b_id)
    mock_llm = MockLLMClient()

    result = await generate_nudge(test_db, mock_llm, a_id, b_id)
    # Should return a generic starter (no LLM call)
    assert result  # non-empty
    assert len(mock_llm.calls) == 0


@pytest.mark.asyncio
async def test_generate_nudge_fallback_on_empty_reply(test_db, paired_users):
    a_id, b_id = paired_users
    await db_mod.add_fact(
        test_db, a_id, "We enjoy cooking", "shared", tag="emotional",
    )

    mock_llm = MockLLMClient([
        {"reply": "", "new_facts": [], "relay_draft": None},
    ])
    result = await generate_nudge(test_db, mock_llm, a_id, b_id)
    assert result  # falls back to generic


# ---------------------------------------------------------------------------
# Appreciation prompt
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_appreciation_prompt(test_db):
    mock_llm = MockLLMClient([
        {"reply": "What's something nice about Bob?", "new_facts": [], "relay_draft": None},
    ])
    result = await generate_appreciation_prompt(
        test_db, mock_llm, 1001, "Bob",
    )
    assert "Bob" in result
    assert len(mock_llm.calls) == 1


@pytest.mark.asyncio
async def test_appreciation_prompt_fallback(test_db):
    mock_llm = MockLLMClient([
        {"reply": "", "new_facts": [], "relay_draft": None},
    ])
    result = await generate_appreciation_prompt(
        test_db, mock_llm, 1001, "Bob",
    )
    assert result  # falls back to i18n string


# ---------------------------------------------------------------------------
# Nudge loop scheduling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nudge_cycle_skips_quiet_hours(test_db, paired_users, config, monkeypatch):
    """Nudge cycle should not send anything during quiet hours."""
    sent: list[tuple[int, str]] = []

    async def mock_send(uid: int, text: str) -> None:
        sent.append((uid, text))

    # Force quiet hours by setting a narrow active window
    class FakeConfig:
        nudges_enabled = True
        appreciation_enabled = False
        nudges_quiet_start = "23:00"
        nudges_quiet_end = "23:01"
        checkin_timezone = "UTC"
        appreciation_target_ratio = 5.0
        appreciation_check_days = 7

    mock_llm = MockLLMClient()
    await _nudge_cycle(test_db, mock_llm, mock_send, FakeConfig())
    assert len(sent) == 0


@pytest.mark.asyncio
async def test_nudge_cycle_sends_during_active_hours(test_db, paired_users, monkeypatch):
    """Nudge cycle sends nudges during active hours."""
    sent: list[tuple[int, str]] = []

    async def mock_send(uid: int, text: str) -> None:
        sent.append((uid, text))

    class FakeConfig:
        nudges_enabled = True
        appreciation_enabled = False
        nudges_quiet_start = "00:00"
        nudges_quiet_end = "23:59"
        checkin_timezone = "UTC"
        appreciation_target_ratio = 5.0
        appreciation_check_days = 7

    mock_llm = MockLLMClient()
    await _nudge_cycle(test_db, mock_llm, mock_send, FakeConfig())
    # Should have sent to both users in the pair
    assert len(sent) == 2


@pytest.mark.asyncio
async def test_nudge_cycle_appreciation_overrides_regular(test_db, paired_users):
    """When appreciation ratio is low, send appreciation prompts instead of regular nudges."""
    a_id, b_id = paired_users
    couple_id = db_mod.make_couple_id(a_id, b_id)

    # Record negative interactions to trigger low ratio
    for _ in range(5):
        await db_mod.record_interaction(test_db, couple_id, a_id, "negative")
    await db_mod.record_interaction(test_db, couple_id, a_id, "positive")

    sent: list[tuple[int, str]] = []

    async def mock_send(uid: int, text: str) -> None:
        sent.append((uid, text))

    class FakeConfig:
        nudges_enabled = True
        appreciation_enabled = True
        nudges_quiet_start = "00:00"
        nudges_quiet_end = "23:59"
        checkin_timezone = "UTC"
        appreciation_target_ratio = 5.0
        appreciation_check_days = 7

    mock_llm = MockLLMClient([
        {"reply": "Appreciate Bob", "new_facts": [], "relay_draft": None},
        {"reply": "Appreciate Alice", "new_facts": [], "relay_draft": None},
    ])
    await _nudge_cycle(test_db, mock_llm, mock_send, FakeConfig())
    # Should have sent appreciation prompts to both users
    assert len(sent) == 2
    assert all("💛" in text for _, text in sent)


@pytest.mark.asyncio
async def test_nudge_cycle_disabled(test_db, paired_users):
    """No nudges sent when both nudges and appreciation are disabled."""
    sent: list[tuple[int, str]] = []

    async def mock_send(uid: int, text: str) -> None:
        sent.append((uid, text))

    class FakeConfig:
        nudges_enabled = False
        appreciation_enabled = False
        nudges_quiet_start = "00:00"
        nudges_quiet_end = "23:59"
        checkin_timezone = "UTC"
        appreciation_target_ratio = 5.0
        appreciation_check_days = 7

    mock_llm = MockLLMClient()
    await _nudge_cycle(test_db, mock_llm, mock_send, FakeConfig())
    assert len(sent) == 0


@pytest.fixture(autouse=True)
def _reset_nudge_state():
    from pairtner.features import nudges

    nudges._nudge_state.clear()
    yield
    nudges._nudge_state.clear()


@pytest.mark.asyncio
async def test_nudge_cooldown_and_backoff(tmp_path):
    from datetime import timedelta, timezone
    from types import SimpleNamespace
    from pairtner.features import nudges

    cfg = SimpleNamespace(nudges_interval_hours=24)
    db_path = str(tmp_path / "x.db")
    await db_mod.init_db(db_path)
    assert await nudges._may_nudge(db_path, 1, cfg) is True
    await nudges._record_nudge(db_path, 1)
    assert await nudges._may_nudge(db_path, 1, cfg) is False  # cooldown
    last = datetime.now(timezone.utc) - timedelta(hours=30)
    nudges._nudge_state[1] = {"last": last, "ignored": 0}
    assert await nudges._may_nudge(db_path, 1, cfg) is True
    nudges._nudge_state[1] = {"last": last, "ignored": 2}  # 4x = 96h
    assert await nudges._may_nudge(db_path, 1, cfg) is False


@pytest.mark.asyncio
async def test_generate_nudge_uses_negative_shared_facts(test_db, paired_users):
    a_id, b_id = paired_users
    await db_mod.add_fact(
        test_db, a_id, "Feeling overwhelmed with chores", "shared", tag="household",
    )
    mock_llm = MockLLMClient([{"reply": "Thinking of you", "new_facts": [], "relay_draft": None}])
    await generate_nudge(test_db, mock_llm, b_id, a_id)
    assert "overwhelmed with chores" in mock_llm.calls[0]["user_content"]
