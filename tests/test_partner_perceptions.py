"""Tests for partner perceptions: CRUD and privacy in prompts."""

from __future__ import annotations

import pytest

from pairtner.db import (
    add_partner_perception,
    get_partner_perceptions,
    get_user_context_data,
    log_message,
    update_perception_confidence,
)
from pairtner.core.prompts import (
    UserContext,
    build_chat_prompt,
    build_partner_summary_block,
    build_tell_prompt,
)


@pytest.mark.asyncio
async def test_add_and_get_perceptions(test_db, paired_users):
    """Perceptions can be added and retrieved by about_id."""
    a_id, b_id = paired_users

    # Alice talks about Bob → perception stored about Bob
    pid = await add_partner_perception(
        test_db, user_id=a_id, about_id=b_id,
        theme="feels unheard about household decisions",
        sentiment="frustrated",
    )
    assert pid > 0

    # Get perceptions ABOUT Bob (these would go into Alice's prompt)
    perceptions = await get_partner_perceptions(test_db, b_id)
    assert len(perceptions) >= 1
    assert any(p["theme"] == "feels unheard about household decisions" for p in perceptions)


@pytest.mark.asyncio
async def test_update_perception_confidence(test_db, paired_users):
    """Confidence score can be updated."""
    a_id, b_id = paired_users

    pid = await add_partner_perception(
        test_db, user_id=a_id, about_id=b_id,
        theme="appreciates quality time",
    )
    await update_perception_confidence(test_db, pid, 0.9)

    perceptions = await get_partner_perceptions(test_db, b_id)
    updated = [p for p in perceptions if p["id"] == pid][0]
    assert updated["confidence"] == 0.9


@pytest.mark.asyncio
async def test_build_partner_summary_block_with_perceptions():
    """Summary block formats perceptions as theme list."""
    perceptions = [
        {"theme": "feels unheard about finances", "sentiment": "frustrated"},
        {"theme": "appreciates quality time", "sentiment": "positive"},
    ]
    block = build_partner_summary_block(perceptions)
    assert "What your partner seems to feel" in block
    assert "feels unheard about finances" in block
    assert "frustrated" in block
    assert "appreciates quality time" in block


@pytest.mark.asyncio
async def test_build_partner_summary_block_empty():
    """Empty perceptions return empty string."""
    assert build_partner_summary_block([]) == ""


@pytest.mark.asyncio
async def test_partner_summary_in_chat_prompt():
    """Partner summary block is included in chat prompt when provided."""
    ctx = UserContext(
        user_name="Alice",
        partner_name="Bob",
        private_facts=["likes walks"],
        shared_facts=["agreed to cook"],
    )
    summary = build_partner_summary_block([
        {"theme": "feels stressed about work", "sentiment": "concerned"},
    ])
    _, content = build_chat_prompt(
        ctx, "How are we doing?", partner_summary=summary,
    )
    assert "What your partner seems to feel" in content
    assert "feels stressed about work" in content


@pytest.mark.asyncio
async def test_partner_summary_in_tell_prompt():
    """Partner summary block is included in tell prompt when provided."""
    ctx = UserContext(
        user_name="Alice",
        partner_name="Bob",
    )
    summary = build_partner_summary_block([
        {"theme": "wants more date nights", "sentiment": "hopeful"},
    ])
    _, content = build_tell_prompt(
        ctx, "I want to tell you something", partner_summary=summary,
    )
    assert "wants more date nights" in content


@pytest.mark.asyncio
async def test_privacy_raw_text_never_in_prompt(test_db, paired_users):
    """PRIVACY: raw partner message text must never appear in prompt.

    Only synthesized themes (from partner_perceptions) may appear.
    """
    a_id, b_id = paired_users

    # Bob sends a raw message (stored in messages)
    await log_message(test_db, b_id, "user", "Money is tight this month and I'm scared")

    # A perception theme is stored (synthesized, not raw)
    await add_partner_perception(
        test_db, user_id=b_id, about_id=a_id,
        theme="worried about financial stability",
        sentiment="anxious",
    )

    # Build Alice's prompt
    data = await get_user_context_data(test_db, a_id)
    ctx = UserContext(**data)
    perceptions = await get_partner_perceptions(test_db, a_id)
    summary = build_partner_summary_block(perceptions)
    _, content = build_chat_prompt(ctx, "How are things?", partner_summary=summary)

    # Theme appears (good)
    assert "worried about financial stability" in content

    # Raw partner text must NEVER appear
    assert "Money is tight this month" not in content
    assert "I'm scared" not in content
