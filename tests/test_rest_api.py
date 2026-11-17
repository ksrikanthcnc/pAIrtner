"""Tests for pairtner.rest.api (pure handler, no sockets)."""
from __future__ import annotations

import asyncio
import json

import pytest

from pairtner.approval import delivery_loop
from pairtner.config import Config
from pairtner.db import add_outbox_event, create_user, get_outbox_events, init_db
from pairtner.handlers import BotService
from pairtner.rest import api as api_mod
from pairtner.rest.api import ROUTES, MAX_BODY_BYTES, Request, RestApi, text_to_msg
from pairtner.rest.ids import REST_ID_BASE
from tests.helpers import MockLLMClient

INVITE = "invite-code-for-tests"


def make_cfg(db: str, **kw) -> Config:
    base = dict(
        telegram_token="", db_path=db, cooldown_minutes=0,
        rest_registration="open", review_before_send=False,
        rest_long_poll_max_seconds=1,
    )
    base.update(kw)
    return Config(**base)


class Client:
    def __init__(self, api: RestApi, ip: str = "10.0.0.1") -> None:
        self.api = api
        self.ip = ip

    async def call(self, method, path, body=None, token=None, *, raw=None,
                   headers=None, ip=None):
        h = {"content-type": "application/json"}
        if token:
            h["authorization"] = f"Bearer {token}"
        h.update(headers or {})
        data = raw if raw is not None else (
            json.dumps(body).encode() if body is not None else b"")
        resp = await self.api.handle(Request(method, path, h, data, ip or self.ip))
        try:
            payload = json.loads(resp.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = resp.body
        return resp.status, payload, resp

    async def register(self, name, **extra):
        status, body, _ = await self.call(
            "POST", "/api/v1/register", {"name": name, **extra})
        assert status == 201, body
        return body


@pytest.fixture
def llm():
    return MockLLMClient()


@pytest.fixture
async def env(test_db, llm, monkeypatch):
    monkeypatch.setenv("REST_INVITE_CODE", INVITE)
    cfg = make_cfg(test_db)
    svc = BotService(cfg, test_db, llm)
    api = RestApi(svc, cfg, web_dir=None)
    return svc, api, Client(api), cfg


def _mk(test_db, llm, **kw):
    cfg = make_cfg(test_db, **kw)
    svc = BotService(cfg, test_db, llm)
    api = RestApi(svc, cfg)
    return svc, api, Client(api), cfg


# ── route table ──────────────────────────────────────────────────────────

PROTECTED = [r for r in ROUTES if not r.public]
PUBLIC_EXPECTED = {
    "/healthz", "/api/v1/meta", "/api/v1/register",
}


def test_route_table_decisions_are_explicit():
    """A new route must be added here on purpose (and given a public flag)."""
    api_routes = {(r.method, r.path): r.public for r in ROUTES
                  if r.path.startswith("/api/") or r.path == "/healthz"}
    assert api_routes == {
        ("GET", "/healthz"): True,
        ("GET", "/api/v1/meta"): True,
        ("POST", "/api/v1/register"): True,
        ("GET", "/api/v1/me"): False,
        ("POST", "/api/v1/message"): False,
        ("POST", "/api/v1/callback"): False,
        ("GET", "/api/v1/events"): False,
        ("GET", "/api/v1/history"): False,
        ("GET", "/api/v1/facts"): False,
        ("DELETE", "/api/v1/message"): False,
        ("POST", "/api/v1/token/rotate"): False,
    }
    static = [r for r in ROUTES if r.handler == "_static"]
    assert static and all(r.public and r.method == "GET" for r in static)
    assert {r.handler for r in ROUTES if r.public and r.handler != "_static"} == {
        "_healthz", "_meta", "_register"}


@pytest.mark.parametrize("route", PROTECTED, ids=lambda r: f"{r.method} {r.path}")
async def test_protected_routes_reject_bad_credentials(env, route):
    svc, api, c, cfg = env
    alice = await c.register("Alice")
    bob = await c.register("Bob")
    secret_a = alice["token"].split(":", 1)[1]
    # rotated-away token
    old = bob["token"]
    status, rotated, _ = await c.call("POST", "/api/v1/token/rotate", token=old)
    assert status == 200 and rotated["token"] != old
    bad_tokens = [
        None,
        "garbage",
        "1:2:3",
        f"{alice['user_id']}:wrong-secret",
        f"{bob['user_id']}:{secret_a}",       # another user's secret
        f"{alice['user_id']}:",
        f":{secret_a}",
        f"abc:{secret_a}",
        f"5:{secret_a}",                      # below the REST id range
        old,                                  # rotated away
    ]
    body = {"text": "hi", "data": "x"} if route.method == "POST" else None
    for tok in bad_tokens:
        status, payload, resp = await c.call(
            route.method, route.path, body, tok, ip=f"9.9.9.{abs(hash(str(tok))) % 200}")
        assert status == 401, (tok, payload)
        assert payload == {"error": "unauthorized"}
        assert ("WWW-Authenticate", "Bearer") in resp.headers
    # Non-bearer scheme
    status, payload, _ = await c.call(
        route.method, route.path, body, None,
        headers={"authorization": f"Basic {alice['token']}"}, ip="9.9.9.250")
    assert status == 401


async def test_auth_failures_are_throttled(env):
    _, _, c, _ = env
    for _ in range(30):
        status, _, _ = await c.call("GET", "/api/v1/me", token="1:x", ip="7.7.7.7")
        assert status == 401
    status, payload, _ = await c.call("GET", "/api/v1/me", token="1:x", ip="7.7.7.7")
    assert status == 429
    # a different peer is unaffected
    status, _, _ = await c.call("GET", "/api/v1/me", token="1:x", ip="7.7.7.8")
    assert status == 401


async def test_not_allowed_user_gets_403(test_db, llm):
    uid = REST_ID_BASE + 5
    svc, api, c, cfg = _mk(test_db, llm, allowed_user_ids=frozenset({uid}))
    token = (await c.register("Ann", user_id=uid))["token"]
    object.__setattr__(cfg, "allowed_user_ids", frozenset({uid + 1}))
    status, payload, _ = await c.call("GET", "/api/v1/me", token=token)
    assert (status, payload) == (403, {"error": "not_allowed"})


# ── registration ─────────────────────────────────────────────────────────

async def test_register_closed(test_db, llm):
    _, _, c, _ = _mk(test_db, llm, rest_registration="closed")
    status, payload, _ = await c.call("POST", "/api/v1/register", {"name": "A"})
    assert (status, payload["error"]) == (403, "registration_closed")


async def test_register_invite_requires_correct_code(test_db, llm, monkeypatch):
    monkeypatch.setenv("REST_INVITE_CODE", INVITE)
    _, _, c, _ = _mk(test_db, llm, rest_registration="invite")
    status, p, _ = await c.call("POST", "/api/v1/register", {"name": "A"})
    assert (status, p["error"]) == (403, "invalid_invite")
    status, p, _ = await c.call("POST", "/api/v1/register",
                                {"name": "A", "invite": "nope"})
    assert (status, p["error"]) == (403, "invalid_invite")
    status, p, _ = await c.call("POST", "/api/v1/register",
                                {"name": "A", "invite": 12345})
    assert status == 403
    status, p, _ = await c.call("POST", "/api/v1/register",
                                {"name": "A", "invite": INVITE})
    assert status == 201 and p["user_id"] >= REST_ID_BASE


async def test_register_invite_mode_without_env_code_is_403(test_db, llm, monkeypatch):
    monkeypatch.delenv("REST_INVITE_CODE", raising=False)
    _, _, c, _ = _mk(test_db, llm, rest_registration="invite")
    for invite in ("", None, "anything"):
        body = {"name": "A"}
        if invite is not None:
            body["invite"] = invite
        status, p, _ = await c.call("POST", "/api/v1/register", body)
        assert (status, p["error"]) == (403, "registration_unavailable")


async def test_register_open_ignores_invite_and_user_id(test_db, llm):
    _, _, c, _ = _mk(test_db, llm)
    p = await c.register("A", user_id=7, invite="x")
    assert p["user_id"] == REST_ID_BASE


async def test_ids_are_sequential_from_base_and_never_collide_with_telegram(test_db, llm):
    _, _, c, _ = _mk(test_db, llm)
    await create_user(test_db, 123456789, "TG")
    a = await c.register("A")
    b = await c.register("B")
    assert (a["user_id"], b["user_id"]) == (REST_ID_BASE, REST_ID_BASE + 1)
    assert b["user_id"] < 2**53
    assert a["token"].startswith(f"{a['user_id']}:")


async def test_concurrent_registrations_get_distinct_ids(test_db, llm):
    _, _, c, _ = _mk(test_db, llm)
    c.api.register_limiter.limit = 100
    results = await asyncio.gather(*(c.register(f"U{i}") for i in range(8)))
    ids = {r["user_id"] for r in results}
    assert len(ids) == 8


async def test_allowlist_requires_explicit_allowed_rest_id(test_db, llm):
    allowed = REST_ID_BASE + 10
    _, _, c, _ = _mk(test_db, llm, allowed_user_ids=frozenset({allowed, 5}))
    for body in (
        {"name": "A"},                                   # no id
        {"name": "A", "user_id": REST_ID_BASE + 11},     # not in list
        {"name": "A", "user_id": 5},                     # in list but below range
        {"name": "A", "user_id": True},
        {"name": "A", "user_id": str(allowed)},
    ):
        status, p, _ = await c.call("POST", "/api/v1/register", body)
        assert (status, p["error"]) == (403, "not_allowed"), body
    p = await c.register("A", user_id=allowed)
    assert p["user_id"] == allowed
    # registering the same allowlisted id again must not hand out a second token
    status, p, _ = await c.call("POST", "/api/v1/register",
                                {"name": "Evil", "user_id": allowed})
    assert (status, p["error"]) == (409, "user_exists")


@pytest.mark.parametrize("raw,expected", [
    ("  Alice  ", "Alice"),
    ("<b>Al&ice</b>", "bAlice/b"),
    ("Al\x00i\x07ce", "Al i ce"),
    ("x" * 100, "x" * 40),
    ("Tom\n\tJones", "Tom Jones"),
    ("నమస్తే", "నమస్తే"),
])
async def test_name_sanitising(test_db, llm, raw, expected):
    _, _, c, _ = _mk(test_db, llm)
    p = await c.register(raw)
    assert p["name"] == expected
    status, me, _ = await c.call("GET", "/api/v1/me", token=p["token"])
    assert me["name"] == expected


@pytest.mark.parametrize("name", ["", "   ", "<>&", None, 5, ["a"], "\x01\x02"])
async def test_invalid_names_rejected(test_db, llm, name):
    _, _, c, _ = _mk(test_db, llm)
    status, p, _ = await c.call("POST", "/api/v1/register", {"name": name})
    assert (status, p["error"]) == (400, "invalid_name")


async def test_invalid_lang_rejected_and_te_accepted(test_db, llm):
    _, _, c, _ = _mk(test_db, llm)
    status, p, _ = await c.call("POST", "/api/v1/register",
                                {"name": "A", "lang": "fr"})
    assert (status, p["error"]) == (400, "invalid_lang")
    p = await c.register("A", lang="te")
    _, me, _ = await c.call("GET", "/api/v1/me", token=p["token"])
    assert me["lang"] == "te"


async def test_register_is_throttled_per_ip(test_db, llm):
    _, _, c, _ = _mk(test_db, llm)
    for _ in range(10):
        await c.register("A")
    status, p, _ = await c.call("POST", "/api/v1/register", {"name": "A"})
    assert status == 429
    status, _, _ = await c.call("POST", "/api/v1/register", {"name": "A"}, ip="8.8.8.8")
    assert status == 201


# ── public routes ────────────────────────────────────────────────────────

async def test_healthz_and_meta(env):
    svc, api, c, cfg = env
    status, body, resp = await c.call("GET", "/healthz")
    assert (status, body) == (200, b"ok")
    status, meta, _ = await c.call("GET", "/api/v1/meta")
    assert status == 200
    assert meta["registration"] == "open" and meta["transport"] == "rest"
    assert meta["languages"] == ["en", "te"]
    assert "Gemini" in meta["llm"] and meta["privacy"]
    assert INVITE not in json.dumps(meta)


async def test_security_headers_on_every_response(env, tmp_path):
    svc, api, c, cfg = env
    (tmp_path / "index.html").write_text("<html></html>")
    api.web_dir = tmp_path
    paths = [("GET", "/healthz"), ("GET", "/api/v1/meta"), ("GET", "/api/v1/me"),
             ("GET", "/nope"), ("DELETE", "/api/v1/me"), ("GET", "/")]
    for method, path in paths:
        _, _, resp = await c.call(method, path)
        h = dict(resp.headers)
        assert "default-src 'self'" in h["Content-Security-Policy"]
        assert "script-src 'self'" in h["Content-Security-Policy"]
        assert h["X-Content-Type-Options"] == "nosniff"
        assert h["Referrer-Policy"] == "no-referrer"
        assert not any(k.lower().startswith("access-control") for k in h)
        if path.startswith("/api/"):
            assert h["Cache-Control"] == "no-store"


async def test_unknown_path_404_and_wrong_method_405(env):
    _, _, c, _ = env
    assert (await c.call("GET", "/api/v1/nothing"))[0] == 404
    assert (await c.call("GET", "/api/v1/message"))[0] == 405
    assert (await c.call("DELETE", "/api/v1/me"))[0] == 405


# ── static files ─────────────────────────────────────────────────────────

async def test_static_serving_content_types(env, tmp_path):
    _, api, c, _ = env
    (tmp_path / "icons").mkdir()
    (tmp_path / "index.html").write_text("<html>hi</html>")
    (tmp_path / "app.js").write_text("//js")
    (tmp_path / "manifest.webmanifest").write_text("{}")
    (tmp_path / "icons" / "icon-192.png").write_bytes(b"\x89PNG")
    api.web_dir = tmp_path
    for path, ctype, body in [
        ("/", "text/html", b"<html>hi</html>"),
        ("/index.html", "text/html", b"<html>hi</html>"),
        ("/app.js", "text/javascript", b"//js"),
        ("/manifest.webmanifest", "application/manifest+json", b"{}"),
        ("/icons/icon-192.png", "image/png", b"\x89PNG"),
    ]:
        status, payload, resp = await c.call("GET", path)
        assert status == 200, path
        assert resp.content_type.startswith(ctype)
        assert resp.body == body
    assert (await c.call("GET", "/style.css"))[0] == 404  # allowed name, file missing


@pytest.mark.parametrize("path", [
    "/../etc/passwd", "/..%2f..%2fetc/passwd", "/%2e%2e/secrets", "/icons/../app.py",
    "/icons/", "/icons", "/.env", "/pairtner/db.py", "//etc/passwd",
    "/app.js/../../x", "/index.html%00.png", "/secret.txt", "/web/", "/%252e%252e/x",
])
async def test_static_path_traversal_rejected(env, tmp_path, path):
    _, api, c, _ = env
    (tmp_path / "secret.txt").write_text("TOPSECRET")
    (tmp_path / ".env").write_text("TOPSECRET")
    api.web_dir = tmp_path / "web"
    (tmp_path / "web").mkdir()
    status, body, _ = await c.call("GET", path)
    assert status in (404, 405)
    assert b"TOPSECRET" not in (body if isinstance(body, bytes) else json.dumps(body).encode())


# ── request validation ───────────────────────────────────────────────────

async def test_body_validation(env):
    _, api, c, _ = env
    tok = (await c.register("A"))["token"]
    status, p, _ = await c.call("POST", "/api/v1/message", raw=b"{not json", token=tok)
    assert (status, p["error"]) == (400, "invalid_json")
    status, p, _ = await c.call("POST", "/api/v1/message", raw=b"[1,2]", token=tok)
    assert (status, p["error"]) == (400, "invalid_json")
    status, p, _ = await c.call("POST", "/api/v1/message", raw=b"\xff\xfe", token=tok)
    assert (status, p["error"]) == (400, "invalid_json")
    status, p, _ = await c.call("POST", "/api/v1/message", {"text": "x"}, tok,
                                headers={"content-type": "text/plain"})
    assert status == 415
    for bad in ({}, {"text": 5}, {"text": "   "}, {"text": None}):
        status, p, _ = await c.call("POST", "/api/v1/message", bad, tok)
        assert (status, p["error"]) == (400, "invalid_text"), bad
    status, p, _ = await c.call("POST", "/api/v1/callback", {"data": 3}, tok)
    assert (status, p["error"]) == (400, "invalid_data")
    status, p, _ = await c.call("POST", "/api/v1/callback", {"data": "x" * 300}, tok)
    assert status == 400


async def test_oversize_body_and_long_text(env):
    _, api, c, cfg = env
    tok = (await c.register("A"))["token"]
    big = json.dumps({"text": "a" * (MAX_BODY_BYTES + 10)}).encode()
    status, p, _ = await c.call("POST", "/api/v1/message", raw=big, token=tok)
    assert (status, p["error"]) == (413, "body_too_large")
    # oversize is rejected even before auth
    status, p, _ = await c.call("POST", "/api/v1/message", raw=big)
    assert status == 413
    text = "a" * (cfg.rest_max_text_chars + 1)
    status, p, _ = await c.call("POST", "/api/v1/message", {"text": text}, tok)
    assert (status, p["error"]) == (400, "text_too_long")


async def test_events_query_validation_and_wait_clamp(env):
    _, api, c, cfg = env
    tok = (await c.register("A"))["token"]
    for q in ("after=abc", "wait=x", "after=-1", "wait=nan"):
        status, p, _ = await c.call("GET", f"/api/v1/events?{q}", token=tok)
        assert (status, p["error"]) == (400, "invalid_query"), q
    loop = asyncio.get_running_loop()
    start = loop.time()
    status, p, _ = await c.call("GET", "/api/v1/events?wait=9999", token=tok)
    elapsed = loop.time() - start
    assert status == 200 and p == {"events": [], "cursor": 0}
    assert cfg.rest_long_poll_max_seconds - 0.2 <= elapsed < cfg.rest_long_poll_max_seconds + 1.5


# ── me, rotate ───────────────────────────────────────────────────────────

async def test_me_and_rotate(env):
    _, _, c, _ = env
    a = await c.register("Alice")
    _, me, _ = await c.call("GET", "/api/v1/me", token=a["token"])
    assert me == {"user_id": a["user_id"], "name": "Alice", "partner_id": None,
                  "partner_name": None, "lang": "en", "paused": False}
    _, rotated, _ = await c.call("POST", "/api/v1/token/rotate", token=a["token"])
    assert (await c.call("GET", "/api/v1/me", token=a["token"]))[0] == 401
    assert (await c.call("GET", "/api/v1/me", token=rotated["token"]))[0] == 200


# ── two users end to end ─────────────────────────────────────────────────

async def _pair(c, svc):
    a = await c.register("Alice")
    b = await c.register("Bob")
    await c.call("POST", "/api/v1/message", {"text": "/start"}, a["token"])
    code = next(iter(svc._pairing_codes))
    status, body, _ = await c.call("POST", "/api/v1/message",
                                   {"text": f"/join {code}"}, b["token"])
    assert status == 200
    for u in (a, b):
        await c.call("POST", "/api/v1/message", {"text": "/skip"}, u["token"])
    return a, b


async def _deliver_once(api, db_path, ticks=0.1):
    async def send(uid, text):
        await api.publish(uid, text_to_msg(text))

    async def send_buttons(uid, text, buttons):
        await api.publish(uid, text_to_msg(text, buttons))

    task = asyncio.create_task(delivery_loop(
        db_path, send, send_buttons_func=send_buttons,
        poll_interval=0.01, review_before_send=False))
    await asyncio.sleep(ticks)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_two_users_pair_send_approve_and_receive_via_events(test_db, llm):
    svc, api, c, cfg = _mk(test_db, llm)
    a, b = await _pair(c, svc)
    _, me, _ = await c.call("GET", "/api/v1/me", token=a["token"])
    assert me["partner_id"] == b["user_id"] and me["partner_name"] == "Bob"

    # pairing notifications reached Alice as events (cursor semantics)
    _, ev, _ = await c.call("GET", "/api/v1/events", token=a["token"])
    assert ev["events"] and ev["cursor"] == ev["events"][-1]["id"]
    assert any("Bob" in e["msg"]["text"] for e in ev["events"])
    cursor = ev["cursor"]
    _, again, _ = await c.call("GET", f"/api/v1/events?after={cursor}", token=a["token"])
    assert again == {"events": [], "cursor": cursor}

    llm.responses.append({"reply": "ok", "new_facts": [], "relay_draft": "RELAYSENTINEL hello Bob"})
    status, out, _ = await c.call("POST", "/api/v1/message",
                                  {"text": "tell him hello #send"}, a["token"])
    button = out["replies"][-1]["buttons"][0][0]
    assert button["data"].startswith("ap:")
    status, out, _ = await c.call("POST", "/api/v1/callback",
                                  {"data": button["data"]}, a["token"])
    assert status == 200 and out["replies"]

    await _deliver_once(api, test_db)
    _, bob_ev, _ = await c.call("GET", "/api/v1/events", token=b["token"])
    texts = [e["msg"]["text"] for e in bob_ev["events"]]
    assert any("RELAYSENTINEL" in t for t in texts)
    # Alice does not receive Bob's relay, Bob does not see Alice's events
    _, alice_ev, _ = await c.call("GET", f"/api/v1/events?after={cursor}", token=a["token"])
    assert not any("RELAYSENTINEL" in e["msg"]["text"] for e in alice_ev["events"])
    # cursor semantics for Bob
    last = bob_ev["cursor"]
    _, none, _ = await c.call("GET", f"/api/v1/events?after={last}", token=b["token"])
    assert none["events"] == []


async def test_relay_to_offline_user_is_retained_until_polled(test_db, llm):
    svc, api, c, cfg = _mk(test_db, llm)
    a, b = await _pair(c, svc)
    llm.responses.append({"reply": "ok", "new_facts": [], "relay_draft": "OFFLINEKEEP"})
    _, out, _ = await c.call("POST", "/api/v1/message", {"text": "x #send"}, a["token"])
    await c.call("POST", "/api/v1/callback",
                 {"data": out["replies"][-1]["buttons"][0][0]["data"]}, a["token"])
    await _deliver_once(api, test_db)
    await asyncio.sleep(0.05)  # Bob never polled in between
    _, ev, _ = await c.call("GET", "/api/v1/events", token=b["token"])
    assert any("OFFLINEKEEP" in e["msg"]["text"] for e in ev["events"])
    rows = await get_outbox_events(test_db, b["user_id"], 0)
    assert any("OFFLINEKEEP" in r["payload"] for r in rows)


async def test_long_poll_wakes_on_new_event(test_db, llm):
    svc, api, c, cfg = _mk(test_db, llm, rest_long_poll_max_seconds=5)
    a = await c.register("Alice")
    poll = asyncio.create_task(
        c.call("GET", "/api/v1/events?wait=5", token=a["token"]))
    await asyncio.sleep(0.2)
    assert not poll.done()
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await api.publish(a["user_id"], text_to_msg("<b>wake</b>"))
    status, body, _ = await asyncio.wait_for(poll, 2)
    assert status == 200 and loop.time() - t0 < 1.0
    assert body["events"][0]["msg"]["text"] == "wake"
    assert body["events"][0]["msg"]["markup"] == "<b>wake</b>"


async def test_events_not_visible_to_other_users(test_db, llm):
    svc, api, c, cfg = _mk(test_db, llm)
    a = await c.register("Alice")
    b = await c.register("Bob")
    await api.publish(a["user_id"], text_to_msg("for alice only"))
    _, ev_b, _ = await c.call("GET", "/api/v1/events", token=b["token"])
    assert ev_b["events"] == []
    _, ev_a, _ = await c.call("GET", "/api/v1/events", token=a["token"])
    assert len(ev_a["events"]) == 1
    # a user cannot ask for another user's events by id
    _, ev, _ = await c.call("GET", f"/api/v1/events?user_id={a['user_id']}", token=b["token"])
    assert ev["events"] == []


async def test_reply_markup_is_safe_and_plain_is_readable(test_db, llm):
    svc, api, c, cfg = _mk(test_db, llm)
    a = await c.register("Alice")
    llm.responses.append({"reply": "<b>fine</b> <script>alert(1)</script>",
                          "new_facts": [], "relay_draft": None})
    from pairtner.db import ensure_user_profile, update_user_profile
    await ensure_user_profile(test_db, a["user_id"])
    await update_user_profile(test_db, a["user_id"], onboarding_done=1)
    _, out, _ = await c.call("POST", "/api/v1/message", {"text": "hello"}, a["token"])
    msg = out["replies"][-1]
    assert "<script" not in msg["markup"]
    assert "<b>fine</b>" in msg["markup"]


async def test_messages_serialised_per_user(test_db, llm):
    svc, api, c, cfg = _mk(test_db, llm)
    a = await c.register("Alice")
    active = 0
    peak = 0
    original = svc.dispatch

    async def slow(uid, name, text):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        try:
            return await original(uid, name, text)
        finally:
            active -= 1

    svc.dispatch = slow
    await asyncio.gather(*(c.call("POST", "/api/v1/message", {"text": "/help"}, a["token"])
                           for _ in range(4)))
    assert peak == 1


async def test_pruning_old_events(test_db, llm):
    from pairtner import db
    import aiosqlite
    eid = await add_outbox_event(test_db, REST_ID_BASE, "message", "{}")
    async with aiosqlite.connect(test_db) as conn:
        await conn.execute(
            "UPDATE rest_outbox SET created_at = datetime('now', '-20 days') WHERE id = ?",
            (eid,))
        await conn.commit()
    fresh = await add_outbox_event(test_db, REST_ID_BASE, "message", "{}")
    assert await db.prune_outbox(test_db, 14) == 1
    ids = [r["id"] for r in await get_outbox_events(test_db, REST_ID_BASE, 0)]
    assert ids == [fresh]


async def test_forward_hook_used_for_non_rest_target(test_db, llm):
    svc, api, c, cfg = _mk(test_db, llm)
    forwarded = []

    async def fwd(uid, msg):
        forwarded.append((uid, msg["text"]))

    api.forward = fwd
    from pairtner.handlers import BotResponse
    await api._reply(REST_ID_BASE, [BotResponse("to telegram", target_user_id=42),
                                    BotResponse("to rest", target_user_id=REST_ID_BASE + 3)])
    assert forwarded == [(42, "to telegram")]
    rows = await get_outbox_events(test_db, REST_ID_BASE + 3, 0)
    assert len(rows) == 1
