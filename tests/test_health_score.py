"""Tests for the relationship health scoring engine."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from pairtner.core.health_score import (
    compute_health_score,
    get_latest_health_score,
    store_health_score,
)
from pairtner.storage.schema_v2 import SCHEMA_V2_SQL, make_relationship_id

A, B = 1001, 1002
REL = make_relationship_id(A, B)


def _ts(days_ago: float) -> str:
    return (datetime.utcnow() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture
def db_path(tmp_path) -> str:
    path = str(tmp_path / "hs.db")
    con = sqlite3.connect(path)
    con.executescript(SCHEMA_V2_SQL)
    con.execute("INSERT INTO users (tg_id, name) VALUES (?, 'A'), (?, 'B')", (A, B))
    con.execute("INSERT INTO relationships (id, user_a_id, user_b_id) VALUES (?,?,?)", (REL, A, B))
    con.commit()
    con.close()
    return path


def sql(path, q, p=()):
    con = sqlite3.connect(path)
    con.execute(q, p)
    con.commit()
    con.close()


def msg(path, user, days_ago):
    sql(path, "INSERT INTO messages (user_id, relationship_id, role, content, created_at) "
              "VALUES (?,?,'user','hi',?)", (user, REL, _ts(days_ago)))


def fact(path, text, intensity=0.8, status="active", created=1, updated=None,
         nudge_status=None, cancel_reason=None):
    sql(path,
        "INSERT INTO facts (owner_id, relationship_id, text, intensity, status, created_at, "
        "updated_at, nudge_target_id, nudge_type, nudge_status, nudge_cancel_reason) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (A, REL, text, intensity, status, _ts(created), _ts(updated if updated is not None else created),
         B if nudge_status else None, "support" if nudge_status else None,
         nudge_status or "pending", cancel_reason))


async def test_balanced_communication_scores_high(db_path):
    for i in range(10):
        msg(db_path, A, i)
        msg(db_path, B, i)
    r = await compute_health_score(db_path, REL)
    assert r["communication_balance"] >= 90


async def test_one_sided_communication_scores_low(db_path):
    for i in range(10):
        msg(db_path, A, i)
    r = await compute_health_score(db_path, REL)
    assert r["communication_balance"] <= 10


async def test_positive_sentiment_scores_high(db_path):
    for t in ("loves the dinner", "feeling happy and grateful", "enjoyed our walk"):
        fact(db_path, t)
    r = await compute_health_score(db_path, REL)
    assert r["sentiment_trend"] >= 90


async def test_nudge_all_addressed_scores_high(db_path):
    for _ in range(3):
        fact(db_path, "x", nudge_status="delivered")
    r = await compute_health_score(db_path, REL)
    assert r["nudge_effectiveness"] == 100


async def test_nudge_all_expired_scores_low(db_path):
    for _ in range(3):
        fact(db_path, "x", nudge_status="expired")
    r = await compute_health_score(db_path, REL)
    assert r["nudge_effectiveness"] == 0


async def test_fast_conflict_resolution_scores_high(db_path):
    fact(db_path, "we had an argument", status="resolved", created=5, updated=4.9)
    fact(db_path, "felt ignored", status="resolved", created=3, updated=2.9)
    r = await compute_health_score(db_path, REL)
    assert r["conflict_resolution"] >= 90


async def test_slow_conflict_resolution_scores_low(db_path):
    fact(db_path, "we had an argument", status="active", created=20)
    r = await compute_health_score(db_path, REL)
    assert r["conflict_resolution"] <= 20


async def test_consistent_engagement_scores_high(db_path):
    for i in range(30):
        msg(db_path, A, i + 0.1)
    r = await compute_health_score(db_path, REL)
    assert r["engagement"] >= 90
    sparse = await compute_health_score(db_path, REL, window_days=30)
    assert sparse["engagement"] > 0


async def test_gappy_engagement_scores_lower(db_path):
    msg(db_path, A, 29)
    msg(db_path, A, 1)
    r = await compute_health_score(db_path, REL)
    assert r["engagement"] < 30


async def test_no_data_returns_neutral(db_path):
    r = await compute_health_score(db_path, REL)
    assert r["total"] == 50
    for k in ("communication_balance", "sentiment_trend", "nudge_effectiveness",
              "conflict_resolution", "engagement"):
        assert r[k] == 50
    assert r["window_days"] == 30


async def test_health_score_stored_in_analysis_results(db_path):
    score = {"total": 72, "communication_balance": 65, "sentiment_trend": 80,
             "nudge_effectiveness": 60, "conflict_resolution": 75, "engagement": 80,
             "computed_at": "2026-11-20T10:00:00", "window_days": 30}
    await store_health_score(db_path, REL, score)
    con = sqlite3.connect(db_path)
    row = con.execute("SELECT result_type, content FROM analysis_results "
                      "WHERE relationship_id = ?", (REL,)).fetchone()
    con.close()
    assert row[0] == "health_score"
    assert json.loads(row[1]) == score
    assert await get_latest_health_score(db_path, REL) == score


async def test_timeline_includes_health_score(db_path):
    from pairtner.rest.timeline import build_timeline
    score = await compute_health_score(db_path, REL)
    await store_health_score(db_path, REL, score)
    con = sqlite3.connect(db_path)
    ev = [e for e in build_timeline(con, REL) if e["type"] == "health_score"]
    con.close()
    assert len(ev) == 1
    assert ev[0]["total"] == 50 and "engagement" in ev[0]["dimensions"]
