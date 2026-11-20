"""Tests for the unified v2 summary system (pairtner/core/summary.py)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from pairtner.core.summary import (
    create_couple_summary,
    create_summary,
    run_periodic_summaries,
)
from pairtner.storage.schema_v2 import SCHEMA_V2_SQL, make_relationship_id
from tests.helpers import MockLLMClient

ALICE, BOB = 1001, 1002
REL = make_relationship_id(ALICE, BOB)


@pytest.fixture
def db_path(tmp_path) -> str:
    path = str(tmp_path / "v2.db")
    con = sqlite3.connect(path)
    con.executescript(SCHEMA_V2_SQL)
    con.execute("INSERT INTO users (tg_id, name) VALUES (?, 'Alice')", (ALICE,))
    con.execute("INSERT INTO users (tg_id, name) VALUES (?, 'Bob')", (BOB,))
    con.execute("INSERT INTO relationships (id, user_a_id, user_b_id) VALUES (?,?,?)",
                (REL, ALICE, BOB))
    con.commit()
    con.close()
    return path


def _sql(db_path, sql, params=()):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    con.commit()
    con.close()
    return rows


def add_facts(db_path, n, created="2026-11-18T10:00:00", owner=ALICE,
              status="active", msg_source="user"):
    for i in range(n):
        _sql(db_path,
             "INSERT INTO facts (owner_id, relationship_id, text, kind, status, msg_source, "
             "created_at) VALUES (?,?,?,'working',?,?,?)",
             (owner, REL, f"fact {created[:10]} #{i}", status, msg_source, created))


def add_messages(db_path, n, day="2026-11-18", user=ALICE):
    for i in range(n):
        _sql(db_path,
             "INSERT INTO messages (user_id, relationship_id, role, content, created_at) "
             "VALUES (?,?,'user','hi',?)", (user, REL, f"{day}T09:{i:02d}:00"))


def resp(tag="x"):
    return {"reply": json.dumps({
        "resolved": [f"r-{tag}"], "ongoing": [f"o-{tag}"], "new": [f"n-{tag}"],
        "narrative": f"Narrative {tag}.",
    })}


def facts_of(db_path, kind):
    return _sql(db_path, "SELECT * FROM facts WHERE kind=? ORDER BY id", (kind,))


@pytest.mark.asyncio
async def test_weekly_summary_created(db_path):
    add_facts(db_path, 4, status="active")
    add_facts(db_path, 1, status="resolved", msg_source="bot")
    llm = MockLLMClient([resp("w")])
    fid = await create_summary(db_path, llm, ALICE, "weekly", "2026-W47", REL)
    assert fid
    row = facts_of(db_path, "weekly")[0]
    assert row["id"] == fid and row["owner_id"] == ALICE
    meta = json.loads(row["metadata"])
    assert meta["period"] == "2026-W47"
    assert meta["start"] == "2026-11-16" and meta["end"] == "2026-11-23"
    assert meta["resolved"] == ["r-w"] and meta["ongoing"] == ["o-w"] and meta["new"] == ["n-w"]
    assert meta["narrative"] == "Narrative w."
    assert len(json.loads(row["source_ids"])) == 5  # bot-source fact included
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_source_facts_archived(db_path):
    add_facts(db_path, 4)
    add_facts(db_path, 2, created="2026-11-25T10:00:00")  # outside window
    await create_summary(db_path, MockLLMClient([resp()]), ALICE, "weekly", "2026-W47", REL)
    rows = _sql(db_path, "SELECT created_at, archived_at FROM facts WHERE kind='working'")
    inside = [r for r in rows if r["created_at"].startswith("2026-11-18")]
    outside = [r for r in rows if r["created_at"].startswith("2026-11-25")]
    assert len(inside) == 4 and all(r["archived_at"] for r in inside)
    assert all(r["archived_at"] is None for r in outside)
    assert facts_of(db_path, "weekly")[0]["archived_at"] is None


@pytest.mark.asyncio
async def test_sparse_window_not_skipped(db_path):
    """Even 1-2 facts get summarized (MIN_ITEMS=1, no fact left behind)."""
    add_facts(db_path, 2)
    llm = MockLLMClient([resp("sparse")])
    fid = await create_summary(db_path, llm, ALICE, "weekly", "2026-W47", REL)
    assert fid is not None  # summary created even for 2 facts
    assert len(llm.calls) == 1
    assert facts_of(db_path, "weekly") != []


@pytest.mark.asyncio
async def test_empty_window_skipped(db_path):
    """Zero facts = no summary (can't summarize nothing)."""
    llm = MockLLMClient([resp()])
    assert await create_summary(db_path, llm, ALICE, "weekly", "2026-W47", REL) is None
    assert llm.calls == []


@pytest.mark.asyncio
async def test_dense_window_batched(db_path):
    add_facts(db_path, 45)  # above BATCH_THRESHOLD of 40 for medium capability
    llm = MockLLMClient([resp("a"), resp("b"), resp("c")])
    fid = await create_summary(db_path, llm, ALICE, "weekly", "2026-W47", REL)
    assert fid
    assert len(llm.calls) == 3  # 20 + 20 + 5 (BATCH_SIZE=20 for medium)
    meta = json.loads(facts_of(db_path, "weekly")[0]["metadata"])
    assert meta["resolved"] == ["r-a", "r-b", "r-c"]
    assert len(json.loads(facts_of(db_path, "weekly")[0]["source_ids"])) == 45
    assert all(r["archived_at"] for r in _sql(db_path, "SELECT archived_at FROM facts WHERE kind='working'"))


@pytest.mark.asyncio
async def test_daily_threshold(db_path):
    add_facts(db_path, 4)
    add_messages(db_path, 9)
    llm = MockLLMClient([resp("d")])
    assert await create_summary(db_path, llm, ALICE, "daily", "2026-11-18", REL) is None
    assert llm.calls == []
    add_messages(db_path, 1, day="2026-11-18")  # 10th (same minute slot 00 is fine)
    # add_messages reuses minute 00; use a distinct timestamp instead
    _sql(db_path, "UPDATE messages SET created_at='2026-11-18T12:00:00' WHERE id=1")
    assert await create_summary(db_path, llm, ALICE, "daily", "2026-11-18", REL)
    assert len(facts_of(db_path, "daily")) == 1


@pytest.mark.asyncio
async def test_missed_periods_catchup(db_path):
    add_facts(db_path, 3, created="2026-11-03T10:00:00")  # W45
    add_facts(db_path, 3, created="2026-11-10T10:00:00")  # W46
    llm = MockLLMClient([resp("w45"), resp("w46")])
    now = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)  # W47 still in progress
    out = await run_periodic_summaries(db_path, llm, ALICE, REL, now=now,
                                       intervals=["weekly"])
    assert out == {"weekly": 2}
    assert len(llm.calls) == 2
    periods = [json.loads(r["metadata"])["period"] for r in facts_of(db_path, "weekly")]
    assert periods == ["2026-W45", "2026-W46"]
    # idempotent: nothing more is due
    assert await run_periodic_summaries(db_path, llm, ALICE, REL, now=now,
                                        intervals=["weekly"]) == {}


@pytest.mark.asyncio
async def test_monthly_archives_weeklies(db_path):
    for i, start in enumerate(["2026-11-02", "2026-11-09", "2026-11-16"]):
        _sql(db_path,
             "INSERT INTO facts (owner_id, relationship_id, text, kind, metadata) "
             "VALUES (?,?,?,'weekly',?)",
             (ALICE, REL, f"week {i}", json.dumps({"period": f"w{i}", "start": start})))
    # a weekly from a different month must stay untouched
    _sql(db_path,
         "INSERT INTO facts (owner_id, relationship_id, text, kind, metadata) "
         "VALUES (?,?,?,'weekly',?)",
         (ALICE, REL, "dec week", json.dumps({"period": "wd", "start": "2026-12-07"})))
    llm = MockLLMClient([resp("m")])
    fid = await create_summary(db_path, llm, ALICE, "monthly", "2026-11", REL)
    assert fid
    weeklies = facts_of(db_path, "weekly")
    assert [bool(w["archived_at"]) for w in weeklies] == [True, True, True, False]
    assert json.loads(facts_of(db_path, "monthly")[0]["metadata"])["period"] == "2026-11"


@pytest.mark.asyncio
async def test_couple_summary(db_path):
    for owner, tag in ((ALICE, "alice"), (BOB, "bob")):
        add_facts(db_path, 3, owner=owner)
        await create_summary(db_path, MockLLMClient([resp(tag)]), owner, "weekly",
                             "2026-W47", REL)
    llm = MockLLMClient([resp("couple")])
    fid = await create_couple_summary(db_path, llm, REL, "weekly", "2026-W47")
    assert fid
    assert len(llm.calls) == 1
    assert "Narrative alice." in llm.calls[0]["user_content"]
    assert "Narrative bob." in llm.calls[0]["user_content"]
    row = [r for r in facts_of(db_path, "weekly") if r["id"] == fid][0]
    assert row["owner_id"] is None and row["relationship_id"] == REL
    # partner summaries survive; second call is a no-op
    assert all(r["archived_at"] is None for r in facts_of(db_path, "weekly"))
    assert await create_couple_summary(db_path, llm, REL, "weekly", "2026-W47") is None
