"""Password auth: set/verify, /password command, POST /api/v1/login."""
from __future__ import annotations

import pytest

from pairtner import db as db_mod
from pairtner.config import Config
from pairtner.handlers import BotService
from pairtner.rest.api import RestApi
from tests.helpers import MockLLMClient
from tests.test_rest_api import Client, make_cfg

TG_ID = 111


@pytest.fixture
async def env(test_db):
    cfg = make_cfg(test_db)
    svc = BotService(cfg, test_db, MockLLMClient())
    api = RestApi(svc, cfg, web_dir=None)
    await db_mod.create_user(test_db, TG_ID, "Alice")
    return svc, Client(api), test_db


async def test_set_and_verify_password(test_db):
    await db_mod.create_user(test_db, TG_ID, "Alice")
    assert not await db_mod.verify_password(test_db, TG_ID, "x")
    await db_mod.set_password(test_db, TG_ID, "s3cret")
    assert await db_mod.verify_password(test_db, TG_ID, "s3cret")
    assert not await db_mod.verify_password(test_db, TG_ID, "wrong")
    user = await db_mod.get_user(test_db, TG_ID)
    assert user["password_hash"] and "s3cret" not in user["password_hash"]


async def test_verify_unknown_user(test_db):
    assert not await db_mod.verify_password(test_db, 999, "x")


async def test_password_command(env):
    svc, _, db = env
    out = await svc.dispatch(TG_ID, "Alice", "/password")
    assert out[0].text == "Usage: /password <your-password>"
    out = await svc.dispatch(TG_ID, "Alice", "/password hunter2")
    assert out[0].text == "Password set! You can now log in at the companion app."
    assert await db_mod.verify_password(db, TG_ID, "hunter2")


async def test_login_by_user_id(env):
    svc, client, db = env
    await db_mod.set_password(db, TG_ID, "pw1")
    status, body, _ = await client.call(
        "POST", "/api/v1/login", {"user_id": TG_ID, "password": "pw1"})
    assert status == 200 and body["user_id"] == TG_ID
    status, me, _ = await client.call("GET", "/api/v1/me", token=body["token"])
    assert status == 200 and me["name"] == "Alice"


async def test_login_by_name(env):
    _, client, db = env
    await db_mod.set_password(db, TG_ID, "pw1")
    status, body, _ = await client.call(
        "POST", "/api/v1/login", {"name": "alice", "password": "pw1"})
    assert status == 200 and body["user_id"] == TG_ID and body["token"]


async def test_login_wrong_password(env):
    _, client, db = env
    await db_mod.set_password(db, TG_ID, "pw1")
    for payload in ({"user_id": TG_ID, "password": "nope"},
                    {"name": "Alice", "password": "nope"},
                    {"name": "Nobody", "password": "pw1"}):
        status, body, _ = await client.call("POST", "/api/v1/login", payload)
        assert status == 401, payload


async def test_login_without_password_set(env):
    _, client, _ = env
    status, _, _ = await client.call(
        "POST", "/api/v1/login", {"user_id": TG_ID, "password": "anything"})
    assert status == 401


async def test_login_bad_body(env):
    _, client, _ = env
    for payload in ({"user_id": TG_ID}, {"password": "x"},
                    {"user_id": True, "password": "x"}):
        status, _, _ = await client.call("POST", "/api/v1/login", payload)
        assert status == 400, payload
