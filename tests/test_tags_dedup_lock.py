"""Tests: existing-tag prompt injection, search source_ids dedup, Telegram per-user lock."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import aiosqlite
import pytest

from pairtner.adapters.telegram_adapter import TelegramAdapter
from pairtner.core.llm_tools import _dedup_by_source_ids, execute_search_memory
from pairtner.core.prompts import build_system_prompt
from pairtner.db import create_user, get_couple_tags, init_db, set_partner

ALICE, BOB, CAROL = 2001, 2002, 2003


@pytest.fixture
async def db_path(tmp_path) -> str:
    path = str(tmp_path / "t.db")
    await init_db(path)
    for uid, name in ((ALICE, "A"), (BOB, "B"), (CAROL, "C")):
        await create_user(path, uid, name)
    await set_partner(path, ALICE, BOB)
    return path


async def _ins(path, owner, text, tag=None, kind="working", source_ids=None) -> int:
    async with aiosqlite.connect(path) as db:
        cur = await db.execute(
            "INSERT INTO facts(owner_id, text, tag, kind, source_ids) VALUES (?,?,?,?,?)",
            (owner, text, tag, kind, json.dumps(source_ids) if source_ids else None),
        )
        await db.commit()
        return cur.lastrowid


# ── 1. tags ──────────────────────────────────────────────────────────────

def test_prompt_includes_existing_tags():
    p = build_system_prompt(existing_tags=["kids", "work"])
    assert "Existing tags for this couple: [kids, work]." in p
    assert "Use an existing tag if it fits" in p


@pytest.mark.parametrize("tags", [None, []])
def test_prompt_skips_tag_line_when_empty(tags):
    assert "Existing tags" not in build_system_prompt(existing_tags=tags)


async def test_get_couple_tags_distinct_sorted_both_partners(db_path):
    await _ins(db_path, ALICE, "a", "work")
    await _ins(db_path, ALICE, "b", "work")
    await _ins(db_path, BOB, "c", "kids")
    await _ins(db_path, ALICE, "d", "")
    await _ins(db_path, BOB, "e", None)
    await _ins(db_path, CAROL, "f", "other-couple")
    assert await get_couple_tags(db_path, ALICE, BOB) == ["kids", "work"]
    assert await get_couple_tags(db_path, CAROL, None) == ["other-couple"]
    assert await get_couple_tags(db_path, 999, None) == []


# ── 2. search dedup ──────────────────────────────────────────────────────

def test_dedup_drops_summary_covering_returned_fact():
    res = [
        {"id": 3, "source_ids": "[1, 2]"},
        {"id": 1, "source_ids": None},
        {"id": 9, "source_ids": None},
    ]
    assert [r["id"] for r in _dedup_by_source_ids(res)] == [1, 9]


def test_dedup_keeps_summary_when_sources_absent_and_handles_bad_json():
    res = [{"id": 3, "source_ids": "[1, 2]"}, {"id": 4, "source_ids": "not json"}]
    assert [r["id"] for r in _dedup_by_source_ids(res)] == [3, 4]


def test_dedup_transitive():
    res = [
        {"id": 10, "source_ids": [5]},
        {"id": 5, "source_ids": [1]},
        {"id": 1, "source_ids": None},
    ]
    assert [r["id"] for r in _dedup_by_source_ids(res)] == [1]


async def test_execute_search_memory_dedups(db_path):
    f1 = await _ins(db_path, ALICE, "weekend hiking trip planned")
    await _ins(db_path, ALICE, "summary: hiking trip planned for weekend",
               kind="weekly", source_ids=[f1])
    out = await execute_search_memory(db_path, ALICE, "hiking")
    assert [r["id"] for r in out] == [f1]
    assert "source_ids" not in out[0]


# ── 3. telegram lock ─────────────────────────────────────────────────────

async def test_on_message_serialises_same_user():
    active = 0
    peak = 0

    class Svc:
        async def handle_message(self, uid, name, text):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1
            return []

    adapter = TelegramAdapter(Svc(), SimpleNamespace(allowed_user_ids=[]))
    assert adapter._lock(1) is adapter._lock(1)
    assert adapter._lock(1) is not adapter._lock(2)

    class Chat:
        async def send_action(self, *_):
            pass

    def upd(uid):
        return SimpleNamespace(
            effective_user=SimpleNamespace(id=uid, first_name="x"),
            message=SimpleNamespace(text="hi"),
            effective_chat=Chat(),
        )

    await asyncio.gather(*(adapter._on_message(upd(1), None) for _ in range(3)))
    assert peak == 1
    peak = 0
    await asyncio.gather(adapter._on_message(upd(1), None), adapter._on_message(upd(2), None))
    assert peak == 2
