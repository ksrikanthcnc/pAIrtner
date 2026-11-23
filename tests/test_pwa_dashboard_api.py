"""Tests for the PWA dashboard REST endpoints (user scoping + admin token)."""
from __future__ import annotations

import json
import sqlite3

import pytest

from pairtner import db as db_mod
from pairtner.config import Config
from pairtner.handlers import BotService
from pairtner.rest.api import Request, RestApi
from pairtner.storage.schema_v2 import make_relationship_id
from tests.helpers import MockLLMClient

ADMIN = "admin-secret-for-tests"


class World:
    def __init__(self, api: RestApi, db: str) -> None:
        self.api = api
        self.db = db

    async def get(self, path, token=None, headers=None):
        h = {}
        if token:
            h["authorization"] = f"Bearer {token}"
        h.update(headers or {})
        resp = await self.api.handle(Request("GET", path, h, b"", "10.0.0.9"))
        return resp.status, json.loads(resp.body)

    async def register(self, name):
        resp = await self.api.handle(Request(
            "POST", "/api/v1/register", {"content-type": "application/json"},
            json.dumps({"name": name}).encode(), "10.0.0.1"))
        body = json.loads(resp.body)
        assert resp.status == 201, body
        return body["user_id"], body["token"]


@pytest.fixture
async def world(test_db, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", ADMIN)
    cfg = Config(telegram_token="", db_path=test_db, rest_registration="open",
                 rest_long_poll_max_seconds=1)
    api = RestApi(BotService(cfg, test_db, MockLLMClient()), cfg)
    return World(api, test_db)


@pytest.fixture
async def couple(world):
    """alice + bob in a couple relationship, plus carol outside it."""
    a, ta = await world.register("alice")
    b, tb = await world.register("bob")
    c, tc = await world.register("carol")
    rid = make_relationship_id(a, b)
    lo, hi = sorted((a, b))
    with sqlite3.connect(world.db) as conn:
        conn.execute("INSERT INTO relationships (id, user_a_id, user_b_id) VALUES (?,?,?)",
                     (rid, lo, hi))
    return {"rid": rid, "a": (a, ta), "b": (b, tb), "c": (c, tc)}


async def _fact(db, owner, text, **kw):
    return await db_mod.add_fact(db, owner, text, **kw)


# ── facts filters ────────────────────────────────────────────────────────

async def test_facts_filters_and_isolation(world, couple):
    (a, ta), (b, tb) = couple["a"], couple["b"]
    await _fact(world.db, a, "Alice likes tea", tag="food", kind="core", visibility="private")
    await _fact(world.db, a, "Alice kid recital", tag=["kids", "family"], visibility="shared")
    await _fact(world.db, b, "Bob secret", tag="food", visibility="private")

    _, d = await world.get("/api/v1/facts", ta)
    assert {f["text"] for f in d["facts"]} == {"Alice likes tea", "Alice kid recital"}

    _, d = await world.get("/api/v1/facts?tag=kids", ta)
    assert [f["text"] for f in d["facts"]] == ["Alice kid recital"]
    _, d = await world.get("/api/v1/facts?vis=private", ta)
    assert [f["text"] for f in d["facts"]] == ["Alice likes tea"]
    _, d = await world.get("/api/v1/facts?kind=core", ta)
    assert d["count"] == 1
    _, d = await world.get("/api/v1/facts?order=desc&limit=1", ta)
    assert d["count"] == 1 and d["total"] == 2 and d["facts"][0]["text"] == "Alice kid recital"
    # bob's id list cannot reach alice's rows
    ids = ",".join(str(f["id"]) for f in (await world.get("/api/v1/facts", ta))[1]["facts"])
    _, d = await world.get(f"/api/v1/facts?ids={ids}", tb)
    assert d["facts"] == []


async def test_facts_bad_query_and_foreign_relationship(world, couple):
    ta, tc = couple["a"][1], couple["c"][1]
    assert (await world.get("/api/v1/facts?limit=abc", ta))[0] == 400
    assert (await world.get("/api/v1/facts?ids=x", ta))[0] == 400
    assert (await world.get(f"/api/v1/topics?relationship_id={couple['rid']}", tc))[0] == 404


async def test_endpoints_require_auth(world):
    for path in ("/api/v1/summaries", "/api/v1/topics", "/api/v1/dashboard/stats",
                 "/api/v1/relationships", "/api/v1/couple/x/facts",
                 "/api/v1/admin/stats", "/api/v1/admin/pipeline/1"):
        assert (await world.get(path))[0] == 401, path


# ── topics / stats / summaries / relationships ───────────────────────────

async def test_topics_stats_summaries(world, couple):
    (a, ta), (b, tb) = couple["a"], couple["b"]
    rid = couple["rid"]
    await _fact(world.db, a, "f1", tag="work", relationship_id=rid)
    await _fact(world.db, a, "f2", tag="work")
    await _fact(world.db, a, "f3", tag="kids", visibility="shared", relationship_id=rid)
    await _fact(world.db, b, "bob fact", tag="bobtopic")
    sid = await _fact(world.db, a, "Week recap", kind="weekly", tag="work",
                      metadata=json.dumps({"period": "2026-W46"}),
                      source_ids=json.dumps([1, 2]))
    await db_mod.log_message(world.db, a, "user", "hello")

    _, d = await world.get("/api/v1/topics", ta)
    topics = {t["topic"]: t["mention_count"] for t in d["topics"]}
    assert topics == {"work": 2, "kids": 1}  # summary not counted, bob's topic absent
    _, d = await world.get(f"/api/v1/topics?relationship_id={rid}", ta)
    assert {t["topic"]: t["mention_count"] for t in d["topics"]} == {"work": 1, "kids": 1}
    _, d = await world.get("/api/v1/topics?relationship_id=none", ta)
    assert {t["topic"] for t in d["topics"]} == {"work"}

    _, s = await world.get("/api/v1/dashboard/stats", ta)
    assert s["facts"] == 3 and s["topics"] == 2 and s["messages"] == 1
    assert s["summaries"] == 1 and s["shared_facts"] == 1 and s["relationships"] == 1
    _, s = await world.get("/api/v1/dashboard/stats", tb)
    assert s["facts"] == 1 and s["messages"] == 0

    _, d = await world.get("/api/v1/summaries?period=weekly&tag=work", ta)
    assert d["count"] == 1
    row = d["summaries"][0]
    assert row["id"] == sid and row["period_id"] == "2026-W46" and row["source_ids"] == [1, 2]
    assert (await world.get("/api/v1/summaries", tb))[1]["count"] == 0
    assert (await world.get("/api/v1/summaries?period=bogus", ta))[0] == 400

    _, d = await world.get("/api/v1/facts?include_summaries=0", ta)
    assert "Week recap" not in {f["text"] for f in d["facts"]}


async def test_relationships_list(world, couple):
    _, d = await world.get("/api/v1/relationships", couple["a"][1])
    assert d["count"] == 1
    r = d["relationships"][0]
    assert r["id"] == couple["rid"] and r["partner_name"] == "bob" and r["rel_type"] == "couple"
    assert (await world.get("/api/v1/relationships", couple["c"][1]))[1]["count"] == 0


# ── couple views ─────────────────────────────────────────────────────────

async def test_couple_views_member_only(world, couple):
    (a, ta), (b, tb), tc = couple["a"], couple["b"], couple["c"][1]
    rid = couple["rid"]
    await _fact(world.db, a, "shared one", visibility="shared", relationship_id=rid)
    await _fact(world.db, a, "private one", visibility="private", relationship_id=rid)
    await _fact(world.db, b, "nudge src", relationship_id=rid, nudge_target_id=a,
                nudge_type="appreciation", nudge_suggestion="Say thanks")
    with sqlite3.connect(world.db) as conn:
        conn.execute("INSERT INTO conflicts (couple_id, relationship_id, theme) VALUES (?,?,?)",
                     (rid, rid, "chores"))
        conn.execute("INSERT INTO conflicts (couple_id, relationship_id, theme, status) "
                     "VALUES (?,?,?,'resolved')", (rid, rid, "old"))
        conn.execute("INSERT INTO relay_queue (relationship_id, sender_id, receiver_id, draft, status)"
                     " VALUES (?,?,?,?,?)", (rid, a, b, "draft to bob", "awaiting_approval"))
        conn.execute("INSERT INTO relay_queue (relationship_id, sender_id, receiver_id, draft, status)"
                     " VALUES (?,?,?,?,?)", (rid, b, a, "hello alice", "sent"))
        conn.execute("INSERT INTO relay_queue (relationship_id, sender_id, receiver_id, draft, status)"
                     " VALUES (?,?,?,?,?)", (rid, b, a, "unsent bob draft", "awaiting_approval"))

    _, d = await world.get(f"/api/v1/couple/{rid}/facts", tb)
    assert [f["text"] for f in d["facts"]] == ["shared one"]  # never the private one
    _, d = await world.get(f"/api/v1/couple/{rid}/conflicts", ta)
    assert [c["theme"] for c in d["conflicts"]] == ["chores"]
    _, d = await world.get(f"/api/v1/couple/{rid}/nudges", ta)
    assert [n["nudge_suggestion"] for n in d["nudges"]] == ["Say thanks"]
    _, d = await world.get(f"/api/v1/couple/{rid}/nudges", tb)
    assert d["nudges"] == []
    _, d = await world.get(f"/api/v1/couple/{rid}/relays", ta)
    assert {(r["direction"], r["text"]) for r in d["relays"]} == {
        ("sent", "draft to bob"), ("received", "hello alice")}  # bob's unsent draft hidden

    for what in ("facts", "conflicts", "nudges", "relays"):
        assert (await world.get(f"/api/v1/couple/{rid}/{what}", tc))[0] == 404
    assert (await world.get(f"/api/v1/couple/{rid}/bogus", ta))[0] == 404


# ── admin ────────────────────────────────────────────────────────────────

async def test_admin_requires_token(world, couple):
    ta = couple["a"][1]
    assert (await world.get("/api/v1/admin/stats", ta))[0] == 403
    assert (await world.get("/api/v1/admin/stats", ta, {"x-admin-token": "wrong"}))[0] == 403
    assert (await world.get("/api/v1/admin/stats", None, {"x-admin-token": ADMIN}))[0] == 401
    status, d = await world.get("/api/v1/admin/stats", ta, {"x-admin-token": ADMIN})
    assert status == 200 and d["users"] == 3 and "facts" in d["tables"]


async def test_admin_disabled_without_env_token(world, couple, monkeypatch):
    monkeypatch.delenv("DASHBOARD_TOKEN")
    status, body = await world.get("/api/v1/admin/stats", couple["a"][1], {"x-admin-token": ""})
    assert status == 403 and body["error"] == "admin_disabled"


async def test_admin_users_and_pipeline(world, couple):
    a, ta = couple["a"]
    hdr = {"x-admin-token": ADMIN}
    mid = await db_mod.log_message(world.db, a, "user", "I love hiking")
    fid = await _fact(world.db, a, "Loves hiking", source_message_id=mid)
    await _fact(world.db, a, "Month recap", kind="monthly", source_ids=json.dumps([fid]))

    _, d = await world.get("/api/v1/admin/users", ta, hdr)
    assert d["count"] == 3
    assert "password_hash" not in d["users"][0]
    alice = [u for u in d["users"] if u["user_id"] == a][0]
    assert alice["facts"] == 2 and alice["messages"] == 1

    _, t = await world.get(f"/api/v1/admin/pipeline/{mid}", ta, hdr)
    assert t["message"]["content"] == "I love hiking"
    assert [f["text"] for f in t["facts"]] == ["Loves hiking"]
    assert [s["text"] for s in t["summaries"]] == ["Month recap"]
    assert (await world.get("/api/v1/admin/pipeline/99999", ta, hdr))[0] == 404
    assert (await world.get(f"/api/v1/admin/pipeline/{mid}", ta))[0] == 403


async def test_static_components_served(world):
    for name in ("chat", "dashboard", "settings", "couple", "admin"):
        resp = await world.api.handle(Request("GET", f"/components/{name}.js", {}, b"", "10.0.0.1"))
        assert resp.status == 200 and resp.content_type.startswith("text/javascript")
