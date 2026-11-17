"""Tests for context budget enforcement in get_user_context_data."""
from __future__ import annotations

import pytest
from pairtner import db as db_mod
from pairtner.db import _apply_word_cap, _get_context_budgets


class TestApplyWordCap:
    def test_under_cap(self):
        items = ["short one", "short two"]
        assert _apply_word_cap(items, 100) == items

    def test_over_cap(self):
        items = [f"word{i} " * 20 for i in range(10)]  # 20 words each
        result = _apply_word_cap(items, 50)
        assert len(result) < len(items)
        total_words = sum(len(r.split()) for r in result)
        assert total_words <= 50

    def test_first_item_always_included(self):
        items = ["a very long item " * 20]  # over any reasonable cap
        result = _apply_word_cap(items, 5)
        assert len(result) == 1  # First item is always included

    def test_empty(self):
        assert _apply_word_cap([], 100) == []


class TestGetContextBudgets:
    def test_returns_dict(self):
        budgets = _get_context_budgets()
        assert isinstance(budgets, dict)
        assert "core" in budgets or "working" in budgets


@pytest.mark.asyncio
async def test_context_budget_caps_working_facts(test_db):
    """With many working facts, the context builder should cap by word budget."""
    await db_mod.ensure_user(test_db, 1001, "Alice")

    # Add 100 working facts (each ~10 words)
    for i in range(100):
        await db_mod.add_fact(
            test_db, 1001,
            f"Working memory number {i} about something important to remember",
            "private",
        )

    data = await db_mod.get_user_context_data(test_db, 1001)
    # With 400 word budget for working, ~10 words each, should cap around 40 items
    assert len(data["private_facts"]) < 100
    # But should have some
    assert len(data["private_facts"]) > 0


@pytest.mark.asyncio
async def test_context_core_before_working(test_db):
    """Core facts should appear before working facts in the context."""
    await db_mod.ensure_user(test_db, 1001, "Alice")

    # Add working facts first
    await db_mod.add_fact(test_db, 1001, "Working fact one", "private")
    await db_mod.add_fact(test_db, 1001, "Working fact two", "private")

    # Add and promote to core
    fid = await db_mod.add_fact(test_db, 1001, "Core identity fact", "private")
    await db_mod.promote_to_core(test_db, fid)

    data = await db_mod.get_user_context_data(test_db, 1001)
    # Core should be first
    assert "(core)" in data["private_facts"][0]
    assert "Core identity fact" in data["private_facts"][0]
