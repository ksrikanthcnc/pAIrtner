"""Daily digest tests."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio

from pairtner.db import (
    add_fact,
    create_pending,
    create_user,
    get_couple_config,
    init_db,
    make_couple_id,
    mark_as_sent,
    set_couple_config,
    set_partner,
    update_pending_status,
)
from pairtner.digest import _check_and_send

from tests.helpers import MockLLMClient


@pytest.fixture
def digest_llm():
    return MockLLMClient([
        {"reply": "Today was a warm day of connection.", "new_facts": [], "relay_draft": None},
    ])


@pytest_asyncio.fixture
async def digest_db(tmp_path):
    db_path = str(tmp_path / "digest.db")
    await init_db(db_path)
    return db_path


@pytest_asyncio.fixture
async def digest_couple(digest_db):
    a_id, b_id = 1001, 1002
    await create_user(digest_db, a_id, "Alice")
    await create_user(digest_db, b_id, "Bob")
    await set_partner(digest_db, a_id, b_id)
    return a_id, b_id


@pytest.mark.asyncio
async def test_digest_sends_to_both(digest_db, digest_couple, digest_llm):
    """Digest sends a summary to both partners when there's activity."""
    a_id, b_id = digest_couple

    # Add a shared fact
    await add_fact(digest_db, a_id, "We had a good talk", "shared")

    sent: list[tuple[int, str]] = []

    async def mock_send(uid: int, text: str) -> None:
        sent.append((uid, text))

    # Patch datetime to match the digest time
    fake_now = datetime(2025, 7, 15, 20, 0, tzinfo=timezone.utc)
    with patch("pairtner.digest.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        await _check_and_send(
            digest_db, digest_llm, mock_send,
            digest_time="20:00", digest_tz="UTC",
        )

    # Both partners should receive the digest
    recipients = {uid for uid, _ in sent}
    assert a_id in recipients
    assert b_id in recipients
    assert any("warm day" in text for _, text in sent)

    # last_digest should be recorded
    couple_id = make_couple_id(a_id, b_id)
    last = await get_couple_config(digest_db, couple_id, "last_digest")
    assert last is not None


@pytest.mark.asyncio
async def test_digest_skips_no_activity(digest_db, digest_couple):
    """Digest skips when there's no new activity since last digest."""
    a_id, b_id = digest_couple

    # Set last_digest to today — no new facts or relays
    couple_id = make_couple_id(a_id, b_id)
    await set_couple_config(digest_db, couple_id, "last_digest", "2025-07-15T19:00:00")

    llm = MockLLMClient()
    sent: list[tuple[int, str]] = []

    async def mock_send(uid: int, text: str) -> None:
        sent.append((uid, text))

    fake_now = datetime(2025, 7, 15, 20, 0, tzinfo=timezone.utc)
    with patch("pairtner.digest.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        await _check_and_send(
            digest_db, llm, mock_send,
            digest_time="20:00", digest_tz="UTC",
        )

    # Nothing should be sent — no new activity
    assert len(sent) == 0
    assert len(llm.calls) == 0


@pytest.mark.asyncio
async def test_digest_skips_wrong_time(digest_db, digest_couple):
    """Digest does not fire when the time doesn't match."""
    a_id, b_id = digest_couple
    await add_fact(digest_db, a_id, "New fact", "shared")

    llm = MockLLMClient()
    sent: list[tuple[int, str]] = []

    async def mock_send(uid: int, text: str) -> None:
        sent.append((uid, text))

    # Set time to 15:00, but digest time is 20:00
    fake_now = datetime(2025, 7, 15, 15, 0, tzinfo=timezone.utc)
    with patch("pairtner.digest.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        await _check_and_send(
            digest_db, llm, mock_send,
            digest_time="20:00", digest_tz="UTC",
        )

    assert len(sent) == 0


@pytest.mark.asyncio
async def test_digest_skips_already_sent_today(digest_db, digest_couple, digest_llm):
    """Digest skips if already sent today (based on last_digest date)."""
    a_id, b_id = digest_couple
    await add_fact(digest_db, a_id, "New fact", "shared")

    couple_id = make_couple_id(a_id, b_id)
    # Mark as already sent today
    await set_couple_config(digest_db, couple_id, "last_digest", "2025-07-15T18:00:00+00:00")

    sent: list[tuple[int, str]] = []

    async def mock_send(uid: int, text: str) -> None:
        sent.append((uid, text))

    fake_now = datetime(2025, 7, 15, 20, 0, tzinfo=timezone.utc)
    with patch("pairtner.digest.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        await _check_and_send(
            digest_db, digest_llm, mock_send,
            digest_time="20:00", digest_tz="UTC",
        )

    assert len(sent) == 0


@pytest.mark.asyncio
async def test_digest_includes_relays(digest_db, digest_couple):
    """Digest picks up sent relays since last digest."""
    a_id, b_id = digest_couple

    # Create and send a relay
    pid = await create_pending(digest_db, a_id, b_id, "I love you")
    await update_pending_status(digest_db, pid, "approved", "2020-01-01T00:00:00")
    await mark_as_sent(digest_db, pid)

    llm = MockLLMClient([
        {"reply": "Messages of love were shared today.", "new_facts": [], "relay_draft": None},
    ])
    sent: list[tuple[int, str]] = []

    async def mock_send(uid: int, text: str) -> None:
        sent.append((uid, text))

    fake_now = datetime(2025, 7, 15, 20, 0, tzinfo=timezone.utc)
    with patch("pairtner.digest.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        await _check_and_send(
            digest_db, llm, mock_send,
            digest_time="20:00", digest_tz="UTC",
        )

    assert len(sent) == 2  # Both partners
    assert any("love" in text.lower() for _, text in sent)
