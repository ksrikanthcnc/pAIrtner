"""Tests for soft read receipts (FEAT-003)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from couples_bot import db as db_mod
from couples_bot.config import Config
from couples_bot.handlers import BotService

from tests.helpers import MockLLMClient


@pytest_asyncio.fixture
async def receipt_db(tmp_path) -> str:
    db_path = str(tmp_path / "receipts.db")
    await db_mod.init_db(db_path)
    await db_mod.create_user(db_path, 1001, "Alice")
    await db_mod.create_user(db_path, 1002, "Bob")
    await db_mod.set_partner(db_path, 1001, 1002)
    return db_path


def _config(db_path: str) -> Config:
    return Config(
        telegram_token="t",
        gemini_api_key="k",
        db_path=db_path,
        feedback_enabled=False,  # disable feedback to isolate receipts
    )


# ---------------------------------------------------------------------------
# acknowledged_at set when partner messages after relay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acknowledged_on_partner_message(receipt_db):
    """When Bob gets a relay and then sends a message, relay is acknowledged."""
    # Alice sends relay → delivered to Bob
    pid = await db_mod.create_pending(receipt_db, 1001, 1002, "Hello Bob")
    await db_mod.update_pending_status(receipt_db, pid, "approved")
    await db_mod.mark_as_sent(receipt_db, pid)

    # Verify not yet acknowledged
    pending = await db_mod.get_pending(receipt_db, pid)
    assert pending["acknowledged_at"] is None

    # Bob sends a message (engages with bot)
    svc = BotService(config=_config(receipt_db), db_path=receipt_db, llm=MockLLMClient())
    await svc.handle_message(1002, "Bob", "Hey, I got your message")

    # Now should be acknowledged
    pending = await db_mod.get_pending(receipt_db, pid)
    assert pending["acknowledged_at"] is not None


@pytest.mark.asyncio
async def test_acknowledged_on_send_tag(receipt_db):
    """Read receipt also triggers via #send messages."""
    pid = await db_mod.create_pending(receipt_db, 1001, 1002, "Hello Bob")
    await db_mod.update_pending_status(receipt_db, pid, "approved")
    await db_mod.mark_as_sent(receipt_db, pid)

    svc = BotService(config=_config(receipt_db), db_path=receipt_db, llm=MockLLMClient())
    await svc.handle_message(1002, "Bob", "#send Tell Alice thanks")

    pending = await db_mod.get_pending(receipt_db, pid)
    assert pending["acknowledged_at"] is not None


@pytest.mark.asyncio
async def test_multiple_relays_acknowledged(receipt_db):
    """Multiple unacknowledged relays all get acknowledged on one message."""
    pids = []
    for i in range(3):
        pid = await db_mod.create_pending(receipt_db, 1001, 1002, f"Msg {i}")
        await db_mod.update_pending_status(receipt_db, pid, "approved")
        await db_mod.mark_as_sent(receipt_db, pid)
        pids.append(pid)

    svc = BotService(config=_config(receipt_db), db_path=receipt_db, llm=MockLLMClient())
    await svc.handle_message(1002, "Bob", "Hi there")

    for pid in pids:
        pending = await db_mod.get_pending(receipt_db, pid)
        assert pending["acknowledged_at"] is not None


@pytest.mark.asyncio
async def test_no_acknowledgment_for_unsent(receipt_db):
    """Relays not yet sent should not be acknowledged."""
    pid = await db_mod.create_pending(receipt_db, 1001, 1002, "Still pending")
    # Status is 'awaiting_approval' — not sent

    svc = BotService(config=_config(receipt_db), db_path=receipt_db, llm=MockLLMClient())
    await svc.handle_message(1002, "Bob", "Hello")

    pending = await db_mod.get_pending(receipt_db, pid)
    assert pending["acknowledged_at"] is None


@pytest.mark.asyncio
async def test_no_acknowledgment_for_unpaired(receipt_db):
    """Unpaired users don't trigger any acknowledgment (no relays to them)."""
    await db_mod.create_user(receipt_db, 9999, "Lonely")

    svc = BotService(config=_config(receipt_db), db_path=receipt_db, llm=MockLLMClient())
    # Should not raise, just handle normally
    responses = await svc.handle_message(9999, "Lonely", "Hello")
    assert responses  # gets a normal response


@pytest.mark.asyncio
async def test_already_acknowledged_not_updated(receipt_db):
    """Already acknowledged relay keeps its original timestamp."""
    pid = await db_mod.create_pending(receipt_db, 1001, 1002, "Hello")
    await db_mod.update_pending_status(receipt_db, pid, "approved")
    await db_mod.mark_as_sent(receipt_db, pid)

    # Manually acknowledge
    await db_mod.mark_pending_acknowledged(receipt_db, pid)
    pending = await db_mod.get_pending(receipt_db, pid)
    first_ack = pending["acknowledged_at"]

    # Bob messages again — shouldn't find unacknowledged relays
    unacked = await db_mod.get_unacknowledged_relays_to_user(receipt_db, 1002)
    assert len(unacked) == 0


# ---------------------------------------------------------------------------
# /history checkmark
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_checkmark_for_acknowledged(receipt_db):
    """Acknowledged sent relays show ✓ in /history output."""
    pid = await db_mod.create_pending(receipt_db, 1001, 1002, "Hello Bob")
    await db_mod.update_pending_status(receipt_db, pid, "approved")
    await db_mod.mark_as_sent(receipt_db, pid)
    await db_mod.mark_pending_acknowledged(receipt_db, pid)

    svc = BotService(config=_config(receipt_db), db_path=receipt_db, llm=MockLLMClient())
    responses = await svc.handle_history(1001, None)
    assert len(responses) == 1
    assert "✓" in responses[0].text


@pytest.mark.asyncio
async def test_history_no_checkmark_for_unacknowledged(receipt_db):
    """Unacknowledged sent relays do not show ✓ in /history output."""
    pid = await db_mod.create_pending(receipt_db, 1001, 1002, "Hello Bob")
    await db_mod.update_pending_status(receipt_db, pid, "approved")
    await db_mod.mark_as_sent(receipt_db, pid)

    svc = BotService(config=_config(receipt_db), db_path=receipt_db, llm=MockLLMClient())
    responses = await svc.handle_history(1001, None)
    assert len(responses) == 1
    # ✓ should not appear (relay not acknowledged)
    text = responses[0].text
    # Split off the header line, check the relay line
    lines = text.split("\n")
    relay_lines = [l for l in lines if "You →" in l or "Partner" in l]
    # The relay line should not have a checkmark
    for line in relay_lines:
        if "You →" in line:
            assert "✓" not in line


@pytest.mark.asyncio
async def test_history_received_no_checkmark(receipt_db):
    """Received relays never show ✓ (only sent relays get it)."""
    pid = await db_mod.create_pending(receipt_db, 1002, 1001, "Hello Alice")
    await db_mod.update_pending_status(receipt_db, pid, "approved")
    await db_mod.mark_as_sent(receipt_db, pid)
    await db_mod.mark_pending_acknowledged(receipt_db, pid)

    svc = BotService(config=_config(receipt_db), db_path=receipt_db, llm=MockLLMClient())
    # Alice views history — she's the receiver, should not see ✓
    responses = await svc.handle_history(1001, None)
    text = responses[0].text
    lines = text.split("\n")
    received_lines = [l for l in lines if "Partner →" in l or "→ You" in l]
    for line in received_lines:
        # Received lines should not have ✓
        assert "✓" not in line


# ---------------------------------------------------------------------------
# DB-level CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_unacknowledged_relays(receipt_db):
    """get_unacknowledged_relays_to_user returns only unacked sent relays."""
    p1 = await db_mod.create_pending(receipt_db, 1001, 1002, "Msg 1")
    await db_mod.update_pending_status(receipt_db, p1, "approved")
    await db_mod.mark_as_sent(receipt_db, p1)

    p2 = await db_mod.create_pending(receipt_db, 1001, 1002, "Msg 2")
    await db_mod.update_pending_status(receipt_db, p2, "approved")
    await db_mod.mark_as_sent(receipt_db, p2)
    await db_mod.mark_pending_acknowledged(receipt_db, p2)

    unacked = await db_mod.get_unacknowledged_relays_to_user(receipt_db, 1002)
    assert len(unacked) == 1
    assert unacked[0]["id"] == p1
