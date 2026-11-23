"""Tests for the duo sandbox endpoints and the admin dry-run endpoint."""
from __future__ import annotations

import json
import os

import pytest

from pairtner import db as db_mod
from pairtner.config import Config
from pairtner.handlers import BotService
from pairtner.rest import api as api_mod
from pairtner.rest.api import Request, RestApi
from tests.helpers import MockLLMClient

ADMIN = "admin-secret-for-tests"


@pytest.fixture
async def env(test_db, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", ADMIN)
    llm = MockLLMClient()
    cfg = Config(telegram_token="", db_path=test_db, rest_registration="open",
                 rest_long_poll_max_seconds=1)
    api = RestApi(BotService(cfg, test_db, llm), cfg)
    created: list[str] = []
    yield api, llm, test_db, created
    for sid in list(api._sandboxes):
        api._drop_sandbox(sid)


async def call(api, method, target, token=None, body=None, headers=None):
    h = dict(headers or {})
    if token:
        h["authorization"] = f"Bearer {token}"
    data = b""
    if body is not None:
        h["content-type"] = "application/json"
        data = json.dumps(body).encode()
    resp = await api.handle(Request(method, target, h, data, "10.0.0.5"))
    return resp.status, json.loads(resp.body)


async def test_sandbox_create_is_isolated_and_unpaired(env):
    """Sandbox creates users but does NOT pre-pair — /start + /join flow."""
    api, llm, main_db, _ = env
    status, sb = await call(api, "POST", "/api/v1/sandbox/create")
    assert status == 201
    sid = sb["sandbox_id"]
    path = f"/tmp/pairtner-sandbox-{sid}.db"
    assert os.path.exists(path)

    status, me = await call(api, "GET", f"/api/v1/me?sandbox={sid}", sb["alice_token"])
    assert status == 200 and me["name"] == "Alice"
    # Users are NOT pre-paired
    assert me.get("partner_name") is None
    status, me = await call(api, "GET", f"/api/v1/me?sandbox={sid}", sb["bob_token"])
    assert me["name"] == "Bob" and me.get("partner_name") is None
    # token is not valid on the main DB; main DB has no users
    assert (await call(api, "GET", "/api/v1/me", sb["alice_token"]))[0] == 401
    assert await db_mod.get_user(main_db, 1) is None

    # messages land in the sandbox DB only
    status, _ = await call(api, "POST", f"/api/v1/message?sandbox={sid}",
                           sb["alice_token"], {"text": "hello"})
    assert status == 200
    assert await db_mod.get_recent_messages(main_db, 1, limit=10) == []
    assert len(await db_mod.get_recent_messages(path, 1, limit=10)) >= 1


async def test_sandbox_delete_and_unknown(env):
    api, *_ = env
    _, sb = await call(api, "POST", "/api/v1/sandbox/create")
    sid = sb["sandbox_id"]
    path = f"/tmp/pairtner-sandbox-{sid}.db"
    assert (await call(api, "DELETE", f"/api/v1/sandbox/{sid}"))[0] == 200
    assert not os.path.exists(path)
    assert (await call(api, "DELETE", f"/api/v1/sandbox/{sid}"))[0] == 404
    status, body = await call(api, "GET", f"/api/v1/me?sandbox={sid}", sb["alice_token"])
    assert status == 404 and body["error"] == "sandbox_not_found"


async def test_sandbox_no_auto_expiry(env, monkeypatch):
    """With TTL=0 sandboxes persist until explicit delete or server restart."""
    api, *_ = env
    _, sb = await call(api, "POST", "/api/v1/sandbox/create")
    sid = sb["sandbox_id"]
    # Even with old creation time, sandbox should still be accessible
    api._sandboxes[sid].created -= 999999
    status, _ = await call(api, "GET", f"/api/v1/me?sandbox={sid}", sb["alice_token"])
    assert status == 200  # still alive, no expiry


async def test_duo_page_is_served(env):
    api, *_ = env
    resp = await api.handle(Request("GET", "/duo.html", {}, b"", "10.0.0.5"))
    assert resp.status == 200
    resp = await api.handle(Request("GET", "/duo.js", {}, b"", "10.0.0.5"))
    assert resp.status == 200


async def _user(api):
    status, body = await call(api, "POST", "/api/v1/register", body={"name": "carol"})
    assert status == 201
    return body["user_id"], body["token"]


async def test_dryrun_requires_admin(env):
    api, *_ = env
    _, tok = await _user(api)
    body = {"text": "hi"}
    assert (await call(api, "POST", "/api/v1/admin/dryrun", tok, body))[0] == 403
    assert (await call(api, "POST", "/api/v1/admin/dryrun", tok, body,
                       {"x-admin-token": "wrong"}))[0] == 403
    assert (await call(api, "POST", "/api/v1/admin/dryrun", None, body,
                       {"x-admin-token": ADMIN}))[0] == 401


async def test_dryrun_returns_prompts_and_saves_nothing(env):
    api, llm, db, _ = env
    uid, tok = await _user(api)
    llm.responses.append({
        "reply": "Sounds rough.",
        "memory_ops": [{"op": "add", "kind": "working", "text": "Had a tough day",
                        "tag": "work", "visibility": "private"}],
        "new_facts": [], "relay_draft": None,
    })
    hdr = {"x-admin-token": ADMIN}
    status, d = await call(api, "POST", "/api/v1/admin/dryrun", tok,
                           {"text": "Tough day at work", "user_id": uid}, hdr)
    assert status == 200, d
    assert d["system_prompt"] and "Tough day at work" in d["user_content"]
    assert d["llm_response"]["reply"] == "Sounds rough."
    assert d["parsed"]["reply"] == "Sounds rough."
    assert [o["text"] for o in d["would_save"]] == ["Had a tough day"]
    assert await db_mod.get_active_facts(db, uid) == []
    assert await db_mod.get_recent_messages(db, uid, limit=10) == []


async def test_dryrun_without_user_and_validation(env):
    api, llm, *_ = env
    _, tok = await _user(api)
    hdr = {"x-admin-token": ADMIN}
    status, d = await call(api, "POST", "/api/v1/admin/dryrun", tok, {"text": "hi"}, hdr)
    assert status == 200 and d["would_save"] == []
    assert (await call(api, "POST", "/api/v1/admin/dryrun", tok, {"text": " "}, hdr))[0] == 400
    assert (await call(api, "POST", "/api/v1/admin/dryrun", tok,
                       {"text": "x", "user_id": "a"}, hdr))[0] == 400
    assert (await call(api, "POST", "/api/v1/admin/dryrun", tok,
                       {"text": "x", "user_id": 987654}, hdr))[0] == 404
