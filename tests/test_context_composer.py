"""Tests for the structural-isolation ContextComposer."""

from __future__ import annotations

import aiosqlite
import pytest
import pytest_asyncio

from pairtner.core.context_composer import (
    TOTAL_WORD_BUDGET,
    ContextComposer,
    _assert_partner_safe,
)
from pairtner.storage.schema_v2 import SCHEMA_V2_SQL, make_relationship_id

ALICE, BOB = 1, 2
REL = make_relationship_id(ALICE, BOB)


@pytest_asyncio.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "t.db")
    async with aiosqlite.connect(path) as db:
        await db.executescript(SCHEMA_V2_SQL)
        await db.execute(
            "CREATE VIRTUAL TABLE facts_fts USING fts5(text, content=facts, content_rowid=id)"
        )
        await db.execute(
            "CREATE TRIGGER facts_fts_ai AFTER INSERT ON facts BEGIN "
            "INSERT INTO facts_fts(rowid, text) VALUES (new.id, new.text); END"
        )
        await db.execute("INSERT INTO users(tg_id, name) VALUES (1,'Alice'),(2,'Bob')")
        await db.execute(
            "INSERT INTO relationships(id, user_a_id, user_b_id) VALUES (?,?,?)",
            (REL, ALICE, BOB),
        )
        await db.commit()
    return path


async def add(path, owner, text, visibility="shared", kind="working", **kw):
    cols = {"owner_id": owner, "relationship_id": REL, "text": text,
            "visibility": visibility, "kind": kind, **kw}
    async with aiosqlite.connect(path) as db:
        await db.execute(
            f"INSERT INTO facts({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            tuple(cols.values()),
        )
        await db.commit()


def flat(ctx) -> str:
    parts = (ctx.core_facts + ctx.working_facts + ctx.partner_facts
             + ctx.nudge_suggestions + ctx.summaries + ctx.proactive
             + [m[1] for m in ctx.recent_messages])
    return "\n".join(parts)


async def test_own_private_facts_included(db_path):
    await add(db_path, BOB, "Bob secretly worries about money", "private")
    ctx = await ContextComposer(db_path).build_context(BOB, REL)
    assert "Bob secretly worries about money" in ctx.working_facts


async def test_partner_private_excluded(db_path):
    await add(db_path, ALICE, "Alice private secret one", "private")
    await add(db_path, ALICE, "Alice private core secret", "private", kind="core")
    await add(db_path, ALICE, "Alice private with nudge", "private",
              nudge_target_id=BOB, nudge_suggestion="Be gentle", nudge_status="pending")
    ctx = await ContextComposer(db_path).build_context(BOB, REL, "Alice private secret")
    assert "private" not in flat(ctx).lower().replace("be gentle", "")
    assert ctx.partner_facts == []


async def test_partner_nudge_eligible_included(db_path):
    await add(db_path, ALICE, "Alice likes surprise dinners", "shared")
    await add(db_path, ALICE, "Alice enjoys hiking together", "shared")
    ctx = await ContextComposer(db_path).build_context(BOB, REL)
    assert set(ctx.partner_facts) == {
        "Alice likes surprise dinners", "Alice enjoys hiking together"}


async def test_nudge_suggestion_injected(db_path):
    await add(db_path, ALICE, "Alice feels ignored when Bob works late", "private",
              nudge_target_id=BOB, nudge_type="attention",
              nudge_suggestion="Consider a small gesture of attention this week",
              nudge_status="pending")
    ctx = await ContextComposer(db_path).build_context(BOB, REL)
    assert ctx.nudge_suggestions == ["Consider a small gesture of attention this week"]
    assert ctx.pending_nudge_count == 1
    assert "ignored" not in flat(ctx)


async def test_core_facts_always_present(db_path):
    await add(db_path, ALICE, "Alice is a nurse", "private", kind="core",
              intensity=0.0, status="resolved")
    for i in range(60):
        await add(db_path, ALICE, f"working fact number {i}", intensity=0.9)
    ctx = await ContextComposer(db_path).build_context(ALICE, REL)
    assert "Alice is a nurse" in ctx.core_facts


async def test_word_budget_respected(db_path):
    for i in range(200):
        await add(db_path, ALICE, " ".join(f"w{i}x{j}" for j in range(20)), "private")
        await add(db_path, BOB, " ".join(f"p{i}x{j}" for j in range(20)), "shared")
        await add(db_path, ALICE, " ".join(f"c{i}x{j}" for j in range(20)), "private",
                  kind="core")
    async with aiosqlite.connect(db_path) as db:
        for i in range(30):
            await db.execute(
                "INSERT INTO messages(user_id, relationship_id, role, content) "
                "VALUES (?,?,?,?)", (ALICE, REL, "user", " ".join(["word"] * 40)))
        await db.commit()
    ctx = await ContextComposer(db_path).build_context(ALICE, REL, "w1x1 w2x2")
    assert ctx.total_words() <= TOTAL_WORD_BUDGET
    assert len(ctx.recent_messages) <= 20


async def test_summaries_gap_filled_one_entry_per_level(db_path):
    await add(db_path, ALICE, "Q1 story", "private", kind="quarterly")
    await add(db_path, ALICE, "Q2 story", "private", kind="quarterly")
    await add(db_path, ALICE, "Year story", "private", kind="yearly")
    await add(db_path, ALICE, "Day story", "private", kind="daily")
    ctx = await ContextComposer(db_path).build_context(ALICE, REL)
    assert len(ctx.summaries) == 3
    assert [s.split(")")[0] for s in ctx.summaries] == ["(daily", "(quarterly", "(yearly"]
    assert "Q1 story" in ctx.summaries[1] and "Q2 story" in ctx.summaries[1]


async def test_fts_removed_from_hot_path(db_path):
    """FTS5 is no longer in the context composer hot path (moved to search_memory tool).
    Verify that the composed context has no fts_matches attribute."""
    await add(db_path, ALICE, "Alice is allergic to pistachio", intensity=0.5)
    ctx = await ContextComposer(db_path).build_context(
        ALICE, REL, "what about pistachio ice cream?")
    assert not hasattr(ctx, "fts_matches")


def test_guard_rejects_private_row():
    with pytest.raises(AssertionError):
        _assert_partner_safe([{"id": 1, "visibility": "private"}])
