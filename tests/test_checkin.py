"""Weekly check-in tests."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio

from pairtner.features.checkin import _check_and_send, process_checkin_answer
from pairtner.db import (
    create_checkin,
    create_user,
    get_active_checkin,
    init_db,
    make_couple_id,
    mark_checkin_complete,
    set_partner,
    update_checkin_answers,
)

from tests.helpers import MockLLMClient


@pytest_asyncio.fixture
async def checkin_db(tmp_path):
    db_path = str(tmp_path / "checkin.db")
    await init_db(db_path)
    return db_path


@pytest_asyncio.fixture
async def checkin_couple(checkin_db):
    a_id, b_id = 2001, 2002
    await create_user(checkin_db, a_id, "Alice")
    await create_user(checkin_db, b_id, "Bob")
    await set_partner(checkin_db, a_id, b_id)
    return a_id, b_id


# ---------------------------------------------------------------------------
# DB CRUD for checkins
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkin_table_crud(checkin_db, checkin_couple):
    """Create, read, update, and complete a check-in record."""
    a_id, b_id = checkin_couple
    couple_id = make_couple_id(a_id, b_id)

    # Create
    cid = await create_checkin(checkin_db, couple_id, "2025-07-13", a_id, b_id)
    assert cid is not None

    # Read active
    active = await get_active_checkin(checkin_db, couple_id)
    assert active is not None
    assert active["couple_id"] == couple_id
    assert active["status"] == "pending"
    assert active["partner_a_id"] == a_id
    assert active["partner_b_id"] == b_id
    assert active["partner_a_answers"] is None
    assert active["partner_b_answers"] is None

    # Update partner A answers
    await update_checkin_answers(checkin_db, cid, "a", "Things went well")
    active = await get_active_checkin(checkin_db, couple_id)
    assert active["partner_a_answers"] == "Things went well"
    assert active["status"] == "partial"

    # Update partner B answers
    await update_checkin_answers(checkin_db, cid, "b", "Could improve communication")
    active = await get_active_checkin(checkin_db, couple_id)
    assert active["partner_b_answers"] == "Could improve communication"

    # Mark complete
    await mark_checkin_complete(checkin_db, cid, "Both partners reflect positively")
    active = await get_active_checkin(checkin_db, couple_id)
    assert active is None  # No longer active (status=complete)


# ---------------------------------------------------------------------------
# Check-in loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkin_sends_questions(checkin_db, checkin_couple):
    """Check-in sends questions to both partners at the right time."""
    a_id, b_id = checkin_couple
    llm = MockLLMClient()
    sent: list[tuple[int, str]] = []

    async def mock_send(uid: int, text: str) -> None:
        sent.append((uid, text))

    questions = ["What went well?", "What to improve?", "What are you grateful for?"]

    # 2025-07-13 is a Sunday
    fake_now = datetime(2025, 7, 13, 18, 0, tzinfo=timezone.utc)
    with patch("pairtner.features.checkin.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        await _check_and_send(
            checkin_db, llm, mock_send,
            checkin_day="sunday", checkin_time="18:00",
            checkin_tz="UTC", questions=questions,
        )

    # Both partners should receive questions
    recipients = {uid for uid, _ in sent}
    assert a_id in recipients
    assert b_id in recipients
    assert len(sent) == 2

    # Check that questions are in the messages
    for _, text in sent:
        assert "What went well?" in text
        assert "What to improve?" in text

    # Active check-in should exist
    couple_id = make_couple_id(a_id, b_id)
    active = await get_active_checkin(checkin_db, couple_id)
    assert active is not None
    assert active["status"] == "pending"


@pytest.mark.asyncio
async def test_checkin_skips_wrong_day(checkin_db, checkin_couple):
    """Check-in does not fire on the wrong day."""
    llm = MockLLMClient()
    sent: list[tuple[int, str]] = []

    async def mock_send(uid: int, text: str) -> None:
        sent.append((uid, text))

    # 2025-07-14 is Monday, not Sunday
    fake_now = datetime(2025, 7, 14, 18, 0, tzinfo=timezone.utc)
    with patch("pairtner.features.checkin.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        await _check_and_send(
            checkin_db, llm, mock_send,
            checkin_day="sunday", checkin_time="18:00",
            checkin_tz="UTC", questions=["Q1?"],
        )

    assert len(sent) == 0


@pytest.mark.asyncio
async def test_checkin_skips_active_existing(checkin_db, checkin_couple):
    """Check-in does not re-send if there's an active check-in this week."""
    a_id, b_id = checkin_couple
    couple_id = make_couple_id(a_id, b_id)

    # Create an existing active check-in
    await create_checkin(checkin_db, couple_id, "2025-07-13", a_id, b_id)

    llm = MockLLMClient()
    sent: list[tuple[int, str]] = []

    async def mock_send(uid: int, text: str) -> None:
        sent.append((uid, text))

    fake_now = datetime(2025, 7, 13, 18, 0, tzinfo=timezone.utc)
    with patch("pairtner.features.checkin.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        await _check_and_send(
            checkin_db, llm, mock_send,
            checkin_day="sunday", checkin_time="18:00",
            checkin_tz="UTC", questions=["Q1?"],
        )

    assert len(sent) == 0


# ---------------------------------------------------------------------------
# Check-in answer processing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkin_stores_answers(checkin_db, checkin_couple):
    """process_checkin_answer stores the answer for the correct partner."""
    a_id, b_id = checkin_couple
    couple_id = make_couple_id(a_id, b_id)
    await create_checkin(checkin_db, couple_id, "2025-07-13", a_id, b_id)

    llm = MockLLMClient()
    sent: list[tuple[int, str]] = []

    async def mock_send(uid: int, text: str) -> None:
        sent.append((uid, text))

    # Partner A answers
    consumed = await process_checkin_answer(
        checkin_db, llm, mock_send, a_id, "Everything went great!",
    )
    assert consumed is True

    active = await get_active_checkin(checkin_db, couple_id)
    assert active["partner_a_answers"] == "Everything went great!"
    assert active["partner_b_answers"] is None
    assert active["status"] == "partial"

    # Should notify waiting for partner
    assert any("waiting" in text.lower() or "partner" in text.lower()
               for _, text in sent)


@pytest.mark.asyncio
async def test_checkin_generates_summary(checkin_db, checkin_couple):
    """Summary is generated when both partners answer."""
    a_id, b_id = checkin_couple
    couple_id = make_couple_id(a_id, b_id)
    await create_checkin(checkin_db, couple_id, "2025-07-13", a_id, b_id)

    llm = MockLLMClient([
        # First call (after partner B answers) — summary generation
        {"reply": "Both partners showed growth and gratitude.", "new_facts": [], "relay_draft": None},
    ])
    sent: list[tuple[int, str]] = []

    async def mock_send(uid: int, text: str) -> None:
        sent.append((uid, text))

    # Partner A answers
    await process_checkin_answer(
        checkin_db, llm, mock_send, a_id, "It was a good week",
    )

    # Partner B answers — triggers summary
    await process_checkin_answer(
        checkin_db, llm, mock_send, b_id, "I appreciated the effort",
    )

    # Summary should be sent to both partners
    summary_msgs = [text for _, text in sent if "growth" in text.lower() or "Summary" in text]
    assert len(summary_msgs) >= 2  # Both partners

    # Check-in should be complete
    active = await get_active_checkin(checkin_db, couple_id)
    assert active is None  # Completed = no longer active


@pytest.mark.asyncio
async def test_checkin_no_active_returns_false(checkin_db, checkin_couple):
    """process_checkin_answer returns False when no active check-in."""
    a_id, _ = checkin_couple
    llm = MockLLMClient()

    async def mock_send(uid: int, text: str) -> None:
        pass

    consumed = await process_checkin_answer(
        checkin_db, llm, mock_send, a_id, "Some text",
    )
    assert consumed is False


@pytest.mark.asyncio
async def test_checkin_unpaired_user_returns_false(checkin_db):
    """process_checkin_answer returns False for an unpaired user."""
    await create_user(checkin_db, 9999, "Solo")
    llm = MockLLMClient()

    async def mock_send(uid: int, text: str) -> None:
        pass

    consumed = await process_checkin_answer(
        checkin_db, llm, mock_send, 9999, "Some text",
    )
    assert consumed is False


@pytest.mark.asyncio
async def test_checkin_ignores_duplicate_answer(checkin_db, checkin_couple):
    """A second answer from the same partner is ignored."""
    a_id, b_id = checkin_couple
    couple_id = make_couple_id(a_id, b_id)
    await create_checkin(checkin_db, couple_id, "2025-07-13", a_id, b_id)

    llm = MockLLMClient()

    async def mock_send(uid: int, text: str) -> None:
        pass

    # First answer
    consumed1 = await process_checkin_answer(
        checkin_db, llm, mock_send, a_id, "First answer",
    )
    assert consumed1 is True

    # Duplicate answer — should not consume
    consumed2 = await process_checkin_answer(
        checkin_db, llm, mock_send, a_id, "Second answer",
    )
    assert consumed2 is False

    # Original answer should be preserved
    active = await get_active_checkin(checkin_db, couple_id)
    assert active["partner_a_answers"] == "First answer"
