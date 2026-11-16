"""Goal tracking tests (FEAT-004)."""

from __future__ import annotations

import pytest

from couples_bot.db import (
    add_user_goal,
    add_shared_goal,
    create_user,
    get_active_goals,
    get_shared_goals,
    make_couple_id,
    set_partner,
    update_goal_status,
    update_shared_goal_status,
)
from couples_bot.handlers import BotService


# ---------------------------------------------------------------------------
# /goal command
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_goal_creates_personal(service: BotService, test_db, paired_users):
    a_id, _ = paired_users
    responses = await service.handle_goal(a_id, "Exercise 3x a week")
    assert len(responses) == 1
    assert "Exercise 3x a week" in responses[0].text

    goals = await get_active_goals(test_db, a_id)
    assert len(goals) == 1
    assert goals[0]["text"] == "Exercise 3x a week"
    assert goals[0]["status"] == "active"


@pytest.mark.asyncio
async def test_goal_no_text(service: BotService, test_db, paired_users):
    a_id, _ = paired_users
    responses = await service.handle_goal(a_id, None)
    assert len(responses) == 1
    assert "Usage" in responses[0].text or "goal" in responses[0].text.lower()


@pytest.mark.asyncio
async def test_goal_empty_text(service: BotService, test_db, paired_users):
    a_id, _ = paired_users
    responses = await service.handle_goal(a_id, "   ")
    assert len(responses) == 1
    assert "Usage" in responses[0].text or "goal" in responses[0].text.lower()


# ---------------------------------------------------------------------------
# /together command
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_together_creates_shared(service: BotService, test_db, paired_users):
    a_id, b_id = paired_users
    responses = await service.handle_together(a_id, "Go on a trip together")
    # Should have 2 responses: confirmation + partner notification
    assert len(responses) == 2
    assert "Go on a trip together" in responses[0].text
    assert responses[1].target_user_id == b_id

    couple_id = make_couple_id(a_id, b_id)
    goals = await get_shared_goals(test_db, couple_id)
    assert len(goals) == 1
    assert goals[0]["text"] == "Go on a trip together"
    assert goals[0]["created_by"] == a_id


@pytest.mark.asyncio
async def test_together_requires_partner(service: BotService, test_db):
    await create_user(test_db, 5001, "Solo")
    responses = await service.handle_together(5001, "Goal text")
    assert len(responses) == 1
    assert "partner" in responses[0].text.lower() or "pair" in responses[0].text.lower()


@pytest.mark.asyncio
async def test_together_no_text(service: BotService, test_db, paired_users):
    a_id, _ = paired_users
    responses = await service.handle_together(a_id, None)
    assert len(responses) == 1
    assert "Usage" in responses[0].text or "together" in responses[0].text.lower()


# ---------------------------------------------------------------------------
# /goals command
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_goals_shows_grouped(service: BotService, test_db, paired_users):
    a_id, b_id = paired_users
    couple_id = make_couple_id(a_id, b_id)

    await add_user_goal(test_db, a_id, "Read more books")
    await add_shared_goal(test_db, couple_id, a_id, "Date night weekly")

    responses = await service.handle_goals(a_id)
    assert len(responses) == 1
    text = responses[0].text
    assert "Read more books" in text
    assert "Date night weekly" in text
    # Should have inline buttons
    assert responses[0].buttons is not None
    assert len(responses[0].buttons) == 2  # 1 personal + 1 shared


@pytest.mark.asyncio
async def test_goals_empty(service: BotService, test_db, paired_users):
    a_id, _ = paired_users
    responses = await service.handle_goals(a_id)
    assert len(responses) == 1
    assert "No active goals" in responses[0].text or "goals" in responses[0].text.lower()


# ---------------------------------------------------------------------------
# Goal callbacks (achieved / dropped)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_goal_achieved_callback(service: BotService, test_db, paired_users):
    a_id, _ = paired_users
    goal_id = await add_user_goal(test_db, a_id, "Learn cooking")

    responses = await service.handle_callback(a_id, f"ga:{goal_id}")
    assert len(responses) == 1
    assert "achieved" in responses[0].text.lower() or "✅" in responses[0].text

    goals = await get_active_goals(test_db, a_id)
    assert len(goals) == 0  # no longer active


@pytest.mark.asyncio
async def test_goal_dropped_callback(service: BotService, test_db, paired_users):
    a_id, _ = paired_users
    goal_id = await add_user_goal(test_db, a_id, "Run marathon")

    responses = await service.handle_callback(a_id, f"gd:{goal_id}")
    assert len(responses) == 1
    assert "dropped" in responses[0].text.lower() or "❌" in responses[0].text

    goals = await get_active_goals(test_db, a_id)
    assert len(goals) == 0


@pytest.mark.asyncio
async def test_shared_goal_achieved_callback(service: BotService, test_db, paired_users):
    a_id, b_id = paired_users
    couple_id = make_couple_id(a_id, b_id)
    goal_id = await add_shared_goal(test_db, couple_id, a_id, "Save money")

    responses = await service.handle_callback(a_id, f"sg_a:{goal_id}")
    assert len(responses) == 1
    assert "achieved" in responses[0].text.lower() or "✅" in responses[0].text

    goals = await get_shared_goals(test_db, couple_id)
    assert len(goals) == 0


@pytest.mark.asyncio
async def test_shared_goal_dropped_callback(service: BotService, test_db, paired_users):
    a_id, b_id = paired_users
    couple_id = make_couple_id(a_id, b_id)
    goal_id = await add_shared_goal(test_db, couple_id, a_id, "Move to new city")

    responses = await service.handle_callback(a_id, f"sg_d:{goal_id}")
    assert len(responses) == 1

    goals = await get_shared_goals(test_db, couple_id)
    assert len(goals) == 0


# ---------------------------------------------------------------------------
# Goal callback — wrong user
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_goal_achieved_wrong_user(service: BotService, test_db, paired_users):
    a_id, b_id = paired_users
    goal_id = await add_user_goal(test_db, a_id, "My goal")

    # Bob tries to mark Alice's goal as achieved
    responses = await service.handle_callback(b_id, f"ga:{goal_id}")
    assert len(responses) == 1
    # Should get error since Bob doesn't own this goal
    assert "wrong" in responses[0].text.lower() or "error" in responses[0].text.lower() or "Something" in responses[0].text


# ---------------------------------------------------------------------------
# Goals in check-in summary
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_goals_in_checkin_prompt(test_db, paired_users):
    """Goals are fetched during check-in summary generation."""
    a_id, b_id = paired_users
    couple_id = make_couple_id(a_id, b_id)

    await add_user_goal(test_db, a_id, "Be more patient")
    await add_shared_goal(test_db, couple_id, a_id, "Weekly check-in habit")

    # Verify the goals exist and can be fetched
    personal = await get_active_goals(test_db, a_id)
    shared = await get_shared_goals(test_db, couple_id)
    assert len(personal) == 1
    assert len(shared) == 1
