"""Tests for PUT/DELETE /api/v1/facts/:id (PWA Facts panel)."""
from __future__ import annotations

import json

import pytest

from pairtner.config import Config
from pairtner.handlers import BotService
from pairtner.rest.api import Request, RestApi
from pairtner.storage import facts as facts_mod
from tests.helpers import MockLLMClient


class World:
    def __init__(self, api: RestApi, db: str) -> None:
        self.api = api
        self.db = db

    async def call(self, method, path, token, body=None):
        h = {"authorization": f"Bearer {token}"}
        raw = b""
        if body is not None:
            h["content-type"] = "application/json"
            raw = json.dumps(body).encode()
        resp = await self.api.handle(Request(method, path, h, raw, "10.0.0.9"))
        return resp.status, json.loads(resp.body)

    async def register(self, name):
        resp = await self.api.handle(Request(
            "POST", "/api/v1/register", {"content-type": "application/json"},
            json.dumps({"name": name}).encode(), "10.0.0.1"))
        body = json.loads(resp.body)
        return body["user_id"], body["token"]


@pytest.fixture
async def world(test_db):
    cfg = Config(telegram_token="", db_path=test_db, rest_registration="open",
                 rest_long_poll_max_seconds=1)
    return World(RestApi(BotService(cfg, test_db, MockLLMClient()), cfg), test_db)


async def _fact(world, uid, text="Likes tea", kind="working"):
    fid = await facts_mod.add_fact(world.db, uid, text, kind=kind)
    return fid


async def test_list_exposes_freshness_fields(world):
    uid, tok = await world.register("alice")
    await _fact(world, uid)
    status, data = await world.call("GET", "/api/v1/facts", tok)
    assert status == 200
    f = data["facts"][0]
    assert "citation_count" in f and "last_cited_at" in f and f["kind"] == "working"


async def test_promote_to_core(world):
    uid, tok = await world.register("alice")
    fid = await _fact(world, uid)
    status, data = await world.call("PUT", f"/api/v1/facts/{fid}", tok, {"kind": "core"})
    assert status == 200 and data["fact"]["kind"] == "core"
    row = await facts_mod.get_fact_by_id(world.db, fid)
    assert row["kind"] == "core"


async def test_update_rejects_other_kind(world):
    uid, tok = await world.register("alice")
    fid = await _fact(world, uid)
    status, data = await world.call("PUT", f"/api/v1/facts/{fid}", tok, {"kind": "episode"})
    assert status == 400 and data["error"] == "invalid_kind"


async def test_delete_fact(world):
    uid, tok = await world.register("alice")
    fid = await _fact(world, uid)
    status, data = await world.call("DELETE", f"/api/v1/facts/{fid}", tok)
    assert status == 200 and data["deleted"] == fid
    assert await facts_mod.get_fact_by_id(world.db, fid) is None


async def test_other_users_fact_is_404(world):
    uid, _ = await world.register("alice")
    _, tok_b = await world.register("bob")
    fid = await _fact(world, uid)
    assert (await world.call("DELETE", f"/api/v1/facts/{fid}", tok_b))[0] == 404
    assert (await world.call("PUT", f"/api/v1/facts/{fid}", tok_b, {"kind": "core"}))[0] == 404
    assert await facts_mod.get_fact_by_id(world.db, fid) is not None


async def test_missing_fact_and_auth(world):
    _, tok = await world.register("alice")
    assert (await world.call("DELETE", "/api/v1/facts/99999", tok))[0] == 404
    resp = await world.api.handle(Request("DELETE", "/api/v1/facts/1", {}, b"", "10.0.0.9"))
    assert resp.status == 401
