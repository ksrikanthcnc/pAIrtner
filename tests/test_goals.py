"""Goal tracking tests — unified /goal command."""

from __future__ import annotations

import pytest

from pairtner.db import (
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
from pairtner.handlers import BotService


# ---------------------------------------------------------------------------
# /goal <text> — create personal goal
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


@pytest.mark.asyncio
async def test_goal_add_prefix(service: BotService, test_db, paired_users):
    """'/goal add <text>' also creates a personal goal."""
    a_id, _ = paired_users
    responses = await service.handle_goal(a_id, "add Read more books")
    assert len(responses) == 1
    assert "Read more books" in responses[0].text


@pytest.mark.asyncio
async def test_goal_no_text_shows_list(service: BotService, test_db, paired_users):
    """'/goal' with no text shows the goal list."""
    a_id, _ = paired_users
    responses = await service.handle_goal(a_id, None)
    assert len(responses) == 1
    assert "No active goals" in responses[0].text or "goal" in responses[0].text.lower()


@pytest.mark.asyncio
async def test_goal_list_subcommand(service: BotService, test_db, paired_users):
    """'/goal list' shows goals."""
    a_id, _ = paired_users
    await add_user_goal(test_db, a_id, "Test goal")
    responses = await service.handle_goal(a_id, "list")
    assert len(responses) == 1
    assert "Test goal" in responses[0].text


# ---------------------------------------------------------------------------
# /goal shared <text> — create shared goal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_goal_shared_creates(service: BotService, test_db, paired_users):
    a_id, b_id = paired_users
    responses = await service.handle_goal(a_id, "shared Go on a trip together")
    assert len(responses) == 2
    assert "Go on a trip together" in responses[0].text
    assert responses[1].target_user_id == b_id

    couple_id = make_couple_id(a_id, b_id)
    goals = await get_shared_goals(test_db, couple_id)
    assert len(goals) == 1
    assert goals[0]["text"] == "Go on a trip together"
    assert goals[0]["created_by"] == a_id


@pytest.mark.asyncio
async def test_goal_shared_requires_partner(service: BotService, test_db):
    await create_user(test_db, 5001, "Solo")
    responses = await service.handle_goal(5001, "shared Goal text")
    assert len(responses) == 1
    assert "partner" in responses[0].text.lower() or "pair" in responses[0].text.lower()


@pytest.mark.asyncio
async def test_goal_shared_no_text(service: BotService, test_db, paired_users):
    a_id, _ = paired_users
    responses = await service.handle_goal(a_id, "shared")
    assert len(responses) == 1
    # Should show usage or error
    assert "Usage" in responses[0].text or "together" in responses[0].text.lower() or "goal" in responses[0].text.lower()


# ---------------------------------------------------------------------------
# /goal (list) — shows grouped personal + shared
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_goal_shows_grouped(service: BotService, test_db, paired_users):
    a_id, b_id = paired_users
    couple_id = make_couple_id(a_id, b_id)

    await add_user_goal(test_db, a_id, "Read more books")
    await add_shared_goal(test_db, couple_id, a_id, "Date night weekly")

    responses = await service.handle_goal(a_id, None)
    assert len(responses) == 1
    text = responses[0].text
    assert "Read more books" in text
    assert "Date night weekly" in text
    assert responses[0].buttons is not None
    assert len(responses[0].buttons) == 2


@pytest.mark.asyncio
async def test_goal_empty(service: BotService, test_db, paired_users):
    a_id, _ = paired_users
    responses = await service.handle_goal(a_id, None)
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
    assert len(await get_active_goals(test_db, a_id)) == 0


@pytest.mark.asyncio
async def test_goal_dropped_callback(service: BotService, test_db, paired_users):
    a_id, _ = paired_users
    goal_id = await add_user_goal(test_db, a_id, "Run marathon")
    responses = await service.handle_callback(a_id, f"gd:{goal_id}")
    assert len(responses) == 1
    assert "dropped" in responses[0].text.lower() or "❌" in responses[0].text
    assert len(await get_active_goals(test_db, a_id)) == 0


@pytest.mark.asyncio
async def test_shared_goal_achieved_callback(service: BotService, test_db, paired_users):
    a_id, b_id = paired_users
    couple_id = make_couple_id(a_id, b_id)
    goal_id = await add_shared_goal(test_db, couple_id, a_id, "Save money")
    responses = await service.handle_callback(a_id, f"sg_a:{goal_id}")
    assert len(responses) == 1
    assert "achieved" in responses[0].text.lower() or "✅" in responses[0].text
    assert len(await get_shared_goals(test_db, couple_id)) == 0


@pytest.mark.asyncio
async def test_shared_goal_dropped_callback(service: BotService, test_db, paired_users):
    a_id, b_id = paired_users
    couple_id = make_couple_id(a_id, b_id)
    goal_id = await add_shared_goal(test_db, couple_id, a_id, "Move to new city")
    responses = await service.handle_callback(a_id, f"sg_d:{goal_id}")
    assert len(responses) == 1
    assert len(await get_shared_goals(test_db, couple_id)) == 0


@pytest.mark.asyncio
async def test_goal_achieved_wrong_user(service: BotService, test_db, paired_users):
    a_id, b_id = paired_users
    goal_id = await add_user_goal(test_db, a_id, "My goal")
    responses = await service.handle_callback(b_id, f"ga:{goal_id}")
    assert len(responses) == 1
    assert "wrong" in responses[0].text.lower() or "error" in responses[0].text.lower() or "Something" in responses[0].text


@pytest.mark.asyncio
async def test_goals_in_checkin_prompt(test_db, paired_users):
    """Goals data layer works for check-in integration."""
    a_id, b_id = paired_users
    couple_id = make_couple_id(a_id, b_id)
    await add_user_goal(test_db, a_id, "Be more patient")
    await add_shared_goal(test_db, couple_id, a_id, "Weekly check-in habit")
    assert len(await get_active_goals(test_db, a_id)) == 1
    assert len(await get_shared_goals(test_db, couple_id)) == 1
