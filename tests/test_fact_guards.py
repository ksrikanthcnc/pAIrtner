"""Guards on LLM-proposed facts: never auto-shared, never duplicated."""
from __future__ import annotations

import pytest

from couples_bot import db as db_mod
from couples_bot.handlers import BotService


@pytest.mark.asyncio
async def test_llm_cannot_publish_a_fact(service: BotService, test_db, paired_users):
    """A fact the model labels 'shared' is stored private (design rule 1)."""
    a_id, _ = paired_users
    await service._save_facts(a_id, [{"text": "Alice wants a holiday", "visibility": "shared"}])
    rows = [f for f in await db_mod.get_active_facts(test_db, a_id) if f["text"] == "Alice wants a holiday"]
    assert [r["visibility"] for r in rows] == ["private"]


@pytest.mark.asyncio
async def test_shared_label_does_not_reach_partner_prompt_inputs(service, test_db, paired_users):
    a_id, b_id = paired_users
    await service._save_facts(a_id, [{"text": "Alice secretly plans a surprise", "visibility": "shared"}])
    partner_shared = [f["text"] for f in await db_mod.get_active_facts(test_db, b_id, "shared")]
    assert "Alice secretly plans a surprise" not in partner_shared


@pytest.mark.asyncio
async def test_duplicate_fact_is_skipped(service: BotService, test_db, paired_users):
    a_id, _ = paired_users
    facts = [{"text": "Alice likes tea"}]
    assert await service._save_facts(a_id, facts) == 1
    # same fact again, differing only in case/spacing/punctuation
    assert await service._save_facts(a_id, [{"text": "  alice likes  TEA. "}]) == 0
    texts = [f["text"] for f in await db_mod.get_active_facts(test_db, a_id)]
    assert sum(1 for t in texts if t.lower().rstrip(". ") == "alice likes tea") == 1


@pytest.mark.asyncio
async def test_duplicates_within_one_batch_are_collapsed(service, test_db, paired_users):
    a_id, _ = paired_users
    n = await service._save_facts(a_id, [{"text": "Alice runs"}, {"text": "alice runs"}])
    assert n == 1
