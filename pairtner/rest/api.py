"""REST API for the pAIrtner bot (JSON over HTTP, stdlib only).

``RestApi.handle`` is a pure async ``Request -> Response`` function so it can
be tested without sockets; ``pairtner.adapters.rest_adapter`` wires it to a
``ThreadingHTTPServer``. See docs/REST_API.md for the client-facing contract.
"""
from __future__ import annotations

import asyncio
import contextvars
import dataclasses
import json
import logging
import os
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, unquote, urlsplit

from pairtner import db as db_mod
from pairtner.storage.messages import msg_source
from pairtner.config import Config, is_user_allowed
from pairtner.handlers import BotResponse, BotService
from pairtner.i18n import t
from pairtner.llm import describe_llm
from pairtner.rest.ids import REST_ID_BASE
from pairtner.rest import dashboard_data as dd
from pairtner.rest.markup import to_markup, to_plain
from pairtner.rest.throttle import SlidingWindowLimiter

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 16 * 1024
SANDBOX_TTL_SECONDS = 0  # 0 = no auto-expiry; sandbox persists until reset or server restart
MAX_SANDBOXES = 20
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
    "/couple/": ("index.html", "text/html; charset=utf-8"),
    "/solo/": ("index.html", "text/html; charset=utf-8"),
    "/couple/manifest.json": (
        "manifest-couple.webmanifest", "application/manifest+json; charset=utf-8"),
    "/solo/manifest.json": (
        "manifest-solo.webmanifest", "application/manifest+json; charset=utf-8"),
    "/duo.html": ("duo.html", "text/html; charset=utf-8"),
    "/duo_keep.html": ("duo_keep.html", "text/html; charset=utf-8"),
    "/duo.js": ("duo.js", "text/javascript; charset=utf-8"),
    "/duo_keep.js": ("duo_keep.js", "text/javascript; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/components/chat.js": ("components/chat.js", "text/javascript; charset=utf-8"),
    "/components/dashboard.js": ("components/dashboard.js", "text/javascript; charset=utf-8"),
    "/components/memory.js": ("components/memory.js", "text/javascript; charset=utf-8"),
    "/components/settings.js": ("components/settings.js", "text/javascript; charset=utf-8"),
    "/components/couple.js": ("components/couple.js", "text/javascript; charset=utf-8"),
    "/components/admin.js": ("components/admin.js", "text/javascript; charset=utf-8"),
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
class Sandbox:
    """An isolated temp DB (plus a service bound to it) for the duo dev page."""

    id: str
    db_path: str
    service: Any
    created: float = field(default_factory=time.monotonic)


# The sandbox (if any) serving the request being handled in this task.
_current_sandbox: contextvars.ContextVar[Sandbox | None] = contextvars.ContextVar(
    "pairtner_sandbox", default=None)


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
    Route("POST", "/api/v1/login", True, "_login", needs_json=True),
    # Dev sandbox (temp DB; no auth, it is a dev tool)
    Route("POST", "/api/v1/sandbox/create", True, "_sandbox_create"),
    Route("GET", "/api/v1/me", False, "_me"),
    Route("POST", "/api/v1/message", False, "_message", needs_json=True),
    Route("POST", "/api/v1/callback", False, "_callback", needs_json=True),
    Route("GET", "/api/v1/events", False, "_events"),
    Route("GET", "/api/v1/history", False, "_history"),
    Route("GET", "/api/v1/facts", False, "_facts"),
    Route("GET", "/api/v1/export/therapy", False, "_export_therapy"),
    Route("GET", "/api/v1/settings/model", False, "_model_get"),
    Route("PUT", "/api/v1/settings/model", False, "_model_put", needs_json=True),
    # PWA dashboard (user-scoped)
    Route("GET", "/api/v1/summaries", False, "_summaries"),
    Route("GET", "/api/v1/topics", False, "_topics"),
    Route("GET", "/api/v1/dashboard/stats", False, "_dashboard_stats"),
    Route("GET", "/api/v1/relationships", False, "_relationships"),
    # PWA admin (bearer auth + X-Admin-Token)
    Route("GET", "/api/v1/admin/stats", False, "_admin_stats"),
    Route("GET", "/api/v1/admin/users", False, "_admin_users"),
    Route("POST", "/api/v1/admin/dryrun", False, "_admin_dryrun", needs_json=True),
    Route("DELETE", "/api/v1/message", False, "_delete_message", needs_json=True),
    Route("POST", "/api/v1/token/rotate", False, "_rotate"),
    # Memory API v1
    Route("GET", "/api/v1/memory", False, "_memory_list"),
    Route("GET", "/api/v1/memory/stats", False, "_memory_stats"),
    Route("GET", "/api/v1/memory/recency", False, "_memory_recency"),
    Route("GET", "/api/v1/memory/search", False, "_memory_search"),
    # Search v2
    Route("GET", "/api/search", False, "_search"),
    Route("POST", "/api/search/rerank", False, "_search_rerank", needs_json=True),
    # Push notifications
    # Chat import
    Route("POST", "/api/v1/import", False, "_import_chat", needs_json=True),
    # Summary trigger (manual)
    Route("POST", "/api/v1/summaries/trigger", False, "_summary_trigger"),
    # Push notifications
    Route("POST", "/api/v1/push/subscribe", False, "_push_subscribe", needs_json=True),
    Route("DELETE", "/api/v1/push/subscribe", False, "_push_unsubscribe", needs_json=True),
    Route("GET", "/api/v1/push/vapid", True, "_push_vapid"),
) + tuple(Route("GET", p, True, "_static") for p in STATIC_FILES)

# Routes with a path parameter: (method, pattern, handler). All need user auth.
DYNAMIC_ROUTES: tuple[tuple[str, "re.Pattern[str]", str], ...] = (
    ("GET", re.compile(r"^/api/v1/couple/([^/]+)/(conflicts|nudges|relays|facts)$"), "_couple"),
    ("GET", re.compile(r"^/api/v1/admin/pipeline/(\d{1,18})$"), "_admin_pipeline"),
    ("PUT", re.compile(r"^/api/v1/facts/(\d{1,18})$"), "_fact_update"),
    ("DELETE", re.compile(r"^/api/v1/facts/(\d{1,18})$"), "_fact_delete"),
    ("DELETE", re.compile(r"^/api/v1/sandbox/([0-9a-zA-Z_-]+)$"), "_sandbox_delete"),
)
_JSON_DYNAMIC = frozenset({"_fact_update"})
_PUBLIC_DYNAMIC = frozenset({"_sandbox_delete"})

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
        self._base_db_path = service.db_path
        self._sandboxes: dict[str, Sandbox] = {}
        self.sandbox_limiter = SlidingWindowLimiter(30, 60.0)
        self.register_limiter = SlidingWindowLimiter(10, 60.0)
        self.auth_fail_limiter = SlidingWindowLimiter(30, 60.0)
        # Optional async callable (user_id, Msg) used for responses addressed to
        # a user this adapter does not own (set by MultiAdapter).
        self.forward: Callable[[int, dict[str, Any]], Awaitable[None]] | None = None
        self._user_locks: dict[Any, asyncio.Lock] = {}
        self._wakeups: dict[Any, asyncio.Event] = {}
        self._alloc_lock = asyncio.Lock()

    # ── sandbox-aware db/service ─────────────────────────────────────────
    # Inside a request carrying ?sandbox=<id>, ``db_path`` and ``service``
    # resolve to that sandbox's temp DB and a service bound to it.

    @property
    def db_path(self) -> str:
        sb = _current_sandbox.get()
        return sb.db_path if sb else self._base_db_path

    @db_path.setter
    def db_path(self, value: str) -> None:
        self._base_db_path = value

    @property
    def service(self) -> Any:
        sb = _current_sandbox.get()
        return sb.service if sb else self._base_service

    @service.setter
    def service(self, value: Any) -> None:
        self._base_service = value

    @staticmethod
    def _key(user_id: int) -> Any:
        sb = _current_sandbox.get()
        return (sb.id, user_id) if sb else user_id

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
        key = self._key(user_id)
        ev = self._wakeups.get(key)
        if ev is None:
            ev = self._wakeups[key] = asyncio.Event()
        return ev

    def _lock(self, user_id: int) -> asyncio.Lock:
        key = self._key(user_id)
        lock = self._user_locks.get(key)
        if lock is None:
            lock = self._user_locks[key] = asyncio.Lock()
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
        params: tuple[str, ...] = ()
        wrong_method = False
        if route is None:
            for method, pattern, handler_name in DYNAMIC_ROUTES:
                m = pattern.match(path)
                if m is None:
                    continue
                if method != request.method:
                    wrong_method = True
                    continue
                route = Route(method, path, handler_name in _PUBLIC_DYNAMIC, handler_name,
                              needs_json=handler_name in _JSON_DYNAMIC)
                params = m.groups()
                break
        if route is None:
            if wrong_method:
                raise ApiError(405, "method_not_allowed")
            if path in _KNOWN_PATHS:
                raise ApiError(405, "method_not_allowed")
            raise ApiError(404, "not_found")
        if len(request.body) > MAX_BODY_BYTES:
            raise ApiError(413, "body_too_large")
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        self._purge_sandboxes()
        token = None
        if path.startswith("/api") and not route.handler.startswith("_sandbox_"):
            sandbox_id = query.get("sandbox")
            if sandbox_id:
                sb = self._sandboxes.get(sandbox_id)
                if sb is None:
                    # Auto-recover persistent sandboxes from disk
                    persist_path = f"/data/pairtner-sandbox-{sandbox_id}.db"
                    if os.path.exists(persist_path):
                        service = self._base_service.__class__(
                            self._base_service.config, persist_path, self._base_service.llm)
                        sb = Sandbox(sandbox_id, persist_path, service)
                        sb.created = time.monotonic()
                        self._sandboxes[sandbox_id] = sb
                    else:
                        raise ApiError(404, "sandbox_not_found")
                token = _current_sandbox.set(sb)
        try:
            uid: int | None = None
            if not route.public:
                uid = await self._authenticate(request)
            body: dict[str, Any] = {}
            if route.needs_json:
                body = self._parse_json(request)
            handler = getattr(self, route.handler)
            return await handler(
                request=request, path=path, uid=uid, body=body, query=query, params=params)
        finally:
            if token is not None:
                _current_sandbox.reset(token)

    # ── auth ─────────────────────────────────────────────────────────────

    async def _authenticate(self, request: Request) -> int:
        ip = request.client_ip
        if self.auth_fail_limiter.blocked(ip):
            raise ApiError(429, "too_many_requests")
        uid = await self._check_bearer(request.headers.get("authorization", ""))
        if uid is None:
            self.auth_fail_limiter.hit(ip)
            raise ApiError(401, "unauthorized")
        if _current_sandbox.get() is None and not is_user_allowed(self.config, uid):
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
        # Tokens are only issued by /register (REST ids) and /login (any user
        # with a password, incl. Telegram ids), so no id-range check here.
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

    async def _login(
        self, *, request: Request, body: dict[str, Any], **_: Any,
    ) -> Response:
        ip = request.client_ip
        if not self.register_limiter.hit(ip):
            raise ApiError(429, "too_many_requests")
        password = body.get("password")
        if not isinstance(password, str) or not password:
            raise ApiError(400, "invalid_credentials")
        uid_in = body.get("user_id")
        name = body.get("name")
        if uid_in is not None:
            if not isinstance(uid_in, int) or isinstance(uid_in, bool):
                raise ApiError(400, "invalid_credentials")
            candidates = [uid_in]
        elif isinstance(name, str) and name.strip():
            candidates = await db_mod.find_user_ids_by_name(
                self.db_path, name.strip())
        else:
            raise ApiError(400, "invalid_credentials")
        for uid in candidates:
            if await db_mod.verify_password(self.db_path, uid, password):
                return _json(200, {
                    "user_id": uid, "token": await self._issue_token(uid)})
        raise ApiError(401, "invalid_credentials")

    async def _issue_token(self, uid: int) -> str:
        secret = secrets.token_urlsafe(32)
        await db_mod.create_auth_token(self.db_path, uid, secret)
        return f"{uid}:{secret}"

    async def _rotate(self, *, uid: int, **_: Any) -> Response:
        """Rotate: invalidate all existing tokens, issue a fresh one."""
        import aiosqlite

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM auth_tokens WHERE user_id = ?", (uid,))
            await db.commit()
        return _json(200, {"user_id": uid, "token": await self._issue_token(uid)})

    # -----------------------------------------------------------------------
    # Memory API v1
    # -----------------------------------------------------------------------

    async def _memory_list(self, *, uid: int, query: dict[str, str], **_: Any) -> Response:
        """List facts with optional scoring, filtering, pagination."""
        from pairtner.core.memory_api import MemoryAPI

        api = MemoryAPI(self.db_path)
        kind = query.get("kind")
        status = query.get("status")
        scored = query.get("scored", "").lower() in ("1", "true", "yes")
        try:
            limit = int(query.get("limit", "50"))
            offset = int(query.get("offset", "0"))
        except ValueError:
            raise ApiError(400, "invalid_query")

        result = await api.list_facts(
            uid, kind=kind, status=status, limit=limit, offset=offset, scored=scored,
        )
        return _json(200, result.to_dict())

    async def _memory_stats(self, *, uid: int, **_: Any) -> Response:
        """Memory statistics for this user."""
        from pairtner.core.memory_api import MemoryAPI

        api = MemoryAPI(self.db_path)
        stats = await api.get_stats(uid)
        return _json(200, stats.to_dict())

    async def _memory_recency(self, *, uid: int, **_: Any) -> Response:
        """Memory health report."""
        from pairtner.core.memory_api import MemoryAPI

        api = MemoryAPI(self.db_path)
        report = await api.get_recency_report(uid)
        return _json(200, report.to_dict())

    async def _memory_search(self, *, uid: int, query: dict[str, str], **_: Any) -> Response:
        """Search facts by text query."""
        from pairtner.core.memory_api import MemoryAPI

        q = query.get("q", "").strip()
        if not q:
            raise ApiError(400, "missing_query")
        try:
            limit = int(query.get("limit", "10"))
        except ValueError:
            raise ApiError(400, "invalid_query")

        api = MemoryAPI(self.db_path)
        facts = await api.search(uid, q, limit=limit)
        return _json(200, {"facts": [f.to_dict() for f in facts], "count": len(facts)})

    def _search_target(self, uid: int, raw: Any, *, admin_only: bool = False) -> int:
        """Resolve the user_id a search acts for; others' data needs admin."""
        is_admin = uid in self.config.admin_user_ids
        if admin_only and not is_admin:
            raise ApiError(403, "admin_required")
        if raw is None or raw == "":
            return uid
        try:
            target = int(raw)
        except (TypeError, ValueError):
            raise ApiError(400, "invalid_query")
        if target != uid and not is_admin:
            raise ApiError(403, "forbidden")
        return target

    async def _search(self, *, uid: int, query: dict[str, str], **_: Any) -> Response:
        """Paginated FTS5 search (no LLM)."""
        from pairtner.core.search_v2 import paginated_search

        q = query.get("q", "").strip()
        if not q:
            raise ApiError(400, "missing_query")
        target = self._search_target(uid, query.get("user_id"))
        try:
            page = int(query.get("page", "1"))
            per_page = int(query.get("per_page", "10"))
        except ValueError:
            raise ApiError(400, "invalid_query")
        kind = query.get("kind") or None
        if kind not in (None, "core", "working"):
            raise ApiError(400, "invalid_kind")
        try:
            data = await paginated_search(
                self.db_path, target, q, query.get("relationship_id") or None,
                page=page, per_page=per_page, kind_filter=kind,
            )
        except PermissionError:
            raise ApiError(403, "forbidden")
        return _json(200, data)

    async def _search_rerank(
        self, *, uid: int, body: dict[str, Any], **_: Any,
    ) -> Response:
        """Two-pass (FTS5 -> LLM) search. Admin only: it spends LLM calls."""
        from pairtner.core.search_v2 import two_pass_search

        target = self._search_target(uid, body.get("user_id"), admin_only=True)
        q = body.get("query")
        if not isinstance(q, str) or not q.strip():
            raise ApiError(400, "missing_query")
        rel = body.get("relationship_id")
        if rel is not None and not isinstance(rel, str):
            raise ApiError(400, "invalid_query")
        try:
            results = await two_pass_search(
                self.db_path, self.service.llm, target, q.strip(), rel or None,
            )
        except PermissionError:
            raise ApiError(403, "forbidden")
        return _json(200, {"results": results, "count": len(results)})

    async def _history(self, *, uid: int, query: dict[str, str], **_: Any) -> Response:
        """Return the full message history for a user (oldest first, with timestamps)."""
        try:
            limit = min(int(query.get("limit", "500")), 2000)
            offset = max(int(query.get("offset", "0")), 0)
            offset_id = int(query.get("offset_id", "0"))
        except ValueError:
            raise ApiError(400, "invalid_query")
        if offset_id > 0:
            messages = await db_mod.get_history_after_id(self.db_path, uid, offset_id, limit=limit)
        else:
            messages = await db_mod.get_full_history(self.db_path, uid, limit=limit, offset=offset)
        return _json(200, {"messages": messages, "count": len(messages)})

    async def _facts(self, *, uid: int, query: dict[str, str], **_: Any) -> Response:
        """The user's active facts. Filters: kind, visibility|vis, tag,
        relationship_id ('none' = solo), ids (comma list), include_summaries=0,
        order=asc|desc (default asc), limit, offset."""
        kind = query.get("kind")  # core, working, episode, partner_view
        visibility = query.get("visibility") or query.get("vis")  # private, shared
        facts = await db_mod.get_active_facts(self.db_path, uid, visibility=visibility)
        if kind:
            facts = [f for f in facts if (f.get("kind") or "working") == kind]
        if query.get("include_summaries", "1") == "0":
            facts = [f for f in facts if f.get("kind") not in dd.SUMMARY_KINDS]
        tag = query.get("tag")
        if tag:
            facts = [f for f in facts if tag in dd.fact_tags(f.get("tag"))]
        rel = await self._rel_filter(uid, query)
        if rel == "none":
            facts = [f for f in facts if not f.get("relationship_id")]
        elif rel:
            facts = [f for f in facts if f.get("relationship_id") == rel]
        if query.get("ids"):
            try:
                wanted = {int(i) for i in query["ids"].split(",") if i}
            except ValueError:
                raise ApiError(400, "invalid_query")
            facts = [f for f in facts if f["id"] in wanted]
        if query.get("order") == "desc":
            facts = facts[::-1]
        total = len(facts)
        offset, limit = self._page(query, default_limit=None)
        facts = facts[offset:offset + limit] if limit is not None else facts[offset:]
        return _json(200, {"facts": facts, "count": len(facts), "total": total})

    async def _owned_fact(self, uid: int, params: tuple[str, ...]) -> dict[str, Any]:
        from pairtner.storage import facts as facts_mod

        row = await facts_mod.get_fact_by_id(self.db_path, int(params[0]))
        if row is None or row.get("owner_id") != uid:
            raise ApiError(404, "not_found")
        return row

    async def _fact_update(
        self, *, uid: int, body: dict[str, Any], params: tuple[str, ...], **_: Any,
    ) -> Response:
        """Change a fact's kind. Only promotion to 'core' is supported."""
        from pairtner.core.memory_api import MemoryAPI

        await self._owned_fact(uid, params)
        if body.get("kind") != "core":
            raise ApiError(400, "invalid_kind")
        fact = await MemoryAPI(self.db_path).promote_to_core(int(params[0]))
        if fact is None:
            raise ApiError(404, "not_found")
        return _json(200, {"fact": fact.to_dict()})

    async def _fact_delete(
        self, *, uid: int, params: tuple[str, ...], **_: Any,
    ) -> Response:
        """Hard-delete one of the user's own facts."""
        from pairtner.core.memory_api import MemoryAPI

        await self._owned_fact(uid, params)
        if not await MemoryAPI(self.db_path).delete(int(params[0]), uid):
            raise ApiError(404, "not_found")
        return _json(200, {"deleted": int(params[0])})

    @staticmethod
    def _page(query: dict[str, str], default_limit: int | None) -> tuple[int, int | None]:
        try:
            limit = int(query["limit"]) if "limit" in query else default_limit
            offset = int(query.get("offset", "0"))
        except ValueError:
            raise ApiError(400, "invalid_query")
        if offset < 0 or (limit is not None and limit < 1):
            raise ApiError(400, "invalid_query")
        return offset, None if limit is None else min(limit, 2000)

    async def _rel_filter(self, uid: int, query: dict[str, str]) -> str | None:
        """Validated ``relationship_id`` query value: None, 'none', or a member id."""
        rel = query.get("relationship_id") or None
        if rel and rel != "none" and await dd.user_relationship(self.db_path, uid, rel) is None:
            raise ApiError(404, "not_found")
        return rel

    async def _summaries(self, *, uid: int, query: dict[str, str], **_: Any) -> Response:
        """The user's period summaries. Filters: period, tag, relationship_id, limit."""
        period = query.get("period") or None
        if period and period not in dd.SUMMARY_KINDS:
            raise ApiError(400, "invalid_period")
        _, limit = self._page(query, default_limit=100)
        rel = await self._rel_filter(uid, query)
        rows = await dd.summaries(self.db_path, uid, rel, period, query.get("tag") or None, limit)
        return _json(200, {"summaries": rows, "count": len(rows)})

    async def _topics(self, *, uid: int, query: dict[str, str], **_: Any) -> Response:
        """Per-tag topic index of the user's own facts. Filters: relationship_id, limit."""
        _, limit = self._page(query, default_limit=50)
        rel = await self._rel_filter(uid, query)
        rows = await dd.topics(self.db_path, uid, rel, limit)
        return _json(200, {"topics": rows, "count": len(rows)})

    async def _dashboard_stats(self, *, uid: int, query: dict[str, str], **_: Any) -> Response:
        rel = await self._rel_filter(uid, query)
        return _json(200, await dd.stats(self.db_path, uid, rel))

    async def _relationships(self, *, uid: int, **_: Any) -> Response:
        rows = await dd.list_relationships(self.db_path, uid)
        return _json(200, {"relationships": rows, "count": len(rows)})

    async def _couple(
        self, *, uid: int, query: dict[str, str], params: tuple[str, ...], **_: Any,
    ) -> Response:
        """Couple-scoped lists. 404 (not 403) unless the caller is a member."""
        rel_id, what = params
        if await dd.user_relationship(self.db_path, uid, rel_id) is None:
            raise ApiError(404, "not_found")
        if what == "conflicts":
            return _json(200, {"conflicts": await dd.couple_conflicts(self.db_path, rel_id)})
        if what == "nudges":
            return _json(200, {"nudges": await dd.couple_nudges(self.db_path, rel_id, uid)})
        if what == "relays":
            return _json(200, {"relays": await dd.couple_relays(self.db_path, rel_id, uid)})
        _, limit = self._page(query, default_limit=50)
        return _json(200, {"facts": await dd.couple_facts(self.db_path, rel_id, limit)})

    # ── Admin (bearer auth AND X-Admin-Token == DASHBOARD_TOKEN) ──────

    def _require_admin(self, request: Request) -> None:
        expected = os.environ.get("DASHBOARD_TOKEN", "")
        if not expected:
            raise ApiError(403, "admin_disabled")
        given = request.headers.get("x-admin-token", "")
        if self.auth_fail_limiter.blocked(request.client_ip):
            raise ApiError(429, "too_many_requests")
        if not given or not secrets.compare_digest(
                given.encode("utf-8"), expected.encode("utf-8")):
            self.auth_fail_limiter.hit(request.client_ip)
            raise ApiError(403, "admin_required")

    async def _admin_stats(self, *, request: Request, **_: Any) -> Response:
        self._require_admin(request)
        return _json(200, await dd.admin_stats(self.db_path))

    async def _admin_users(self, *, request: Request, **_: Any) -> Response:
        self._require_admin(request)
        users = await dd.admin_users(self.db_path)
        return _json(200, {"users": users, "count": len(users)})

    async def _admin_pipeline(
        self, *, request: Request, params: tuple[str, ...], **_: Any,
    ) -> Response:
        self._require_admin(request)
        trace = await dd.admin_pipeline(self.db_path, int(params[0]))
        if trace is None:
            raise ApiError(404, "not_found")
        return _json(200, trace)

    async def _admin_dryrun(
        self, *, request: Request, body: dict[str, Any], **_: Any,
    ) -> Response:
        """Run the chat pipeline on *text* without saving anything."""
        self._require_admin(request)
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ApiError(400, "invalid_text")
        if len(text) > self.config.rest_max_text_chars:
            raise ApiError(400, "text_too_long")
        target = body.get("user_id")
        if target is not None:
            if not isinstance(target, int) or isinstance(target, bool):
                raise ApiError(400, "invalid_user_id")
            if await db_mod.get_user(self.db_path, target) is None:
                raise ApiError(404, "user_not_found")
        result = await self.service.dryrun_extraction(target, text.strip())
        return _json(200, result)

    # ── Dev sandbox (isolated temp DB for the duo page) ───────────────

    def _purge_sandboxes(self) -> None:
        if not SANDBOX_TTL_SECONDS:
            return  # no auto-expiry
        now = time.monotonic()
        for sid in [i for i, sb in self._sandboxes.items()
                    if now - sb.created > SANDBOX_TTL_SECONDS]:
            self._drop_sandbox(sid)

    def _drop_sandbox(self, sandbox_id: str) -> bool:
        sb = self._sandboxes.pop(sandbox_id, None)
        if sb is None:
            return False
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                os.unlink(sb.db_path + suffix)
            except FileNotFoundError:
                pass
        for table in (self._user_locks, self._wakeups):
            for key in [k for k in table if isinstance(k, tuple) and k[0] == sandbox_id]:
                del table[key]
        return True

    async def _sandbox_create(self, *, request: Request, query: dict[str, str], **_: Any) -> Response:
        if not self.sandbox_limiter.hit(request.client_ip):
            raise ApiError(429, "too_many_requests")

        # Persistent sandbox: ?keep=<id> reuses a fixed DB path
        keep_id = query.get("keep", "")
        if keep_id:
            sandbox_id = keep_id
            # Use a sibling of the main DB for persistence on any OS
            main_dir = os.path.dirname(os.path.abspath(self.db_path))
            path = os.path.join(main_dir, f"sandbox-{sandbox_id}.db")
        else:
            sandbox_id = uuid.uuid4().hex
            path = f"/tmp/pairtner-sandbox-{sandbox_id}.db"

        # If this sandbox already exists in memory, return existing tokens
        existing = self._sandboxes.get(sandbox_id)
        if existing:
            tokens = {}
            for uid in (1, 2):
                secret = secrets.token_urlsafe(32)
                await db_mod.create_auth_token(existing.db_path, uid, secret)
                tokens[uid] = f"{uid}:{secret}"
            return _json(200, {
                "sandbox_id": sandbox_id, "alice_token": tokens[1], "bob_token": tokens[2]})

        if len(self._sandboxes) >= MAX_SANDBOXES:
            raise ApiError(429, "too_many_sandboxes")
        from pairtner.storage.migrate_v2 import migrate_to_v2

        # For persistent sandboxes, DB may already exist on disk from a previous run
        db_exists = os.path.exists(path)
        try:
            await db_mod.init_db(path)
            await migrate_to_v2(path)
            if not db_exists:
                for uid, name in ((1, "Alice"), (2, "Bob")):
                    await db_mod.create_user(path, uid, name, "en")
                    await db_mod.set_password(path, uid, name.lower())
                # Users are NOT pre-paired — they go through /start + /join naturally
                await migrate_to_v2(path)
            tokens = {}
            for uid in (1, 2):
                secret = secrets.token_urlsafe(32)
                await db_mod.create_auth_token(path, uid, secret)
                tokens[uid] = f"{uid}:{secret}"
        except Exception:
            for suffix in ("", "-wal", "-shm", "-journal"):
                try:
                    os.unlink(path + suffix)
                except FileNotFoundError:
                    pass
            raise
        base = self._base_service
        cfg = dataclasses.replace(self.config, db_path=path)
        service = BotService(cfg, path, base.llm)
        service.privacy_info = base.privacy_info
        self._sandboxes[sandbox_id] = Sandbox(sandbox_id, path, service)
        return _json(201, {
            "sandbox_id": sandbox_id, "alice_token": tokens[1], "bob_token": tokens[2]})

    async def _sandbox_delete(self, *, params: tuple[str, ...], **_: Any) -> Response:
        if not self._drop_sandbox(params[0]):
            raise ApiError(404, "sandbox_not_found")
        return _json(200, {"deleted": params[0]})

    async def _delete_message(self, *, uid: int, body: dict[str, Any], **_: Any) -> Response:
        """Soft-delete a message by ID (must be owned by the user)."""
        msg_id = body.get("id")
        if not isinstance(msg_id, int) or msg_id < 1:
            raise ApiError(400, "invalid_id")
        deleted = await db_mod.soft_delete_message(self.db_path, uid, msg_id)
        if not deleted:
            raise ApiError(404, "not_found")
        return _json(200, {"deleted": msg_id})

    async def _model_get(self, *, uid: int, **_: Any) -> Response:
        model = await self.service._get_user_model(uid)
        # Include available providers
        inner = getattr(self.service.llm, '_inner', self.service.llm)
        providers = getattr(inner, 'providers', [])
        return _json(200, {
            "model": model or self.service._default_model_name(),
            "custom": bool(model),
            "providers": providers,
        })

    async def _model_put(self, *, uid: int, body: dict[str, Any], **_: Any) -> Response:
        raw = body.get("model")
        if raw is None or (isinstance(raw, str) and raw.strip().lower() in ("", "reset")):
            await self.service._set_user_model(uid, None)
        elif isinstance(raw, str) and len(raw.strip()) <= 100 and " " not in raw.strip():
            await self.service._set_user_model(uid, raw.strip())
        else:
            raise ApiError(400, "invalid_model")
        return await self._model_get(uid=uid)

    async def _export_therapy(self, *, uid: int, **_: Any) -> Response:
        """Therapist-facing markdown built from stored data (no LLM)."""
        from pairtner.features.therapy_export import build_therapy_notes

        user = await db_mod.get_user(self.db_path, uid)
        if user is None:
            raise ApiError(401, "unauthorized")
        if not user.get("partner_id"):
            raise ApiError(409, "not_paired")
        md = await build_therapy_notes(self.db_path, uid)
        if md is None:
            raise ApiError(404, "not_enough_data")
        return Response(200, md.encode("utf-8"), "text/markdown; charset=utf-8")

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
        if body.get("batched") is True:
            text = "[Offline batch] " + text
        async with self._lock(uid):
            user = await db_mod.get_user(self.db_path, uid)
            name = (user or {}).get("name") or f"User{uid}"
            token = msg_source.set("pwa")
            try:
                responses = await self.service.dispatch(uid, name, text)
            finally:
                msg_source.reset(token)
            return await self._reply(uid, responses)

    async def _callback(self, *, uid: int, body: dict[str, Any], **_: Any) -> Response:
        data = body.get("data")
        if not isinstance(data, str) or not data.strip() or len(data) > 256:
            raise ApiError(400, "invalid_data")
        async with self._lock(uid):
            token = msg_source.set("pwa")
            try:
                responses = await self.service.handle_callback(uid, data.strip())
            finally:
                msg_source.reset(token)
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

    # ── Push notification endpoints ──────────────────────────────────

    async def _push_vapid(self, **_: Any) -> Response:
        """Return the VAPID public key for client-side push subscription."""
        key = os.environ.get("VAPID_PUBLIC_KEY", "")
        if not key:
            raise ApiError(501, "push_not_configured")
        return _json(200, {"vapid_public_key": key})

    # ── Chat import ───────────────────────────────────────────────────
    async def _import_chat(
        self, *, uid: int, body: dict[str, Any], **_: Any,
    ) -> Response:
        """Import chat history and extract knowledge."""
        from pairtner.core.chat_import import import_chat
        messages = body.get("messages")
        if not messages:
            raise ApiError(400, "missing_messages")
        result = await import_chat(
            self._db_path, self._llm, uid, messages,
            partner_name=body.get("partner_name"),
            user_name=body.get("user_name"),
            dry_run=body.get("dry_run", False),
            consolidate=body.get("consolidate", True),
        )
        return _json(200, result)

    # ── Summary trigger ───────────────────────────────────────────────
    async def _summary_trigger(self, *, uid: int, **_: Any) -> Response:
        """Manually trigger summary generation for the authenticated user."""
        from pairtner.core.summary import run_periodic_summaries
        user = await db_mod.get_user(self.db_path, uid)
        if not user:
            raise ApiError(404, "user_not_found")
        # Find the user's relationships
        from pairtner.storage.helpers import _fetch_all
        rels = await _fetch_all(
            self.db_path,
            "SELECT id FROM relationships WHERE status='active' "
            "AND (user_a_id=? OR user_b_id=?)", (uid, uid),
        )
        created = {}
        for r in rels:
            result = await run_periodic_summaries(
                self.db_path, self._llm, uid, r["id"],
            )
            for k, v in result.items():
                created[k] = created.get(k, 0) + v
        return _json(200, {"created": created, "total": sum(created.values())})

    async def _push_subscribe(
        self, *, uid: int, body: dict[str, Any], **_: Any,
    ) -> Response:
        """Store a push subscription for the authenticated user."""
        from pairtner.push import PushSubscription, ensure_push_table, save_subscription

        sub = body.get("subscription")
        if not isinstance(sub, dict):
            raise ApiError(400, "missing_subscription")
        endpoint = sub.get("endpoint")
        keys = sub.get("keys", {})
        p256dh = keys.get("p256dh")
        auth = keys.get("auth")
        if not all([endpoint, p256dh, auth]):
            raise ApiError(400, "incomplete_subscription")

        await ensure_push_table(self.db_path)
        await save_subscription(
            self.db_path, uid,
            PushSubscription(endpoint=endpoint, p256dh=p256dh, auth=auth),
        )
        return _json(200, {"ok": True})

    async def _push_unsubscribe(
        self, *, uid: int, body: dict[str, Any], **_: Any,
    ) -> Response:
        """Remove a push subscription."""
        from pairtner.push import ensure_push_table, remove_subscription

        endpoint = body.get("endpoint")
        if not isinstance(endpoint, str):
            raise ApiError(400, "missing_endpoint")
        await ensure_push_table(self.db_path)
        await remove_subscription(self.db_path, endpoint)
        return _json(200, {"ok": True})
