"""CRITICAL privacy tests — every failure here is a data-leak bug."""

from __future__ import annotations

import pytest

from pairtner.db import (
    add_fact,
    create_pending,
    create_user,
    get_deliverable,
    get_facts,
    get_shared_facts_for_pair,
    get_user_context_data,
    init_db,
    log_message,
    set_partner,
)
from pairtner.core.prompts import (
    UserContext,
    build_chat_prompt,
    build_mediate_prompt,
    build_tell_prompt,
)


# ---------------------------------------------------------------------------
# Prompt-level privacy
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_prompt_excludes_partner_private_facts(test_db, paired_users):
    """User A's prompt must NEVER contain Bob's private facts."""
    a_id, b_id = paired_users

    data = await get_user_context_data(test_db, a_id)
    ctx = UserContext(**data)
    _sys, content = build_chat_prompt(ctx, "How are we doing?")

    # Alice's own stuff should be present
    assert "Alice feels stressed about work" in content
    assert "Alice likes morning walks" in content

    # Shared facts should be present
    assert "cook together on weekends" in content
    assert "date nights" in content

    # Bob's PRIVATE facts must NEVER appear
    assert "anxious about finances" not in content
    assert "quiet evenings" not in content


@pytest.mark.asyncio
async def test_prompt_excludes_partner_messages(test_db, paired_users):
    """User A's prompt must never contain Bob's chat messages."""
    a_id, b_id = paired_users

    data = await get_user_context_data(test_db, a_id)
    ctx = UserContext(**data)
    _sys, content = build_chat_prompt(ctx, "Hi")

    # Alice's own messages may appear
    assert "tough day" in content

    # Bob's messages must NEVER appear
    assert "Money is tight" not in content
    assert "Money is tight this month" not in content


@pytest.mark.asyncio
async def test_mediate_uses_only_shared_facts(test_db, paired_users):
    """Mediate prompt must contain ONLY shared facts — no private from either."""
    a_id, b_id = paired_users

    shared = await get_shared_facts_for_pair(test_db, a_id, b_id)
    shared_texts = [f["text"] for f in shared]
    _sys, content = build_mediate_prompt(shared_texts)

    # Shared facts present
    assert "cook together on weekends" in content
    assert "date nights" in content

    # No private facts from either partner
    assert "stressed about work" not in content
    assert "morning walks" not in content
    assert "anxious about finances" not in content
    assert "quiet evenings" not in content


@pytest.mark.asyncio
async def test_no_send_without_approval(test_db, paired_users):
    """Delivery function must only pick up rows with status='approved'
    and send_after in the past — never 'awaiting_approval'."""
    a_id, b_id = paired_users

    # Create pending with awaiting_approval (default)
    await create_pending(test_db, a_id, b_id, "Please hear me out")

    deliverable = await get_deliverable(test_db)
    assert len(deliverable) == 0, "Unapproved messages must not be delivered"


@pytest.mark.asyncio
async def test_whatdoyouknow_shows_only_own_facts(test_db, paired_users):
    """get_facts for user A must return only A's facts."""
    a_id, b_id = paired_users

    a_facts = await get_facts(test_db, a_id)
    a_texts = [f["text"] for f in a_facts]

    # Alice's facts
    assert "Alice feels stressed about work" in a_texts
    assert "Alice likes morning walks" in a_texts
    assert "We agreed to cook together on weekends" in a_texts

    # Bob's facts must NOT be here
    assert "Bob is anxious about finances" not in a_texts
    assert "Bob prefers quiet evenings" not in a_texts
    assert "We decided to try date nights" not in a_texts


@pytest.mark.asyncio
async def test_export_only_own_data(test_db, paired_users):
    """Export for user A must contain only A's facts."""
    a_id, b_id = paired_users

    a_facts = await get_facts(test_db, a_id)
    export_text = "\n".join(f["text"] for f in a_facts)

    assert "Alice feels stressed" in export_text
    assert "Bob is anxious" not in export_text


@pytest.mark.asyncio
async def test_partner_private_facts_never_leak_in_any_prompt_type(
    test_db, paired_users
):
    """Check all prompt types: chat, tell, mediate, deep (same as chat).
    Partner's private facts must NEVER appear."""
    a_id, b_id = paired_users

    data = await get_user_context_data(test_db, a_id)
    ctx = UserContext(**data)

    bob_private = ["anxious about finances", "quiet evenings"]

    # Chat prompt
    _, chat_content = build_chat_prompt(ctx, "thinking about us")
    for fact in bob_private:
        assert fact not in chat_content, f"Leaked in chat: {fact}"

    # Tell prompt
    _, tell_content = build_tell_prompt(ctx, "I need to tell you something")
    for fact in bob_private:
        assert fact not in tell_content, f"Leaked in tell: {fact}"

    # Mediate prompt (uses only shared facts)
    shared = await get_shared_facts_for_pair(test_db, a_id, b_id)
    _, mediate_content = build_mediate_prompt([f["text"] for f in shared])
    for fact in bob_private:
        assert fact not in mediate_content, f"Leaked in mediate: {fact}"

    # Deep uses the same prompt builder as chat
    _, deep_content = build_chat_prompt(ctx, "think deeply about us")
    for fact in bob_private:
        assert fact not in deep_content, f"Leaked in deep: {fact}"


@pytest.mark.asyncio
async def test_bob_prompt_excludes_alice_private(test_db, paired_users):
    """Mirror test: Bob's prompt must not contain Alice's private facts."""
    a_id, b_id = paired_users

    data = await get_user_context_data(test_db, b_id)
    ctx = UserContext(**data)
    _, content = build_chat_prompt(ctx, "How is everything?")

    # Bob's stuff
    assert "anxious about finances" in content
    assert "quiet evenings" in content

    # Shared
    assert "cook together" in content
    assert "date nights" in content

    # Alice's private must NOT appear
    assert "stressed about work" not in content
    assert "morning walks" not in content


@pytest.mark.asyncio
async def test_partner_perceptions_only_themes_in_prompt(test_db, paired_users):
    """Partner perception themes (not raw text) appear in prompt."""
    from pairtner.db import add_partner_perception
    from pairtner.core.prompts import build_partner_summary_block

    a_id, b_id = paired_users

    # Bob says something raw (stored in messages, NOT perceptions)
    await log_message(test_db, b_id, "user", "I am so frustrated with money")

    # A synthesized theme is stored as a perception
    await add_partner_perception(
        test_db, user_id=b_id, about_id=a_id,
        theme="concerned about financial stress",
        sentiment="frustrated",
    )

    # Build Alice's prompt with partner summary
    from pairtner.db import get_partner_perceptions as get_pp
    perceptions = await get_pp(test_db, a_id)
    summary = build_partner_summary_block(perceptions)

    data = await get_user_context_data(test_db, a_id)
    ctx = UserContext(**data)
    _, content = build_chat_prompt(ctx, "Hi", partner_summary=summary)

    # Synthesized theme appears
    assert "concerned about financial stress" in content

    # Raw partner messages must never appear
    assert "I am so frustrated with money" not in content


# ---------------------------------------------------------------------------
# /mediate redesign: new prompt inputs (perceptions, window)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mediate_prompt_with_perceptions_has_no_private_or_raw(
    test_db, paired_users
):
    """New mediate inputs carry only shared facts and filtered themes."""
    a_id, b_id = paired_users
    await log_message(test_db, b_id, "user", "RAW-PRIVACY-SENTINEL-8841")
    shared = [f["text"] for f in await get_shared_facts_for_pair(test_db, a_id, b_id)]
    _sys, content = build_mediate_prompt(
        shared,
        perceptions_a=["seems to feel unheard"],
        perceptions_b=["wants more date nights"],
        name_a="Alice", name_b="Bob", window_label="the last 7 days",
    )
    assert "seems to feel unheard" in content
    assert "Impressions Alice has noted about Bob" in content
    assert "Impressions Bob has noted about Alice" in content
    assert "RAW-PRIVACY-SENTINEL-8841" not in content
    for private in ("stressed about work", "morning walks",
                    "anxious about finances", "quiet evenings"):
        assert private not in content


def test_mediate_prompt_positional_call_still_valid():
    _sys, content = build_mediate_prompt(["we cook on weekends"])
    assert "we cook on weekends" in content
    assert "Impressions" not in content
