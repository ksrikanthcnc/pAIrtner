"""Tests for memory-related callbacks — core proposal confirm/dismiss."""

from __future__ import annotations

import pytest

from pairtner import db as db_mod


@pytest.mark.asyncio
async def test_mc_confirm_promotes_to_core(service, test_db, paired_users):
    """mc:<id>:y should promote a fact to core."""
    a_id, _ = paired_users

    # Add a working fact (simulating what propose_core does)
    fact_id = await db_mod.add_fact(test_db, a_id, "I'm an introvert", "private")

    # Confirm it as core
    responses = await service.handle_callback(a_id, f"mc:{fact_id}:y")

    assert len(responses) == 1
    assert "core" in responses[0].text.lower() or "✅" in responses[0].text


@pytest.mark.asyncio
async def test_mc_dismiss_keeps_as_working(service, test_db, paired_users):
    """mc:<id>:n should keep the fact as working (no change)."""
    a_id, _ = paired_users

    fact_id = await db_mod.add_fact(test_db, a_id, "Maybe important", "private")

    responses = await service.handle_callback(a_id, f"mc:{fact_id}:n")

    assert len(responses) == 1
    # Should not error
    assert "error" not in responses[0].text.lower()


@pytest.mark.asyncio
async def test_mc_invalid_id_returns_error(service, test_db, paired_users):
    """mc with nonexistent id should not crash."""
    a_id, _ = paired_users
    responses = await service.handle_callback(a_id, "mc:999999:y")
    # Should return something (error message), not crash
    assert len(responses) <= 1
