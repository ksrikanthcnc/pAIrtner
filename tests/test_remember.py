"""Tests for the /remember command."""

from __future__ import annotations

import pytest

from couples_bot import db as db_mod
from tests.helpers import MockLLMClient


# ---------------------------------------------------------------------------
# /remember with positive shared facts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_remember_with_positive_facts(service, test_db, paired_users):
    a_id, b_id = paired_users

    # Add positive shared facts
    await db_mod.add_fact(
        test_db, a_id, "We had an amazing trip to Goa", "shared",
        category="relationship",
    )
    await db_mod.add_fact(
        test_db, a_id, "We love cooking biryani together", "shared",
        category="emotional",
    )

    responses = await service.handle_remember(a_id)
    assert len(responses) == 1
    text = responses[0].text
    assert "💭" in text  # header emoji
    assert "✨" in text  # item emoji


@pytest.mark.asyncio
async def test_remember_picks_random_fact(service, test_db, paired_users):
    a_id, b_id = paired_users

    # Add several positive shared facts
    facts = [
        "We love hiking",
        "We enjoy movies",
        "We cook together",
    ]
    for f in facts:
        await db_mod.add_fact(
            test_db, a_id, f, "shared", category="relationship",
        )

    # Call multiple times — should get valid responses each time
    seen_texts = set()
    for _ in range(10):
        responses = await service.handle_remember(a_id)
        assert len(responses) == 1
        seen_texts.add(responses[0].text)

    # With 10 tries and 3 facts, we should see at least 2 different responses
    # (probabilistic but extremely likely)
    assert len(seen_texts) >= 2


# ---------------------------------------------------------------------------
# /remember with no facts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_remember_no_positive_facts(service, test_db, paired_users):
    a_id, _ = paired_users

    # No positive shared facts (existing facts in conftest are not categorized)
    responses = await service.handle_remember(a_id)
    assert len(responses) == 1
    text = responses[0].text.lower()
    # Should mention no memories yet
    assert "memor" in text or "yet" in text or "chat" in text


# ---------------------------------------------------------------------------
# /remember unpaired user
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_remember_unpaired(service, test_db):
    await db_mod.create_user(test_db, 9999, "Solo")

    responses = await service.handle_remember(9999)
    assert len(responses) == 1
    # Should indicate user needs to pair
    text = responses[0].text.lower()
    assert "pair" in text or "partner" in text


# ---------------------------------------------------------------------------
# /remember only picks shared positive-category facts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_remember_ignores_private_facts(service, test_db, paired_users):
    a_id, b_id = paired_users

    # Add a private fact (should not appear in /remember)
    await db_mod.add_fact(
        test_db, a_id, "I secretly love chocolate", "private",
        category="relationship",
    )

    responses = await service.handle_remember(a_id)
    assert len(responses) == 1
    # Should show empty message since no shared positive facts exist
    assert "chocolate" not in responses[0].text


@pytest.mark.asyncio
async def test_remember_ignores_non_positive_categories(service, test_db, paired_users):
    a_id, b_id = paired_users

    # Add shared fact with non-positive category
    await db_mod.add_fact(
        test_db, a_id, "We need to fix the plumbing", "shared",
        category="household",
    )

    responses = await service.handle_remember(a_id)
    assert len(responses) == 1
    assert "plumbing" not in responses[0].text
