"""Tests for A/B rewrite relay options (FEAT-003)."""

from __future__ import annotations

import pytest
import pytest_asyncio

from couples_bot import db as db_mod
from couples_bot.config import Config
from couples_bot.handlers import BotService
from couples_bot.llm import LLMResponse

from tests.helpers import MockLLMClient


@pytest_asyncio.fixture
async def ab_db(tmp_path) -> str:
    db_path = str(tmp_path / "ab.db")
    await db_mod.init_db(db_path)
    await db_mod.create_user(db_path, 1001, "Alice")
    await db_mod.create_user(db_path, 1002, "Bob")
    await db_mod.set_partner(db_path, 1001, 1002)
    # Set dampening base to 0.5 (mid-range for A/B trigger)
    await db_mod.ensure_user_profile(db_path, 1001)
    await db_mod.update_user_profile(db_path, 1001, dampening_base=0.5)
    return db_path


def _make_config(db_path: str, ab_enabled: bool = True, damp_range=None) -> Config:
    return Config(
        telegram_token="t",
        gemini_api_key="k",
        db_path=db_path,
        ab_rewrite_enabled=ab_enabled,
        ab_rewrite_dampening_range=damp_range or [0.3, 0.7],
        dampening_enabled=True,
        dampening_default_base=0.5,
        dampening_max_intensity=1.0,  # makes level ≈ base for test simplicity
    )


# ---------------------------------------------------------------------------
# A/B generation tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ab_rewrite_shows_two_options(ab_db):
    """When dampening is in range and A/B is enabled, show two options."""
    llm = MockLLMClient(responses=[
        {"reply": "Mock reply", "new_facts": [], "relay_draft": "Original draft"},
        {"reply": "Mock reply B", "new_facts": [], "relay_draft": "Lighter draft"},
    ])
    config = _make_config(ab_db)
    svc = BotService(config=config, db_path=ab_db, llm=llm)

    responses = await svc.handle_message(1001, "Alice", "#send Tell Bob I miss him")
    assert len(responses) >= 1
    main_resp = responses[0]
    # Should contain both option A and option B
    assert "Option A" in main_resp.text or "option a" in main_resp.text.lower()
    assert "Option B" in main_resp.text or "option b" in main_resp.text.lower()
    # Should have A/B buttons
    assert main_resp.buttons is not None
    cb_data = [b.callback_data for row in main_resp.buttons for b in row]
    assert any(d.startswith("ab_a:") for d in cb_data)
    assert any(d.startswith("ab_b:") for d in cb_data)


@pytest.mark.asyncio
async def test_ab_rewrite_two_llm_calls(ab_db):
    """A/B rewrite makes exactly two LLM calls — original + lighter variant."""
    llm = MockLLMClient(responses=[
        {"reply": "Reply 1", "new_facts": [], "relay_draft": "Draft 1"},
        {"reply": "Reply 2", "new_facts": [], "relay_draft": "Draft 2"},
    ])
    config = _make_config(ab_db)
    svc = BotService(config=config, db_path=ab_db, llm=llm)

    await svc.handle_message(1001, "Alice", "#send Tell Bob I miss him")
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_ab_disabled_single_llm_call(ab_db):
    """When A/B is disabled, only one LLM call is made (normal flow)."""
    llm = MockLLMClient(responses=[
        {"reply": "Reply", "new_facts": [], "relay_draft": "Draft"},
    ])
    config = _make_config(ab_db, ab_enabled=False)
    svc = BotService(config=config, db_path=ab_db, llm=llm)

    responses = await svc.handle_message(1001, "Alice", "#send Tell Bob I miss him")
    assert len(llm.calls) == 1
    # Should have normal approval buttons, not A/B
    main_resp = responses[0]
    if main_resp.buttons:
        cb_data = [b.callback_data for row in main_resp.buttons for b in row]
        assert not any(d.startswith("ab_a:") for d in cb_data)


@pytest.mark.asyncio
async def test_ab_outside_dampening_range(ab_db):
    """When dampening is outside range, normal single-draft flow."""
    # Set dampening base very high (outside 0.3-0.7 after formula)
    await db_mod.update_user_profile(ab_db, 1001, dampening_base=0.9)

    llm = MockLLMClient(responses=[
        {"reply": "Reply", "new_facts": [], "relay_draft": "Draft"},
    ])
    config = _make_config(ab_db)
    svc = BotService(config=config, db_path=ab_db, llm=llm)

    responses = await svc.handle_message(1001, "Alice", "#send Tell Bob I miss him")
    assert len(llm.calls) == 1
    if responses[0].buttons:
        cb_data = [b.callback_data for row in responses[0].buttons for b in row]
        assert not any(d.startswith("ab_a:") for d in cb_data)


@pytest.mark.asyncio
async def test_ab_below_dampening_range(ab_db):
    """Dampening below the A/B range also skips A/B."""
    await db_mod.update_user_profile(ab_db, 1001, dampening_base=0.1)

    llm = MockLLMClient(responses=[
        {"reply": "Reply", "new_facts": [], "relay_draft": "Draft"},
    ])
    config = _make_config(ab_db)
    svc = BotService(config=config, db_path=ab_db, llm=llm)

    responses = await svc.handle_message(1001, "Alice", "#send Tell Bob I miss him")
    assert len(llm.calls) == 1


# ---------------------------------------------------------------------------
# A/B option selection callbacks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ab_choose_a_creates_pending(ab_db):
    """Choosing option A creates a pending relay with draft_a content."""
    llm = MockLLMClient(responses=[
        {"reply": "Reply", "new_facts": [], "relay_draft": "Original (B)"},
        {"reply": "Reply", "new_facts": [], "relay_draft": "Lighter (A)"},
    ])
    config = _make_config(ab_db)
    svc = BotService(config=config, db_path=ab_db, llm=llm)

    responses = await svc.handle_message(1001, "Alice", "#send Tell Bob I miss him")
    # Extract ab_id from callback data
    cb_data = [
        b.callback_data for row in responses[0].buttons for b in row
        if b.callback_data.startswith("ab_a:")
    ]
    assert cb_data
    ab_id = cb_data[0].split(":")[1]

    # Choose A
    result = await svc.handle_callback(1001, f"ab_a:{ab_id}")
    assert len(result) >= 1
    # Should contain the lighter draft
    assert "Lighter (A)" in result[0].text


@pytest.mark.asyncio
async def test_ab_choose_b_creates_pending(ab_db):
    """Choosing option B creates a pending relay with draft_b content."""
    llm = MockLLMClient(responses=[
        {"reply": "Reply", "new_facts": [], "relay_draft": "Original (B)"},
        {"reply": "Reply", "new_facts": [], "relay_draft": "Lighter (A)"},
    ])
    config = _make_config(ab_db)
    svc = BotService(config=config, db_path=ab_db, llm=llm)

    responses = await svc.handle_message(1001, "Alice", "#send Tell Bob I miss him")
    cb_data = [
        b.callback_data for row in responses[0].buttons for b in row
        if b.callback_data.startswith("ab_b:")
    ]
    ab_id = cb_data[0].split(":")[1]

    result = await svc.handle_callback(1001, f"ab_b:{ab_id}")
    assert "Original (B)" in result[0].text


# ---------------------------------------------------------------------------
# Preference tracking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ab_preference_tracking(ab_db):
    """Choosing option A increments ab_preference_count_a in couple_config."""
    llm = MockLLMClient(responses=[
        {"reply": "R", "new_facts": [], "relay_draft": "B"},
        {"reply": "R", "new_facts": [], "relay_draft": "A"},
    ])
    config = _make_config(ab_db)
    svc = BotService(config=config, db_path=ab_db, llm=llm)

    responses = await svc.handle_message(1001, "Alice", "#send msg")
    cb_data = [
        b.callback_data for row in responses[0].buttons for b in row
        if b.callback_data.startswith("ab_a:")
    ]
    ab_id = cb_data[0].split(":")[1]
    await svc.handle_callback(1001, f"ab_a:{ab_id}")

    couple_id = db_mod.make_couple_id(1001, 1002)
    val = await db_mod.get_couple_config(ab_db, couple_id, "ab_preference_count_a")
    assert val == "1"


@pytest.mark.asyncio
async def test_ab_invalid_choice_id(ab_db):
    """Invalid ab_id returns error."""
    config = _make_config(ab_db)
    svc = BotService(config=config, db_path=ab_db, llm=MockLLMClient())

    result = await svc.handle_callback(1001, "ab_a:invalid_id")
    assert any("wrong" in r.text.lower() or "error" in r.text.lower() for r in result)
