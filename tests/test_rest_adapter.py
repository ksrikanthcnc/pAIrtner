"""RestAdapter over a real socket (ephemeral port), MockLLMClient behind it."""
from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

import pytest

from couples_bot.adapters.rest_adapter import RestAdapter
from couples_bot.config import Config
from couples_bot.handlers import BotService
from couples_bot.rest.ids import REST_ID_BASE
from tests.helpers import MockLLMClient


def _cfg(db: str, **kw) -> Config:
    base = dict(
        telegram_token="", db_path=db, cooldown_minutes=0,
        rest_host="127.0.0.1", rest_port=0, rest_registration="open",
        rest_long_poll_max_seconds=2, review_before_send=False,
        delivery_check_interval_seconds=1,
        backup_enabled=False, digest_enabled=False, checkin_enabled=False,
        nudges_enabled=False, appreciation_enabled=False,
    )
    base.update(kw)
    return Config(**base)


def _http(base, method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            return resp.status, dict(resp.headers), raw
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


class Http:
    def __init__(self, base):
        self.base = base

    async def call(self, method, path, body=None, token=None):
        status, headers, raw = await asyncio.to_thread(
            _http, self.base, method, path, body, token)
        try:
            return status, json.loads(raw), headers
        except json.JSONDecodeError:
            return status, raw, headers


@pytest.fixture
async def running(test_db):
    llm = MockLLMClient()

    async def boot(**kw):
        cfg = _cfg(test_db, **kw)
        svc = BotService(cfg, test_db, llm)
        adapter = RestAdapter(svc, cfg)
        task = asyncio.create_task(adapter.start())
        await asyncio.wait_for(adapter.ready.wait(), 5)
        return svc, adapter, task, Http(f"http://127.0.0.1:{adapter.bound_port}")

    started = []

    async def factory(**kw):
        item = await boot(**kw)
        started.append(item)
        return item + (llm,)

    yield factory
    for _, adapter, task, _ in started:
        await adapter.stop()
        await asyncio.wait_for(task, 5)


async def test_register_pair_chat_and_relay_over_http(running, test_db):
    svc, adapter, task, http, llm = await running()
    status, body, headers = await http.call("GET", "/healthz")
    assert status == 200 and body == b"ok"
    assert "default-src 'self'" in headers["Content-Security-Policy"]

    _, a, _ = await http.call("POST", "/api/v1/register", {"name": "Alice"})
    _, b, _ = await http.call("POST", "/api/v1/register", {"name": "Bob"})
    assert a["user_id"] >= REST_ID_BASE and b["user_id"] == a["user_id"] + 1

    await http.call("POST", "/api/v1/message", {"text": "/start"}, a["token"])
    code = next(iter(svc._pairing_codes))
    status, out, _ = await http.call(
        "POST", "/api/v1/message", {"text": f"/join {code}"}, b["token"])
    assert status == 200
    for t in (a, b):
        await http.call("POST", "/api/v1/message", {"text": "/skip"}, t["token"])

    llm.responses.append({"reply": "ok", "new_facts": [],
                          "relay_draft": "SOCKETRELAY hi Bob"})
    _, out, _ = await http.call(
        "POST", "/api/v1/message", {"text": "say hi #send"}, a["token"])
    data = out["replies"][-1]["buttons"][0][0]["data"]
    await http.call("POST", "/api/v1/callback", {"data": data}, a["token"])

    # the real delivery loop (started by the adapter) puts it in Bob's outbox
    seen = []
    cursor = 0
    for _ in range(6):
        _, ev, _ = await http.call(
            "GET", f"/api/v1/events?after={cursor}&wait=2", token=b["token"])
        cursor = ev["cursor"]
        seen += [e["msg"]["text"] for e in ev["events"]]
        if any("SOCKETRELAY" in t for t in seen):
            break
    assert any("SOCKETRELAY" in t for t in seen)


async def test_send_message_from_background_lands_in_outbox(running):
    svc, adapter, task, http, llm = await running()
    _, a, _ = await http.call("POST", "/api/v1/register", {"name": "Alice"})
    poll = asyncio.create_task(
        http.call("GET", "/api/v1/events?wait=2", token=a["token"]))
    await asyncio.sleep(0.2)
    await adapter.send_message(a["user_id"], "<b>digest</b>", buttons=[[("Go", "x:1")]])
    status, ev, _ = await asyncio.wait_for(poll, 5)
    msg = ev["events"][0]["msg"]
    assert msg["text"] == "digest" and msg["markup"] == "<b>digest</b>"
    assert msg["buttons"] == [[{"text": "Go", "data": "x:1"}]]


async def test_http_errors_and_headers_over_socket(running):
    svc, adapter, task, http, llm = await running()
    assert (await http.call("GET", "/api/v1/me"))[0] == 401
    assert (await http.call("GET", "/nope"))[0] == 404
    assert (await http.call("PUT", "/api/v1/me"))[0] == 405
    status, body, _ = await http.call("POST", "/api/v1/register", None)
    assert status in (400, 415)
    big = {"text": "x" * 40000}
    _, a, _ = await http.call("POST", "/api/v1/register", {"name": "A"})
    status, body, _ = await http.call("POST", "/api/v1/message", big, a["token"])
    assert status == 413


async def test_stop_closes_the_port(running):
    svc, adapter, task, http, llm = await running()
    assert (await http.call("GET", "/healthz"))[0] == 200
    await adapter.stop()
    await asyncio.wait_for(task, 5)
    with pytest.raises(urllib.error.URLError):
        await asyncio.to_thread(_http, http.base, "GET", "/healthz")


def test_adapter_metadata(test_db):
    cfg = _cfg(test_db)
    adapter = RestAdapter(BotService(cfg, test_db, MockLLMClient()), cfg)
    assert "HTTP" in adapter.transport_description
    assert "Telegram" not in adapter.transport_description
    assert adapter.owns_user(REST_ID_BASE) and not adapter.owns_user(REST_ID_BASE - 1)


async def test_privacy_notice_uses_rest_wording(test_db):
    from couples_bot.__main__ import _set_privacy_info
    cfg = _cfg(test_db, llm_provider="openai_compat",
               llm_base_url="http://127.0.0.1:11434/v1", llm_api_key="k")
    svc = BotService(cfg, test_db, MockLLMClient())
    adapter = RestAdapter(svc, cfg)
    _set_privacy_info(svc, cfg, [adapter])
    assert "Telegram" not in svc._disclaimer("en")
    assert "Gemini" not in svc._disclaimer("en")
