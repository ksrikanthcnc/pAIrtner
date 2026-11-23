"""User model preference (/model, REST) and nudge frequency controls."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from pairtner.config import Config
from pairtner.core.memory import SETTINGS_META_PREFIX
from pairtner.features.nudge_prefs import apply_nudge_request, parse_nudge_request
from pairtner.features.nudges import _may_nudge
from pairtner.handlers import BotService
from pairtner.rest.api import Request, RestApi
from pairtner.storage import settings as settings_mod
from tests.helpers import MockLLMClient


# ── /model ──────────────────────────────────────────────────────────────

async def test_model_show_set_reset(service, paired_users):
    a, _ = paired_users
    r = await service.handle_model(a, None)
    assert "server default" in r[0].text
    r = await service.handle_model(a, "gemma3:4b")
    assert "gemma3:4b" in r[0].text
    assert await service._get_user_model(a) == "gemma3:4b"
    r = await service.handle_model(a, None)
    assert "(yours)" in r[0].text
    await service.handle_model(a, "reset")
    assert await service._get_user_model(a) is None


async def test_user_model_passed_to_generate(service, paired_users, mock_llm):
    a, _ = paired_users
    await service.handle_model(a, "my-model")
    await service.handle_message(a, "Alice", "hello there")
    assert mock_llm.calls and mock_llm.calls[-1]["model"] == "my-model"


async def test_no_model_pref_uses_default(service, paired_users, mock_llm):
    a, _ = paired_users
    await service.handle_message(a, "Alice", "hello there")
    assert mock_llm.calls[-1]["model"] is None


async def test_settings_shows_model_and_nudges(service, test_db, paired_users):
    a, _ = paired_users
    await service.handle_model(a, "my-model")
    await settings_mod.set_user_preference(
        test_db, a, SETTINGS_META_PREFIX + "nudge_frequency", "weekly")
    text = (await service.handle_settings(a))[0].text
    assert "model: my-model" in text
    assert "nudge_frequency: weekly" in text


# ── REST ────────────────────────────────────────────────────────────────

async def test_rest_model_roundtrip(test_db):
    cfg = Config(telegram_token="", db_path=test_db, rest_registration="open",
                 rest_long_poll_max_seconds=1)
    api = RestApi(BotService(cfg, test_db, MockLLMClient()), cfg)

    async def call(method, path, tok=None, body=None):
        h = {"authorization": f"Bearer {tok}"} if tok else {}
        raw = b""
        if body is not None:
            h["content-type"] = "application/json"
            raw = json.dumps(body).encode()
        resp = await api.handle(Request(method, path, h, raw, "10.0.0.9"))
        return resp.status, json.loads(resp.body)

    _, reg = await call("POST", "/api/v1/register", body={"name": "al"})
    tok = reg["token"]
    st, data = await call("GET", "/api/v1/settings/model", tok)
    assert st == 200 and data["custom"] is False
    st, data = await call("PUT", "/api/v1/settings/model", tok, {"model": "x-1"})
    assert st == 200 and data == {"model": "x-1", "custom": True, "providers": []}
    st, _ = await call("PUT", "/api/v1/settings/model", tok, {"model": "bad name"})
    assert st == 400
    st, data = await call("PUT", "/api/v1/settings/model", tok, {"model": "reset"})
    assert data["custom"] is False


# ── nudge prefs ─────────────────────────────────────────────────────────

def test_parse_nudge_requests():
    assert parse_nudge_request("nudge me less") == [("nudge_frequency", "weekly")]
    assert parse_nudge_request("no nudges on weekends") == [("nudge_skip_weekends", "true")]
    assert parse_nudge_request("please stop nudging") == [("nudge_frequency", "off")]
    assert parse_nudge_request("I had a nice day") == []


async def test_nl_sets_pref_directly(service, test_db, paired_users):
    a, _ = paired_users
    await service.handle_message(a, "Alice", "no nudges on weekends")
    val = await settings_mod.get_user_preference(
        test_db, a, SETTINGS_META_PREFIX + "nudge_skip_weekends")
    assert val == "true"


class _Cfg:
    nudges_interval_hours = 24
    nudges_quiet_start = "00:00"
    nudges_quiet_end = "23:59"
    checkin_day = "sunday"


async def test_may_nudge_off_weekly_weekend(test_db, paired_users):
    a, _ = paired_users
    sunday = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    monday = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
    saturday = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
    assert await _may_nudge(test_db, a, _Cfg(), monday)

    await apply_nudge_request(test_db, a, "nudge me less")  # weekly
    assert not await _may_nudge(test_db, a, _Cfg(), monday)
    assert await _may_nudge(test_db, a, _Cfg(), sunday)

    await apply_nudge_request(test_db, a, "stop nudging me")  # off
    assert not await _may_nudge(test_db, a, _Cfg(), sunday)

    await settings_mod.set_user_preference(
        test_db, a, SETTINGS_META_PREFIX + "nudge_frequency", "daily")
    await apply_nudge_request(test_db, a, "no nudges on weekends")
    assert not await _may_nudge(test_db, a, _Cfg(), saturday)
    assert await _may_nudge(test_db, a, _Cfg(), monday)
