"""REST API for the pAIrtner bot (JSON over HTTP, stdlib only).

``RestApi.handle`` is a pure async ``Request -> Response`` function so it can
be tested without sockets; ``couples_bot.adapters.rest_adapter`` wires it to a
``ThreadingHTTPServer``. See docs/REST_API.md for the client-facing contract.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, unquote, urlsplit

from couples_bot import db as db_mod
from couples_bot.config import Config, is_user_allowed
from couples_bot.handlers import BotResponse
from couples_bot.i18n import t
from couples_bot.llm import describe_llm
from couples_bot.rest.ids import REST_ID_BASE
from couples_bot.rest.markup import to_markup, to_plain
from couples_bot.rest.throttle import SlidingWindowLimiter

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 16 * 1024
MAX_NAME_CHARS = 40
EVENT_PAGE_SIZE = 100
LANGUAGES = ("en", "te")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

# URL path -> (file name under web/, content type). Fixed allowlist: a request
# path is only ever looked up in this dict, never joined onto a directory.
STATIC_FILES: dict[str, tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/duo.html": ("duo.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/render.js": ("render.js", "text/javascript; charset=utf-8"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/manifest.webmanifest": (
        "manifest.webmanifest", "application/manifest+json; charset=utf-8"),
    "/icons/icon-192.png": ("icons/icon-192.png", "image/png"),
    "/icons/icon-512.png": ("icons/icon-512.png", "image/png"),
    "/icons/icon.svg": ("icons/icon.svg", "image/svg+xml"),
}

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_NAME_STRIP_RE = re.compile(r"[<>&]")


@dataclass
class Request:
    method: str
    target: str  # raw request target, including the query string
    headers: dict[str, str] = field(default_factory=dict)  # lower-case keys
    body: bytes = b""
    client_ip: str = "-"


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    content_type: str = "application/json; charset=utf-8"
    headers: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    public: bool
    handler: str  # RestApi method name
    needs_json: bool = False


ROUTES: tuple[Route, ...] = (
    Route("GET", "/healthz", True, "_healthz"),
    Route("GET", "/api/v1/meta", True, "_meta"),
    Route("POST", "/api/v1/register", True, "_register", needs_json=True),
    Route("GET", "/api/v1/me", False, "_me"),
    Route("POST", "/api/v1/message", False, "_message", needs_json=True),
    Route("POST", "/api/v1/callback", False, "_callback", needs_json=True),
    Route("GET", "/api/v1/events", False, "_events"),
    Route("POST", "/api/v1/token/rotate", False, "_rotate"),
) + tuple(Route("GET", p, True, "_static") for p in STATIC_FILES)

_ROUTE_INDEX: dict[tuple[str, str], Route] = {(r.method, r.path): r for r in ROUTES}
_KNOWN_PATHS = {r.path for r in ROUTES}


class ApiError(Exception):
    """Raised inside handlers to return a JSON error."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def _json(status: int, payload: Any) -> Response:
    return Response(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def sanitize_name(raw: Any) -> str | None:
    """Return a display name of 1-40 chars without control chars or <>&."""
    if not isinstance(raw, str):
        return None
    cleaned = _NAME_STRIP_RE.sub("", _CONTROL_RE.sub(" ", raw))
    cleaned = " ".join(cleaned.split())[:MAX_NAME_CHARS].strip()
    return cleaned or None


def bot_response_to_msg(resp: BotResponse) -> dict[str, Any]:
    """Convert a BotResponse to the client ``Msg`` shape."""
    buttons = [
        [{"text": b.text, "data": b.callback_data} for b in row]
        for row in (resp.buttons or [])
    ]
    document = (
        {"filename": resp.document[0], "content": resp.document[1]}
        if resp.document else None
    )
    return {
        "text": to_plain(resp.text),
        "markup": to_markup(resp.text),
        "buttons": buttons,
        "document": document,
    }


def text_to_msg(
    text: str,
    buttons: list[list[tuple[str, str]]] | None = None,
    document: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """Msg for loop-originated messages (plain tuples, not BotResponse)."""
    return {
        "text": to_plain(text),
        "markup": to_markup(text),
        "buttons": [[{"text": a, "data": b} for a, b in row] for row in (buttons or [])],
        "document": {"filename": document[0], "content": document[1]} if document else None,
    }


class RestApi:
    """Routes, auth, registration, long-poll, static files."""

    def __init__(
        self,
        service: Any,
        config: Config,
        hooks: Any = None,
        *,
        web_dir: Path | None = None,
    ) -> None:
        self.service = service
        self.config = config
        self.hooks = hooks
        self.web_dir = web_dir or WEB_DIR
        self.db_path = service.db_path
        self.register_limiter = SlidingWindowLimiter(10, 60.0)
        self.auth_fail_limiter = SlidingWindowLimiter(30, 60.0)
        # Optional async callable (user_id, Msg) used for responses addressed to
        # a user this adapter does not own (set by MultiAdapter).
        self.forward: Callable[[int, dict[str, Any]], Awaitable[None]] | None = None
        self._user_locks: dict[int, asyncio.Lock] = {}
        self._wakeups: dict[int, asyncio.Event] = {}
        self._alloc_lock = asyncio.Lock()

    # ── public helpers ───────────────────────────────────────────────────

    @property
    def routes(self) -> tuple[Route, ...]:
        return ROUTES

    def _invite_code(self) -> str:
        getter = getattr(self.hooks, "invite_code", None)
        if callable(getter):
            return getter() or ""
        return os.environ.get("REST_INVITE_CODE", "")

    async def publish(self, user_id: int, msg: dict[str, Any]) -> int:
        """Store a message event for *user_id* and wake waiting polls."""
        event_id = await db_mod.add_outbox_event(
            self.db_path, user_id, "message", json.dumps(msg, ensure_ascii=False),
        )
        self._wakeup(user_id).set()
        return event_id

    def _wakeup(self, user_id: int) -> asyncio.Event:
        ev = self._wakeups.get(user_id)
        if ev is None:
            ev = self._wakeups[user_id] = asyncio.Event()
        return ev

    def _lock(self, user_id: int) -> asyncio.Lock:
        lock = self._user_locks.get(user_id)
        if lock is None:
            lock = self._user_locks[user_id] = asyncio.Lock()
        return lock

    # ── entry point ──────────────────────────────────────────────────────

    async def handle(self, request: Request) -> Response:
        try:
            resp = await self._dispatch(request)
        except ApiError as exc:
            resp = _json(exc.status, {"error": exc.code})
            if exc.status == 401:
                resp.headers.append(("WWW-Authenticate", "Bearer"))
        return self._finish(resp, request)

    def _finish(self, resp: Response, request: Request) -> Response:
        names = {k.lower() for k, _ in resp.headers}
        for key, value in SECURITY_HEADERS.items():
            if key.lower() not in names:
                resp.headers.append((key, value))
        if "cache-control" not in names:
            path = urlsplit(request.target).path
            resp.headers.append(
                ("Cache-Control", "no-store" if path.startswith("/api/") else "no-cache")
            )
        return resp

    async def _dispatch(self, request: Request) -> Response:
        parts = urlsplit(request.target)
        path = unquote(parts.path)
        route = _ROUTE_INDEX.get((request.method, path))
        if route is None:
            if path in _KNOWN_PATHS:
                raise ApiError(405, "method_not_allowed")
            raise ApiError(404, "not_found")
        if len(request.body) > MAX_BODY_BYTES:
            raise ApiError(413, "body_too_large")
        uid: int | None = None
        if not route.public:
            uid = await self._authenticate(request)
        body: dict[str, Any] = {}
        if route.needs_json:
            body = self._parse_json(request)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        handler = getattr(self, route.handler)
        return await handler(request=request, path=path, uid=uid, body=body, query=query)

    # ── auth ─────────────────────────────────────────────────────────────

    async def _authenticate(self, request: Request) -> int:
        ip = request.client_ip
        if self.auth_fail_limiter.blocked(ip):
            raise ApiError(429, "too_many_requests")
        uid = await self._check_bearer(request.headers.get("authorization", ""))
        if uid is None:
            self.auth_fail_limiter.hit(ip)
            raise ApiError(401, "unauthorized")
        if not is_user_allowed(self.config, uid):
            raise ApiError(403, "not_allowed")
        return uid

    async def _check_bearer(self, header: str) -> int | None:
        scheme, _, cred = header.partition(" ")
        if scheme.lower() != "bearer":
            return None
        uid_s, sep, secret = cred.strip().partition(":")
        if not sep or not secret or not (uid_s.isascii() and uid_s.isdigit()) or len(uid_s) > 18:
            return None
        uid = int(uid_s)
        if uid < REST_ID_BASE:
            return None
        if not await db_mod.validate_auth_token(self.db_path, uid, secret):
            return None
        return uid

    @staticmethod
    def _parse_json(request: Request) -> dict[str, Any]:
        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise ApiError(415, "json_required")
        try:
            data = json.loads(request.body.decode("utf-8") or "null")
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "invalid_json")
        if not isinstance(data, dict):
            raise ApiError(400, "invalid_json")
        return data

    # ── handlers ─────────────────────────────────────────────────────────

    async def _healthz(self, **_: Any) -> Response:
        return Response(200, b"ok", "text/plain; charset=utf-8")

    async def _meta(self, **_: Any) -> Response:
        info = self.service.privacy_info
        privacy = t("en", "privacy_note", transport=info.transport, llm=info.llm)
        return _json(200, {
            "name": "pAIrtner",
            "transport": "rest",
            "registration": self.config.rest_registration,
            "llm": describe_llm(self.config),
            "languages": list(LANGUAGES),
            "privacy": privacy,
        })

    async def _static(self, *, path: str, **_: Any) -> Response:
        filename, ctype = STATIC_FILES[path]
        file_path = self.web_dir / filename
        try:
            data = file_path.read_bytes()
        except FileNotFoundError:
            raise ApiError(404, "not_found")
        headers = [("Cache-Control", "no-cache")]
        return Response(200, data, ctype, headers)

    async def _register(
        self, *, request: Request, body: dict[str, Any], **_: Any,
    ) -> Response:
        ip = request.client_ip
        if not self.register_limiter.hit(ip):
            raise ApiError(429, "too_many_requests")
        mode = self.config.rest_registration
        if mode not in ("invite", "open"):
            raise ApiError(403, "registration_closed")
        if mode == "invite":
            expected = self._invite_code()
            if not expected:
                raise ApiError(403, "registration_unavailable")
            given = body.get("invite")
            if not isinstance(given, str) or not secrets.compare_digest(
                given.encode("utf-8"), expected.encode("utf-8")
            ):
                raise ApiError(403, "invalid_invite")
        name = sanitize_name(body.get("name"))
        if name is None:
            raise ApiError(400, "invalid_name")
        lang = body.get("lang", "en")
        if lang not in LANGUAGES:
            raise ApiError(400, "invalid_lang")

        async with self._alloc_lock:
            if self.config.allowed_user_ids:
                uid = body.get("user_id")
                if (not isinstance(uid, int) or isinstance(uid, bool)
                        or uid < REST_ID_BASE or not is_user_allowed(self.config, uid)):
                    raise ApiError(403, "not_allowed")
                if await db_mod.get_user(self.db_path, uid) is not None:
                    raise ApiError(409, "user_exists")
                await db_mod.create_user(self.db_path, uid, name, lang)
            else:
                uid = await db_mod.allocate_rest_user(
                    self.db_path, name, lang, REST_ID_BASE)
        token = await self._issue_token(uid)
        return _json(201, {"user_id": uid, "name": name, "token": token})

    async def _issue_token(self, uid: int) -> str:
        secret = secrets.token_urlsafe(32)
        await db_mod.create_auth_token(self.db_path, uid, secret)
        return f"{uid}:{secret}"

    async def _rotate(self, *, uid: int, **_: Any) -> Response:
        return _json(200, {"user_id": uid, "token": await self._issue_token(uid)})

    async def _me(self, *, uid: int, **_: Any) -> Response:
        user = await db_mod.get_user(self.db_path, uid)
        if user is None:
            raise ApiError(401, "unauthorized")
        partner_id = user.get("partner_id")
        partner = await db_mod.get_user(self.db_path, partner_id) if partner_id else None
        return _json(200, {
            "user_id": uid,
            "name": user.get("name"),
            "partner_id": partner_id,
            "partner_name": (partner or {}).get("name"),
            "lang": user.get("lang") or "en",
            "paused": bool(user.get("paused")),
        })

    async def _message(self, *, uid: int, body: dict[str, Any], **_: Any) -> Response:
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ApiError(400, "invalid_text")
        if len(text) > self.config.rest_max_text_chars:
            raise ApiError(400, "text_too_long")
        async with self._lock(uid):
            user = await db_mod.get_user(self.db_path, uid)
            name = (user or {}).get("name") or f"User{uid}"
            responses = await self.service.dispatch(uid, name, text)
            return await self._reply(uid, responses)

    async def _callback(self, *, uid: int, body: dict[str, Any], **_: Any) -> Response:
        data = body.get("data")
        if not isinstance(data, str) or not data.strip() or len(data) > 256:
            raise ApiError(400, "invalid_data")
        async with self._lock(uid):
            responses = await self.service.handle_callback(uid, data.strip())
            return await self._reply(uid, responses)

    async def _reply(self, uid: int, responses: list[BotResponse]) -> Response:
        replies: list[dict[str, Any]] = []
        for resp in responses:
            msg = bot_response_to_msg(resp)
            target = resp.target_user_id
            if target is None or target == uid:
                replies.append(msg)
            elif target >= REST_ID_BASE or self.forward is None:
                await self.publish(target, msg)
            else:
                await self.forward(target, msg)
        return _json(200, {"replies": replies})

    async def _events(self, *, uid: int, query: dict[str, str], **_: Any) -> Response:
        try:
            after = int(query.get("after", "0"))
            wait = float(query.get("wait", "0"))
        except ValueError:
            raise ApiError(400, "invalid_query")
        if after < 0 or wait != wait:  # negative cursor or NaN
            raise ApiError(400, "invalid_query")
        wait = max(0.0, min(wait, float(self.config.rest_long_poll_max_seconds)))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        wakeup = self._wakeup(uid)
        while True:
            wakeup.clear()
            rows = await db_mod.get_outbox_events(
                self.db_path, uid, after, EVENT_PAGE_SIZE)
            remaining = deadline - loop.time()
            if rows or remaining <= 0:
                break
            try:
                await asyncio.wait_for(wakeup.wait(), remaining)
            except asyncio.TimeoutError:
                pass
        events = [
            {
                "id": r["id"],
                "type": r["kind"],
                "msg": json.loads(r["payload"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]
        cursor = events[-1]["id"] if events else after
        return _json(200, {"events": events, "cursor": cursor})
