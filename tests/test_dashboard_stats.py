"""Stats and timeline queries over a seeded database."""
from __future__ import annotations

import json
import sqlite3

import pytest

from pairtner.dashboard import stats, store
from tests.dashboard_seed import seed_db


@pytest.fixture
def conn(tmp_path):
    path = seed_db(str(tmp_path / "dash.db"))
    c = store.open_readonly(path)
    yield c
    c.close()


def test_overview_counts(conn):
    o = stats.overview(conn)
    assert o["users"] == 5
    assert o["couples"] == 2
    assert o["messages_total"] == 4
    assert o["messages_24h"] == 4
    assert o["facts_total"] == 3
    assert o["pending_by_status"] == {"awaiting_approval": 2}
    assert o["batches_by_status"] == {"accumulating": 2}
    assert o["conflicts_by_status"] == {"open": 2}
    assert o["user_goals_by_status"] == {"active": 2}
    assert o["shared_goals_by_status"] == {"active": 1}
    assert o["usage_today"] == 3


def test_messages_per_day_fills_gaps(conn):
    days = stats.messages_per_day(conn, 7)
    assert len(days) == 7
    assert [d["day"] for d in days] == sorted(d["day"] for d in days)
    assert days[-1]["count"] == 4 and days[-1]["from_users"] == 3
    assert all(d["count"] == 0 for d in days[:-1])


def test_usage_per_model_per_day(conn):
    rows = stats.usage_per_model_per_day(conn, 7)
    assert {(r["model"], r["count"]) for r in rows} == {("model-a", 2), ("model-b", 1)}


def test_user_activity(conn):
    rows = {r["tg_id"]: r for r in stats.user_activity(conn, True)}
    assert rows[1]["messages"] == 2 and rows[1]["facts"] == 1
    assert rows[1]["relays_sent"] == 1 and rows[2]["relays_received"] == 1
    assert rows[1]["goals"] == 1
    assert rows[5]["messages"] == 0 and rows[5]["last_seen"] is None
    assert rows[1]["name"].startswith("CANARY-RAW")


def test_queue_and_conflicts_and_goals(conn):
    q = stats.queue(conn, True)
    assert len(q["relays"]) == 2 and len(q["batches"]) == 2
    assert {b["items"] for b in q["batches"]} == {1}
    assert len(stats.conflicts(conn, True)) == 2
    g = stats.goals(conn, True)
    assert len(g["user_goals"]) == 2 and len(g["shared_goals"]) == 1


def test_couples(conn):
    cs = {c["couple_id"]: c for c in stats.couples(conn)}
    assert set(cs) == {"1:2", "3:4"}
    assert cs["1:2"]["open_conflicts"] == 1 and cs["1:2"]["shared_goals"] == 1


def test_user_timeline_merges_sources_sorted(conn):
    ev = stats.user_timeline(conn, 1, True)
    kinds = {e["kind"] for e in ev}
    assert kinds == {"message", "fact", "relay", "goal"}
    assert [e["at"] for e in ev] == sorted((e["at"] for e in ev), reverse=True)
    assert any("sent" in e["label"] for e in ev)


def test_couple_timeline(conn):
    ev = stats.couple_timeline(conn, "1:2", True)
    assert {e["kind"] for e in ev} == {"timeline", "conflict", "relay", "goal", "checkin"}
    assert not any("3:4" in e["label"] for e in ev)
    assert len(stats.couple_timeline(conn, "1:2", True, limit=2)) == 2


def test_unknown_user_and_bad_couple(conn):
    with pytest.raises(store.NotFound):
        stats.user_timeline(conn, 424242, True)
    for bad in ("x", "1-2", "2:1", "1:2; DROP"):
        with pytest.raises(store.NotFound):
            stats.couple_timeline(conn, bad, True)


def test_no_raw_text_when_off(conn):
    everything = [
        stats.user_activity(conn, False), stats.queue(conn, False),
        stats.conflicts(conn, False), stats.goals(conn, False),
        stats.user_timeline(conn, 1, False), stats.user_timeline(conn, 3, False),
        stats.couple_timeline(conn, "1:2", False), stats.couple_timeline(conn, "3:4", False),
    ]
    dump = json.dumps(everything, default=str)
    assert "CANARY" not in dump
    # counts / metadata survive
    assert stats.user_activity(conn, False)[0]["messages"] >= 0
    assert any("chars" in e["label"] for e in stats.user_timeline(conn, 1, False))
