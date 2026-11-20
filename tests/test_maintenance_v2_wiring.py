"""Wiring tests: v2 summary engine + nudge cleanup in the maintenance loop."""
from __future__ import annotations

import asyncio
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from pairtner.core import memory_maintenance as mm
from pairtner.storage.schema_v2 import SCHEMA_V2_SQL, make_relationship_id
from tests.helpers import MockLLMClient

ALICE, BOB, CAROL, DAVE = 1001, 1002, 1003, 1004
REL = make_relationship_id(ALICE, BOB)
REL2 = make_relationship_id(CAROL, DAVE)


def _sql(path, sql, params=()):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(sql, params).fetchall()]
    con.commit()
    con.close()
    return rows


@pytest.fixture
def db_path(tmp_path) -> str:
    path = str(tmp_path / "v2.db")
    con = sqlite3.connect(path)
    con.executescript(SCHEMA_V2_SQL)
    for uid, name in [(ALICE, "A"), (BOB, "B"), (CAROL, "C"), (DAVE, "D")]:
        con.execute("INSERT INTO users (tg_id, name) VALUES (?,?)", (uid, name))
    for rid, a, b in [(REL, ALICE, BOB), (REL2, CAROL, DAVE)]:
        con.execute("INSERT INTO relationships (id, user_a_id, user_b_id) VALUES (?,?,?)",
                    (rid, a, b))
    con.commit()
    con.close()
    return path


def _nudge(path, rel, owner, target, created, ttl=24):
    _sql(path,
         "INSERT INTO facts (owner_id, relationship_id, text, kind, status, nudge_target_id, "
         "nudge_type, nudge_suggestion, nudge_ttl_h, nudge_status, created_at) "
         "VALUES (?,?,?,'working','active',?,'appreciation','be kind',?,'pending',?)",
         (owner, rel, "private", target, ttl, created))


@pytest.mark.asyncio
async def test_v2_runs_periodic_summaries(db_path):
    with patch("pairtner.core.summary.run_periodic_summaries",
               new=AsyncMock(return_value={})) as m:
        await mm._run_v2_maintenance(db_path, MockLLMClient(), ALICE)
    m.assert_awaited_once()
    assert m.await_args.args[2] == ALICE and m.await_args.args[3] == REL
    meta = _sql(db_path, "SELECT value FROM user_meta WHERE user_id=? AND key='last_v2_maintenance'",
                (ALICE,))
    assert meta and meta[0]["value"]


@pytest.mark.asyncio
async def test_v2_creates_couple_summary_when_both_have_summaries(db_path):
    for owner in (ALICE, BOB):
        _sql(db_path,
             "INSERT INTO facts (owner_id, relationship_id, text, kind, status, metadata) "
             "VALUES (?,?,'s','weekly','active','{\"period\":\"2026-W40\"}')", (owner, REL))
    with patch("pairtner.core.summary.run_periodic_summaries", new=AsyncMock(return_value={})), \
         patch("pairtner.core.summary.create_couple_summary",
               new=AsyncMock(return_value=1)) as c:
        await mm._run_v2_maintenance(db_path, MockLLMClient(), ALICE)
    c.assert_awaited_once()
    assert c.await_args.args[2:] == (REL, "weekly", "2026-W40")


@pytest.mark.asyncio
async def test_v2_expires_stale_nudges(db_path):
    _nudge(db_path, REL, ALICE, BOB, "2020-01-01 00:00:00")
    with patch("pairtner.core.summary.run_periodic_summaries", new=AsyncMock(return_value={})):
        await mm._run_v2_maintenance(db_path, MockLLMClient(), ALICE)
    st = _sql(db_path, "SELECT nudge_status FROM facts")[0]["nudge_status"]
    assert st == "expired"


@pytest.mark.asyncio
async def test_v2_enforces_max_active(db_path):
    for i in range(8):
        _nudge(db_path, REL, ALICE, BOB, f"2099-01-0{i+1} 00:00:00", ttl=100000)
    with patch("pairtner.core.summary.run_periodic_summaries", new=AsyncMock(return_value={})):
        await mm._run_v2_maintenance(db_path, MockLLMClient(), ALICE)
    rows = _sql(db_path, "SELECT nudge_status FROM facts")
    assert sum(r["nudge_status"] == "pending" for r in rows) == 5
    assert sum(r["nudge_status"] == "expired" for r in rows) == 3


async def _run_loop_once(db_path):
    real_sleep = asyncio.sleep
    calls = {"n": 0}

    async def fake_sleep(_):
        calls["n"] += 1
        if calls["n"] > 1:
            raise asyncio.CancelledError
        await real_sleep(0)

    with patch.object(mm.asyncio, "sleep", fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await mm.memory_maintenance_loop(db_path, MockLLMClient(), interval=0)


@pytest.mark.asyncio
async def test_v1_schema_falls_back_to_consolidation(tmp_path):
    path = str(tmp_path / "v1.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY, owner_id INTEGER, archived_at TEXT)")
    con.execute("INSERT INTO facts (owner_id) VALUES (7)")
    con.commit()
    con.close()
    with patch.object(mm, "MemoryAPI") as api, \
         patch.object(mm, "_maybe_consolidate", new=AsyncMock()) as old, \
         patch.object(mm, "_run_v2_maintenance", new=AsyncMock()) as new:
        api.return_value.run_aging = AsyncMock(return_value=type("R", (), {"marked_outdated": 0})())
        await _run_loop_once(path)
    old.assert_awaited_once()
    new.assert_not_awaited()


@pytest.mark.asyncio
async def test_error_in_one_user_does_not_block_others(db_path):
    for uid, rel in [(ALICE, REL), (CAROL, REL2)]:
        _sql(db_path, "INSERT INTO facts (owner_id, relationship_id, text, kind) "
                      "VALUES (?,?,'x','working')", (uid, rel))
    seen = []

    async def fake_v2(_db, _llm, uid):
        seen.append(uid)
        if uid == ALICE:
            raise RuntimeError("boom")

    with patch.object(mm, "MemoryAPI") as api, \
         patch.object(mm, "_run_v2_maintenance", new=fake_v2):
        api.return_value.run_aging = AsyncMock(return_value=type("R", (), {"marked_outdated": 0})())
        await _run_loop_once(db_path)
    assert sorted(seen) == [ALICE, CAROL]
