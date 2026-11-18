"""Tests for the relay feedback loop (FEAT-003)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from pairtner import db as db_mod
from pairtner.config import Config
from pairtner.features.feedback import (
    adjust_dampening_from_feedback,
    check_for_feedback_opportunity,
    generate_feedback_request,
)
from pairtner.handlers import BotService

from tests.helpers import MockLLMClient


@pytest_asyncio.fixture
async def feedback_db(tmp_path) -> str:
    db_path = str(tmp_path / "feedback.db")
    await db_mod.init_db(db_path)
    # Create paired users
    await db_mod.create_user(db_path, 1001, "Alice")
    await db_mod.create_user(db_path, 1002, "Bob")
    await db_mod.set_partner(db_path, 1001, 1002)
    return db_path


# ---------------------------------------------------------------------------
# check_for_feedback_opportunity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feedback_opportunity_after_delivery(feedback_db):
    """After a relay is delivered (status=sent, recent sent_at), feedback is needed."""
    pid = await db_mod.create_pending(feedback_db, 1001, 1002, "Hello gently")
    await db_mod.update_pending_status(feedback_db, pid, "approved")
    await db_mod.mark_as_sent(feedback_db, pid)

    result = await check_for_feedback_opportunity(feedback_db, 1001)
    assert result is not None
    assert result["id"] == pid


@pytest.mark.asyncio
async def test_no_feedback_when_already_given(feedback_db):
    """No opportunity when feedback is already recorded."""
    pid = await db_mod.create_pending(feedback_db, 1001, 1002, "Hello gently")
    await db_mod.update_pending_status(feedback_db, pid, "approved")
    await db_mod.mark_as_sent(feedback_db, pid)
    await db_mod.update_pending_feedback(feedback_db, pid, "yes")

    result = await check_for_feedback_opportunity(feedback_db, 1001)
    assert result is None


@pytest.mark.asyncio
async def test_no_feedback_outside_24h(feedback_db):
    """No opportunity for relays older than 24 hours."""
    pid = await db_mod.create_pending(feedback_db, 1001, 1002, "Old relay")
    await db_mod.update_pending_status(feedback_db, pid, "approved")
    # Manually set sent_at to 25 hours ago
    old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    await db_mod._execute(
        feedback_db,
        "UPDATE pending SET status = 'sent', sent_at = ? WHERE id = ?",
        (old_time, pid),
    )

    result = await check_for_feedback_opportunity(feedback_db, 1001)
    assert result is None


@pytest.mark.asyncio
async def test_no_feedback_for_unsent_relay(feedback_db):
    """Relays still awaiting approval don't need feedback."""
    await db_mod.create_pending(feedback_db, 1001, 1002, "Pending draft")

    result = await check_for_feedback_opportunity(feedback_db, 1001)
    assert result is None


# ---------------------------------------------------------------------------
# generate_feedback_request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feedback_request_format(feedback_db):
    pid = await db_mod.create_pending(feedback_db, 1001, 1002, "Hello gently")
    await db_mod.update_pending_status(feedback_db, pid, "approved")
    await db_mod.mark_as_sent(feedback_db, pid)

    pending = await db_mod.get_pending(feedback_db, pid)
    text, buttons = generate_feedback_request(pending, "en")

    assert "delivered" in text.lower()
    assert len(buttons) == 1
    assert len(buttons[0]) == 3  # yes, not_sure, no
    # Check callback data prefixes
    cb_data = [b[1] for b in buttons[0]]
    assert any(d.startswith("fb_y:") for d in cb_data)
    assert any(d.startswith("fb_m:") for d in cb_data)
    assert any(d.startswith("fb_n:") for d in cb_data)


# ---------------------------------------------------------------------------
# update_pending_feedback / get_feedback_stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feedback_recording(feedback_db):
    pid = await db_mod.create_pending(feedback_db, 1001, 1002, "Msg 1")
    await db_mod.update_pending_feedback(feedback_db, pid, "yes")

    pending = await db_mod.get_pending(feedback_db, pid)
    assert pending["feedback"] == "yes"


@pytest.mark.asyncio
async def test_feedback_stats(feedback_db):
    for _ in range(3):
        pid = await db_mod.create_pending(feedback_db, 1001, 1002, "Msg")
        await db_mod.update_pending_feedback(feedback_db, pid, "yes")

    pid = await db_mod.create_pending(feedback_db, 1001, 1002, "Msg bad")
    await db_mod.update_pending_feedback(feedback_db, pid, "no")

    stats = await db_mod.get_feedback_stats(feedback_db, 1001)
    assert stats["yes"] == 3
    assert stats["no"] == 1


# ---------------------------------------------------------------------------
# adjust_dampening_from_feedback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dampening_suggestion_positive(feedback_db):
    """Consistent 'yes' suggests lowering dampening."""
    config = Config(telegram_token="t", gemini_api_key="k")
    for _ in range(4):
        pid = await db_mod.create_pending(feedback_db, 1001, 1002, "Msg")
        await db_mod.update_pending_feedback(feedback_db, pid, "yes")

    adj = await adjust_dampening_from_feedback(feedback_db, 1001, config)
    assert adj == -0.05


@pytest.mark.asyncio
async def test_dampening_suggestion_negative(feedback_db):
    """Consistent 'no' suggests raising dampening."""
    config = Config(telegram_token="t", gemini_api_key="k")
    for _ in range(3):
        pid = await db_mod.create_pending(feedback_db, 1001, 1002, "Msg")
        await db_mod.update_pending_feedback(feedback_db, pid, "no")

    adj = await adjust_dampening_from_feedback(feedback_db, 1001, config)
    assert adj == 0.05


@pytest.mark.asyncio
async def test_dampening_suggestion_too_few(feedback_db):
    """With < 3 feedback entries, no suggestion."""
    config = Config(telegram_token="t", gemini_api_key="k")
    pid = await db_mod.create_pending(feedback_db, 1001, 1002, "Msg")
    await db_mod.update_pending_feedback(feedback_db, pid, "yes")

    adj = await adjust_dampening_from_feedback(feedback_db, 1001, config)
    assert adj is None


# ---------------------------------------------------------------------------
# Feedback callback integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feedback_callback_yes(feedback_db):
    config = Config(telegram_token="t", gemini_api_key="k", db_path=feedback_db)
    svc = BotService(config=config, db_path=feedback_db, llm=MockLLMClient())

    pid = await db_mod.create_pending(feedback_db, 1001, 1002, "Draft")
    responses = await svc.handle_callback(1001, f"fb_y:{pid}")
    assert any("feedback" in r.text.lower() or "thanks" in r.text.lower() for r in responses)

    pending = await db_mod.get_pending(feedback_db, pid)
    assert pending["feedback"] == "yes"


@pytest.mark.asyncio
async def test_feedback_callback_no(feedback_db):
    config = Config(telegram_token="t", gemini_api_key="k", db_path=feedback_db)
    svc = BotService(config=config, db_path=feedback_db, llm=MockLLMClient())

    pid = await db_mod.create_pending(feedback_db, 1001, 1002, "Draft")
    responses = await svc.handle_callback(1001, f"fb_n:{pid}")

    pending = await db_mod.get_pending(feedback_db, pid)
    assert pending["feedback"] == "no"


@pytest.mark.asyncio
async def test_feedback_callback_not_sure(feedback_db):
    config = Config(telegram_token="t", gemini_api_key="k", db_path=feedback_db)
    svc = BotService(config=config, db_path=feedback_db, llm=MockLLMClient())

    pid = await db_mod.create_pending(feedback_db, 1001, 1002, "Draft")
    responses = await svc.handle_callback(1001, f"fb_m:{pid}")

    pending = await db_mod.get_pending(feedback_db, pid)
    assert pending["feedback"] == "not_sure"


# ---------------------------------------------------------------------------
# Feedback requested on sender's next message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feedback_on_sender_next_message(feedback_db):
    """After relay delivery, sender's next plain message includes feedback request."""
    config = Config(
        telegram_token="t", gemini_api_key="k", db_path=feedback_db,
        feedback_enabled=True,
    )
    svc = BotService(config=config, db_path=feedback_db, llm=MockLLMClient())

    # Deliver a relay
    pid = await db_mod.create_pending(feedback_db, 1001, 1002, "Hello gently")
    await db_mod.update_pending_status(feedback_db, pid, "approved")
    await db_mod.mark_as_sent(feedback_db, pid)

    # Sender's next message should trigger feedback request
    responses = await svc.handle_message(1001, "Alice", "How are things?")
    assert len(responses) >= 2  # reply + feedback request
    # At least one response should have feedback buttons
    has_feedback = any(
        r.buttons and any(
            any(b.callback_data.startswith("fb_") for b in row)
            for row in r.buttons
        )
        for r in responses
    )
    assert has_feedback


@pytest.mark.asyncio
async def test_no_feedback_when_disabled(feedback_db):
    """Feedback is not requested when disabled in config."""
    config = Config(
        telegram_token="t", gemini_api_key="k", db_path=feedback_db,
        feedback_enabled=False,
    )
    svc = BotService(config=config, db_path=feedback_db, llm=MockLLMClient())

    pid = await db_mod.create_pending(feedback_db, 1001, 1002, "Hello gently")
    await db_mod.update_pending_status(feedback_db, pid, "approved")
    await db_mod.mark_as_sent(feedback_db, pid)

    responses = await svc.handle_message(1001, "Alice", "How are things?")
    # Should not have feedback buttons
    has_feedback = any(
        r.buttons and any(
            any(b.callback_data.startswith("fb_") for b in row)
            for row in r.buttons
        )
        for r in responses
    )
    assert not has_feedback
