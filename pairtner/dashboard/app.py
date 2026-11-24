"""HTTP app for the admin dashboard (stdlib only).

``DashboardApp.handle`` is a pure request -> response function (testable
without sockets); ``create_server`` wires it to ``ThreadingHTTPServer``.
No connection to the database is kept between requests.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator
from urllib.parse import parse_qs, unquote, urlsplit

import json

from pathlib import Path

from pairtner.dashboard import (
    admin_actions, explorer, overview_page, pipeline, stats, store, timeline_page,
)
from pairtner.dashboard.config import DashboardConfig
from pairtner.dashboard.render import (
    SECURITY_HEADERS, Html, cards, esc, layout, link, svg_bars, table,
)
from pairtner.dashboard.security import (
    BEARER_SESSION, COOKIE_NAME, Authenticator,
)
from pairtner.dashboard.store import NotFound
from pairtner.rest import timeline as timeline_api

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 16 * 1024
_TABLE_RE = re.compile(r"^/db/([A-Za-z0-9_]+)$")
_USER_RE = re.compile(r"^/users/(\d+)$")
_COUPLE_RE = re.compile(r"^/couples/(\d+:\d+)$")
_CLEAR_USER_RE = re.compile(r"^/admin/clear-user/(\d+)$")
_PIPELINE_FACT_RE = re.compile(r"^/pipeline/fact/(\d+)$")
_TIMELINE_PAGE_RE = re.compile(r"^/timeline/(\d+_\d+)$")
STATIC_DIR = Path(__file__).parent / "static"
_STATIC_TYPES = {
    "manifest.json": "application/manifest+json",
    "sw.js": "application/javascript; charset=utf-8",
    "pwa.js": "application/javascript; charset=utf-8",
    "icon-192.png": "image/png",
}
_TIMELINE_API_RE = re.compile(r"^/api/admin/timeline/([^/]+)$")


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    content_type: str = "text/html; charset=utf-8"
    headers: list[tuple[str, str]] = field(default_factory=list)


def _html(status: int, body: str, headers: list[tuple[str, str]] | None = None) -> Response:
    return Response(status, body.encode(), headers=headers or [])


def _redirect(location: str, headers: list[tuple[str, str]] | None = None,
              status: int = 302) -> Response:
    return Response(status, b"", headers=[("Location", location)] + (headers or []))


class DashboardApp:
    def __init__(self, config: DashboardConfig) -> None:
        self.config = config
        self.auth = Authenticator(config.token, config.session_ttl_hours * 3600)

    # ------------------------------------------------------------------
    # entry point
    # ------------------------------------------------------------------
    def handle(
        self, method: str, raw_path: str, headers: dict[str, str], body: bytes,
        client_ip: str,
    ) -> Response:
        parts = urlsplit(raw_path)
        path = unquote(parts.path)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        try:
            return self._route(method, path, query, headers, body, client_ip)
        except NotFound as exc:
            return self._error(404, f"Not found: {exc}")
        except FileNotFoundError:
            return self._error(503, "Database file not found. Is the bot's DB mounted?")
        except sqlite3.OperationalError as exc:
            logger.warning("database error: %s", exc)
            return self._error(503, "Database busy or unavailable. Retry shortly.")

    def _route(self, method, path, query, headers, body, client_ip) -> Response:
        if method not in ("GET", "POST"):
            return self._error(405, "Method not allowed")
        if path == "/healthz" and method == "GET":
            return Response(200, b"ok", "text/plain; charset=utf-8")
        if path == "/login":
            return self._login(method, body, client_ip)

        if path.startswith("/static/") and method == "GET":
            return self._static(path[len("/static/"):])

        session_id = self._authenticate(headers, client_ip)
        if session_id is None:
            if self.auth.is_locked(client_ip):
                return self._error(429, "Too many failed attempts. Try later.")
            if method == "GET" and "authorization" not in headers:
                return _redirect("/login")
            return Response(401, b"unauthorized", "text/plain; charset=utf-8",
                            [("WWW-Authenticate", "Bearer")])
        ctx = _Ctx(session_id, self.auth.csrf_token(session_id))

        if method == "POST":
            form = self._form(body)
            if form is None:
                return self._error(413, "Request body too large")
            return self._post(path, form, ctx)
        return self._get(path, query, ctx)

    @staticmethod
    def _static(name: str) -> Response:
        """Public PWA assets (fixed allowlist; no secrets, no path traversal)."""
        ctype = _STATIC_TYPES.get(name)
        if ctype is None:
            raise NotFound(f"/static/{name}")
        headers = [("Service-Worker-Allowed", "/")] if name == "sw.js" else []
        return Response(200, (STATIC_DIR / name).read_bytes(), ctype, headers)

    # ------------------------------------------------------------------
    # auth
    # ------------------------------------------------------------------
    def _authenticate(self, headers: dict[str, str], client_ip: str) -> str | None:
        if self.auth.is_locked(client_ip):
            return None
        authz = headers.get("authorization", "")
        if authz:
            scheme, _, cred = authz.partition(" ")
            if scheme.lower() == "bearer" and self.auth.check_token(cred.strip()):
                return BEARER_SESSION
            self.auth.record_failure(client_ip)
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("cookie", ""))
        except Exception:  # http.cookies raises CookieError on malformed input
            return None
        morsel = cookie.get(COOKIE_NAME)
        if morsel and self.auth.verify_session(morsel.value):
            return morsel.value
        return None

    def _login(self, method: str, body: bytes, client_ip: str) -> Response:
        if method == "GET":
            return _html(200, self._login_page())
        if self.auth.is_locked(client_ip):
            return self._error(429, "Too many failed attempts. Try later.")
        form = self._form(body)
        if form is None:
            return self._error(413, "Request body too large")
        if self.auth.check_token(form.get("token", "")):
            self.auth.record_success(client_ip)
            max_age = int(self.config.session_ttl_hours * 3600)
            cookie = (
                f"{COOKIE_NAME}={self.auth.issue_session()}; HttpOnly; "
                f"SameSite=Strict; Path=/; Max-Age={max_age}"
            )
            return _redirect("/", [("Set-Cookie", cookie)], status=303)
        self.auth.record_failure(client_ip)
        return _html(401, self._login_page("Wrong token."))

    @staticmethod
    def _login_page(error: str = "") -> str:
        msg = f'<p class="err">{esc(error)}</p>' if error else ""
        return layout(
            "Log in",
            f'{msg}<form method="post" action="/login">'
            '<input type="password" name="token" placeholder="dashboard token" '
            'autocomplete="current-password" autofocus> <button>Log in</button></form>',
            show_nav=False,
        )

    @staticmethod
    def _form(body: bytes) -> dict[str, str] | None:
        if len(body) > MAX_BODY_BYTES:
            return None
        return {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @contextmanager
    def _ro(self) -> Iterator[sqlite3.Connection]:
        conn = store.open_readonly(self.config.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def _error(self, status: int, message: str) -> Response:
        return _html(status, layout(f"Error {status}", f'<p class="err">{esc(message)}</p>',
                                    show_nav=False))

    def _page(self, ctx: "_Ctx", title: str, body: str, active: str = "") -> Response:
        banner = ""
        if not self.config.show_raw_text:
            banner = ('<p class="warn">Raw text is hidden '
                      "(<code>dashboard.show_raw_text: false</code>): counts and "
                      "metadata only.</p>")
        return _html(200, layout(title, banner + body, active, ctx.csrf))

    # ------------------------------------------------------------------
    # GET pages
    # ------------------------------------------------------------------
    def _get(self, path: str, query: dict[str, str], ctx: "_Ctx") -> Response:
        raw = self.config.show_raw_text
        if path == "/":
            return self._overview(ctx)
        if path == "/usage":
            return self._usage(ctx)
        if path == "/users":
            with self._ro() as c:
                rows = stats.user_activity(c, raw)
            cols = ["user", "name", "partner", "messages", "facts", "relays sent",
                    "relays received", "goals", "last seen"]
            data = [[link(f"/users/{r['tg_id']}", r["tg_id"]), r["name"], r["partner_id"],
                     r["messages"], r["facts"], r["relays_sent"], r["relays_received"],
                     r["goals"], r["last_seen"]] for r in rows]
            return self._page(ctx, "Users", table(cols, data), "/users")
        if m := _USER_RE.match(path):
            return self._user(ctx, int(m.group(1)))
        if path == "/couples":
            return self._couples(ctx)
        if m := _COUPLE_RE.match(path):
            return self._couple(ctx, m.group(1))
        if path.startswith("/conversations/"):
            couple_id = path[len("/conversations/"):]
            return self._conversation(ctx, couple_id)
        if path == "/queue":
            return self._queue(ctx)
        if path == "/pipeline":
            return self._pipeline(ctx, query)
        if m := _PIPELINE_FACT_RE.match(path):
            return self._pipeline_fact(ctx, int(m.group(1)))
        if m := _TIMELINE_PAGE_RE.match(path):
            return self._timeline_page(ctx, m.group(1), query)
        if m := _TIMELINE_API_RE.match(path):
            return self._timeline_json(m.group(1), query)
        if path == "/db":
            with self._ro() as c:
                tables = explorer.tables(c)
            data = [[link(f"/db/{t['name']}", t["name"]), t["rows"]] for t in tables]
            return self._page(ctx, "SQLite explorer (read-only)",
                              table(["table", "rows"], data), "/db")
        if m := _TABLE_RE.match(path):
            return self._table(ctx, m.group(1), query)
        if path == "/admin":
            return self._admin(ctx)
        if path == "/admin/config":
            return self._admin_config(ctx)
        if not self.config.admin_actions_enabled and path.startswith("/admin/"):
            return self._error(403, "Admin actions are disabled")
        if path == "/admin/clear-user":
            uid = store.parse_user_id(query.get("user_id", "").strip())
            return _redirect(f"/admin/clear-user/{uid}")
        if m := _CLEAR_USER_RE.match(path):
            return self._clear_user_preview(ctx, int(m.group(1)))
        if path == "/admin/clear-all":
            return self._clear_all_preview(ctx)
        raise NotFound(path)

    def _overview(self, ctx: "_Ctx") -> Response:
        raw = self.config.show_raw_text
        with self._ro() as c:
            o = stats.overview(c)
            health = stats.health_summary(c)
            events = stats.recent_activity(c, raw, 10)
        def kv(d: dict) -> str:
            return ", ".join(f"{k}: {v}" for k, v in sorted(d.items())) or "none"
        body = overview_page.render_overview(o, health, events) + cards([
            ("users", o["users"]), ("messages 7d", o["messages_7d"]),
            ("LLM calls today", o["usage_today"]),
        ]) + table(["queue / state", "counts"], [
            ["relays by status", kv(o["pending_by_status"])],
            ["batches by status", kv(o["batches_by_status"])],
            ["conflicts by status", kv(o["conflicts_by_status"])],
            ["user goals by status", kv(o["user_goals_by_status"])],
            ["shared goals by status", kv(o["shared_goals_by_status"])],
        ])
        return self._page(ctx, "Overview", body, "/")

    def _usage(self, ctx: "_Ctx") -> Response:
        with self._ro() as c:
            msgs = stats.messages_per_day(c, 14)
            usage = stats.usage_per_model_per_day(c, 14)
        per_day: dict[str, int] = {}
        for u in usage:
            per_day[u["date"]] = per_day.get(u["date"], 0) + u["count"]
        body = (
            "<h2>Messages per day (14d)</h2>"
            + svg_bars([(m["day"], m["count"]) for m in msgs])
            + table(["day", "messages", "from users"],
                    [[m["day"], m["count"], m["from_users"]] for m in reversed(msgs)])
            + "<h2>LLM calls per day (14d)</h2>"
            + svg_bars(sorted(per_day.items()))
            + "<h2>Per model per day</h2>"
            + table(["date", "model", "calls"],
                    [[u["date"], u["model"], u["count"]] for u in usage])
        )
        return self._page(ctx, "Usage", body, "/usage")

    @staticmethod
    def _timeline_table(events: list[dict]) -> str:
        return table(["at (UTC)", "kind", "what", "text"],
                     [[e["at"], e["kind"], e["label"], e["text"]] for e in events])

    def _user(self, ctx: "_Ctx", user_id: int) -> Response:
        raw = self.config.show_raw_text
        with self._ro() as c:
            events = stats.user_timeline(c, user_id, raw)
            row = next(r for r in stats.user_activity(c, raw) if r["tg_id"] == user_id)
        body = cards([
            ("messages", row["messages"]), ("facts", row["facts"]),
            ("relays sent", row["relays_sent"]), ("relays received", row["relays_received"]),
            ("goals", row["goals"]), ("last seen", row["last_seen"] or "never"),
        ])
        partner = row["partner_id"]
        if partner:
            cid = f"{min(user_id, partner)}:{max(user_id, partner)}"
            body += f"<p>Partner: {link(f'/users/{partner}', partner)} · couple {link(f'/couples/{cid}', cid)}</p>"
        body += "<h2>Timeline</h2>" + self._timeline_table(events)
        return self._page(ctx, f"User {user_id}", body, "/users")

    def _couples(self, ctx: "_Ctx") -> Response:
        raw = self.config.show_raw_text
        with self._ro() as c:
            couples = stats.couples(c)
            conflicts = stats.conflicts(c, raw)
            goals = stats.goals(c, raw)
        body = table(
            ["couple", "open conflicts", "timeline events", "shared goals"],
            [[link(f"/couples/{x['couple_id']}", x["couple_id"]), x["open_conflicts"],
              x["timeline_events"], x["goals"]] for x in couples],
        ) + "<h2>Conflicts</h2>" + table(
            ["id", "couple", "theme", "status", "opened", "resolved"],
            [[x["id"], x["couple_id"], x["theme"], x["status"], x["opened_at"],
              x["resolved_at"]] for x in conflicts],
        ) + "<h2>Shared goals</h2>" + table(
            ["id", "user", "text", "status", "progress"],
            [[x["id"], x.get("user_id", ""), x["text"], x["status"], x["progress"]]
             for x in goals["goals"]],
        )
        return self._page(ctx, "Couples", body, "/couples")

    def _couple(self, ctx: "_Ctx", couple_id: str) -> Response:
        raw = self.config.show_raw_text
        a, b = stats.parse_couple_id(couple_id)
        with self._ro() as c:
            events = stats.couple_timeline(c, couple_id, raw)
            partners = {r["tg_id"]: r for r in stats.user_activity(c, raw)
                        if r["tg_id"] in (a, b)}
            health = stats.latest_health(c, couple_id)
            quick = stats.couple_quick_stats(c, couple_id)
        body = overview_page.render_couple(
            couple_id, a, b, partners, health, quick, self._timeline_table(events))
        return self._page(ctx, f"Couple {couple_id}", body, "/couples")

    def _conversation(self, ctx: "_Ctx", couple_id: str) -> Response:
        """Render the full conversation between a couple as a chat view."""
        a, b = stats.parse_couple_id(couple_id)
        with self._ro() as c:
            messages = stats.couple_conversation(c, a, b, self.config.show_raw_text)
            user_a = stats.user_name(c, a)
            user_b = stats.user_name(c, b)

        body = (
            f"<p>{link(f'/couples/{couple_id}', '← Back to couple')}</p>"
            f"<p><b>{esc(user_a)}</b> ↔ <b>{esc(user_b)}</b></p>"
            '<div class="conversation">'
        )

        for m in messages:
            uid = m["user_id"]
            role = m["role"]
            name = m.get("user_name") or f"User {uid}"
            content = esc(m.get("content") or "")
            ts = m.get("created_at") or ""

            if role == "user":
                sender = esc(name)
                css = "msg-user-a" if uid == a else "msg-user-b"
            else:
                sender = f"🤖 pAIrtner → {esc(name)}"
                css = "msg-bot"

            body += (
                f'<div class="chat-msg {css}">'
                f'<div class="chat-sender">{sender}</div>'
                f'<div class="chat-text">{content}</div>'
                f'<div class="chat-ts">{esc(ts)}</div>'
                f'</div>'
            )

        body += '</div>'

        # Add inline styles for the conversation view
        style = """
        <style>
        .conversation { max-width: 720px; }
        .chat-msg { padding: 8px 12px; margin: 4px 0; border-radius: 12px; }
        .msg-user-a { background: var(--me, #2b5cff); color: #fff; margin-right: 20%; }
        .msg-user-b { background: var(--relay, #efe3fa); color: var(--text, #1c1f24); margin-left: 20%; }
        .msg-bot { background: var(--bot, #e6e9f2); color: var(--text, #1c1f24); margin-left: 10%; margin-right: 10%; opacity: 0.85; }
        .chat-sender { font-size: 12px; font-weight: bold; margin-bottom: 2px; }
        .msg-user-a .chat-sender { color: rgba(255,255,255,0.8); }
        .msg-user-b .chat-sender { color: var(--muted, #6b7280); }
        .msg-bot .chat-sender { color: var(--muted, #6b7280); }
        .chat-text { white-space: pre-wrap; word-wrap: break-word; }
        .chat-ts { font-size: 11px; opacity: 0.6; margin-top: 2px; }
        </style>
        """

        return self._page(ctx, f"Conversation: {user_a} ↔ {user_b}",
                          style + body, "/couples")

    def _timeline_events(self, rid: str, query: dict[str, str]):
        """Shared by the JSON endpoint and the page: (events, limit, before, has_more)."""
        if not timeline_api.REL_RE.match(rid):
            raise NotFound(f"bad relationship id: {rid!r}")
        limit = timeline_api.parse_limit(query.get("limit"))
        before = timeline_api.parse_before(query.get("before"))
        types = timeline_api.parse_types(query.get("types"))
        try:
            with self._ro() as c:
                if not timeline_api.relationship_exists(c, rid):
                    raise NotFound(f"no such relationship: {rid}")
                events = timeline_api.build_timeline(
                    c, rid, limit=limit + 1, before=before, types=types,
                    show_raw=self.config.show_raw_text)
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc) or "no such column" in str(exc):
                raise NotFound("timeline needs the v2 schema") from exc
            raise
        has_more = len(events) > limit
        return events[-limit:] if has_more else events, limit, before, has_more

    def _timeline_json(self, rid: str, query: dict[str, str]) -> Response:
        try:
            events, _, _, _ = self._timeline_events(rid, query)
        except timeline_api.BadRequest as exc:
            return Response(400, json.dumps({"error": str(exc)}).encode(),
                            "application/json; charset=utf-8")
        except NotFound as exc:
            return Response(404, json.dumps({"error": str(exc)}).encode(),
                            "application/json; charset=utf-8")
        return Response(200, json.dumps(events).encode(), "application/json; charset=utf-8")

    def _timeline_page(self, ctx: "_Ctx", rid: str, query: dict[str, str]) -> Response:
        try:
            events, limit, before, has_more = self._timeline_events(rid, query)
        except timeline_api.BadRequest as exc:
            return self._error(400, str(exc))
        body = timeline_page.render_timeline(
            rid, events, limit=limit, before=before, has_more=has_more)
        return self._page(ctx, f"Timeline {rid}", body, "/couples")

    def _pipeline(self, ctx: "_Ctx", query: dict[str, str]) -> Response:
        limit = pipeline.parse_limit(query.get("limit"))
        raw_uid = query.get("user_id", "").strip()
        user_id = store.parse_user_id(raw_uid) if raw_uid else None
        before = query.get("before", "").strip()
        before_id = int(before) if before.isdigit() else None
        with self._ro() as c:
            data = pipeline.build_pipeline(
                c, limit=limit, before_id=before_id, user_id=user_id,
                show_raw=self.config.show_raw_text)
        body = pipeline.render_pipeline(data, user_id=user_id, limit=limit)
        return self._page(ctx, "Pipeline debug", body, "/pipeline")

    def _pipeline_fact(self, ctx: "_Ctx", fact_id: int) -> Response:
        with self._ro() as c:
            trace = pipeline.build_fact_trace(
                c, fact_id, show_raw=self.config.show_raw_text)
        return self._page(ctx, f"Fact {fact_id} trace",
                          pipeline.render_fact_trace(trace), "/pipeline")

    def _queue(self, ctx: "_Ctx") -> Response:
        with self._ro() as c:
            q = stats.queue(c, self.config.show_raw_text)
        body = "<h2>Pending relays</h2>" + table(
            ["id", "from", "to", "status", "send after", "created", "length", "draft"],
            [[r["id"], r["sender_id"], r["receiver_id"], r["status"], r["send_after"],
              r["created_at"], r["draft_len"], r["draft"]] for r in q["relays"]],
        ) + "<h2>Pending batches</h2>" + table(
            ["id", "from", "to", "status", "send after", "created", "items"],
            [[r["id"], r["sender_id"], r["receiver_id"], r["status"], r["send_after"],
              r["created_at"], r["items"]] for r in q["batches"]],
        )
        return self._page(ctx, "Queue", body, "/queue")

    def _table(self, ctx: "_Ctx", name: str, query: dict[str, str]) -> Response:
        def to_int(key: str, default: int) -> int:
            try:
                return int(query.get(key, default))
            except ValueError:
                return default
        page, size = to_int("page", 1), to_int("size", self.config.page_size)
        with self._ro() as c:
            sch = explorer.schema(c, name)
            data = explorer.rows(c, name, page, size, self.config.show_raw_text)
        nav = []
        if data["page"] > 1:
            nav.append(link(f"/db/{name}?page={data['page'] - 1}&size={data['size']}", "‹ prev"))
        nav.append(Html(esc(f"page {data['page']} / {data['pages']} ({data['total']} rows)")))
        if data["page"] < data["pages"]:
            nav.append(link(f"/db/{name}?page={data['page'] + 1}&size={data['size']}", "next ›"))
        body = (
            f"<p>{' · '.join(esc(n) for n in nav)}</p>"
            + table(data["columns"], data["rows"])
            + "<h2>Schema</h2>"
            + table(["column", "type", "not null", "default", "pk"],
                    [[c["name"], c["type"], c["notnull"], c["default"], c["pk"]]
                     for c in sch["columns"]])
            + table(["index", "unique", "columns"],
                    [[i["name"], i["unique"], ", ".join(i["columns"])] for i in sch["indexes"]])
            + f"<pre>{esc(sch['ddl'])}</pre>"
        )
        return self._page(ctx, f"Table {name}", body, "/db")

    # ------------------------------------------------------------------
    # admin
    # ------------------------------------------------------------------
    def _admin(self, ctx: "_Ctx") -> Response:
        backups = table(["file", "bytes", "modified (UTC)"],
                        [[b["name"], b["size"], b["modified"]]
                         for b in admin_actions.list_backups(self.config.backup_dir)])
        if not self.config.admin_actions_enabled:
            return self._page(ctx, "Admin",
                              '<p class="warn">Admin actions are disabled '
                              "(<code>dashboard.admin_actions_enabled: false</code>).</p>"
                              "<h2>Backups</h2>" + backups, "/admin")
        body = (
            "<h2>Backup now</h2>"
            f'<form method="post" action="/admin/backup">{self._csrf_field(ctx)}'
            "<button>Create backup</button></form>"
            "<h2>Clear one user</h2>"
            '<form method="get" action="/admin/clear-user">'
            '<input type="text" name="user_id" placeholder="user id"> '
            "<button>Preview</button></form>"
            "<h2>Clear the whole database</h2>"
            '<p><a href="/admin/clear-all">Preview and confirm…</a></p>'
            "<h2>Runtime Config</h2>"
            '<p><a href="/admin/config">Edit runtime settings (model, provider, intervals)…</a></p>'
            "<h2>Backups</h2>" + backups
        )
        return self._page(ctx, "Admin", body, "/admin")

    def _admin_config(self, ctx: "_Ctx") -> Response:
        """Admin config page — runtime settings that override static config."""
        settings: dict[str, str] = {}
        try:
            conn = sqlite3.connect(self.config.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT key, value FROM user_meta WHERE user_id = 0").fetchall()
            settings = {r["key"]: r["value"] for r in rows}
            conn.close()
        except Exception:
            pass

        # Known settings with defaults
        known = {
            "llm_provider": ("LLM Provider", "gemini", "gemini,openai_compat"),
            "model_default": ("Default Model", "gemini-3.1-flash-lite", ""),
            "model_heavy": ("Heavy Model", "gemini-3-flash-preview", ""),
            "analysis_interval": ("Analysis Interval (messages)", "10", ""),
            "cooldown_minutes": ("Relay Cooldown (minutes)", "5", ""),
        }

        rows_html = ""
        for key, (label, default, options) in known.items():
            current = settings.get(key, "")
            placeholder = f"default: {default}"
            if options:
                opts = "".join(
                    f'<option value="{esc(o)}" {"selected" if current == o else ""}>{esc(o)}</option>'
                    for o in options.split(",")
                )
                field = f'<select name="{esc(key)}">' \
                        f'<option value="">({placeholder})</option>{opts}</select>'
            else:
                field = (f'<input type="text" name="{esc(key)}" '
                         f'value="{esc(current)}" placeholder="{esc(placeholder)}">')
            rows_html += f"<tr><td>{esc(label)}</td><td>{field}</td></tr>"

        body = (
            '<form method="post" action="/admin/config">'
            f'{self._csrf_field(ctx)}'
            '<table><tr><th>Setting</th><th>Value</th></tr>'
            f'{rows_html}'
            '</table>'
            '<button style="margin-top: 12px">Save Settings</button>'
            '</form>'
            '<p style="margin-top: 12px; color: var(--muted)">Empty values use the '
            'static config from settings.yaml / environment variables.</p>'
        )
        return self._page(ctx, "Runtime Config", body, "/admin")

    def _save_config(self, form: dict[str, str], ctx: "_Ctx") -> Response:
        """Save runtime settings from the admin config form."""
        import sqlite3
        known_keys = {"llm_provider", "model_default", "model_heavy",
                      "analysis_interval", "cooldown_minutes"}
        conn = sqlite3.connect(self.config.db_path)
        for key in known_keys:
            value = form.get(key, "").strip()
            if value:
                conn.execute(
                    "INSERT OR REPLACE INTO user_meta (user_id, key, value, updated_at) "
                    "VALUES (0, ?, ?, CURRENT_TIMESTAMP)",
                    (key, value),
                )
            else:
                conn.execute(
                    "DELETE FROM user_meta WHERE user_id = 0 AND key = ?",
                    (key,),
                )
        conn.commit()
        conn.close()
        return _redirect("/admin/config")

    @staticmethod
    def _csrf_field(ctx: "_Ctx") -> str:
        return f'<input type="hidden" name="csrf" value="{esc(ctx.csrf)}">'

    def _confirm_form(self, ctx: "_Ctx", action: str, phrase: str) -> str:
        return (
            f'<form method="post" action="{esc(action)}">{self._csrf_field(ctx)}'
            f"<p>A backup is taken first. Type <code>{esc(phrase)}</code> to confirm:</p>"
            '<input type="text" name="confirmation" autocomplete="off"> '
            "<button>Delete</button></form>"
        )

    def _clear_user_preview(self, ctx: "_Ctx", user_id: int) -> Response:
        counts = admin_actions.preview_clear_user(self.config.db_path, user_id)
        body = ("<p class=warn>These rows will be deleted:</p>"
                + table(["table", "rows"], sorted(counts.items()))
                + self._confirm_form(ctx, f"/admin/clear-user/{user_id}",
                                     admin_actions.user_confirmation_phrase(user_id)))
        return self._page(ctx, f"Clear user {user_id}", body, "/admin")

    def _clear_all_preview(self, ctx: "_Ctx") -> Response:
        counts = admin_actions.preview_clear_all(self.config.db_path)
        body = ("<p class=warn>Every row in every table will be deleted:</p>"
                + table(["table", "rows"], sorted(counts.items()))
                + self._confirm_form(ctx, "/admin/clear-all", admin_actions.CLEAR_ALL_PHRASE))
        return self._page(ctx, "Clear whole database", body, "/admin")

    def _post(self, path: str, form: dict[str, str], ctx: "_Ctx") -> Response:
        if path == "/logout":
            if not self.auth.verify_csrf(ctx.session_id, form.get("csrf")):
                return self._error(403, "Bad CSRF token")
            return _redirect("/login", [(
                "Set-Cookie", f"{COOKIE_NAME}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")],
                status=303)
        if path not in ("/admin/backup", "/admin/clear-all", "/admin/config") and not _CLEAR_USER_RE.match(path):
            raise NotFound(path)
        if not self.config.admin_actions_enabled:
            return self._error(403, "Admin actions are disabled")
        if not self.auth.verify_csrf(ctx.session_id, form.get("csrf")):
            return self._error(403, "Bad CSRF token")
        cfg = self.config
        try:
            if path == "/admin/config":
                return self._save_config(form, ctx)
            if path == "/admin/backup":
                result = admin_actions.backup_now(cfg.db_path, cfg.backup_dir)
            elif path == "/admin/clear-all":
                result = admin_actions.clear_all(
                    cfg.db_path, cfg.backup_dir, form.get("confirmation", ""))
            else:
                uid = int(_CLEAR_USER_RE.match(path).group(1))
                result = admin_actions.clear_user(
                    cfg.db_path, cfg.backup_dir, uid, form.get("confirmation", ""))
        except admin_actions.ConfirmationError as exc:
            return _html(400, layout("Confirmation required",
                                     f'<p class="err">Nothing was changed. {esc(exc)}</p>',
                                     "/admin", ctx.csrf))
        except admin_actions.BackupError as exc:
            return _html(500, layout("Backup failed",
                                     f'<p class="err">Nothing was deleted. {esc(exc)}</p>',
                                     "/admin", ctx.csrf))
        body = (f'<p class="ok">Done: {esc(result.action)}. Backup: '
                f"<code>{esc(os.path.basename(result.backup_path))}</code></p>"
                + (table(["table", "rows"], sorted(result.counts.items()))
                   if result.counts else ""))
        return self._page(ctx, "Admin action complete", body, "/admin")


@dataclass
class _Ctx:
    session_id: str
    csrf: str


# ----------------------------------------------------------------------
# server wiring
# ----------------------------------------------------------------------

def _make_handler(app: DashboardApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "pairtner-dashboard"
        sys_version = ""

        def _serve(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                body = b"x" * (MAX_BODY_BYTES + 1)  # signals "too large" without reading it all
            else:
                body = self.rfile.read(length) if length else b""
            headers = {k.lower(): v for k, v in self.headers.items()}
            try:
                resp = app.handle(method, self.path, headers, body, self.client_address[0])
            except Exception:  # last-resort boundary: log, return generic 500
                logger.exception("unhandled error serving %s", urlsplit(self.path).path)
                resp = app._error(500, "Internal error")
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.content_type)
            self.send_header("Content-Length", str(len(resp.body)))
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            for k, v in resp.headers:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(resp.body)

        def do_GET(self) -> None:  # noqa: N802
            self._serve("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._serve("POST")

        def do_PUT(self) -> None:  # noqa: N802
            self._serve("PUT")

        do_DELETE = do_PATCH = do_PUT

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            # Log method + path only; never query strings or headers.
            logger.info("%s %s -> %s", self.command, urlsplit(self.path).path, args[1] if len(args) > 1 else "")

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True


def create_server(config: DashboardConfig) -> ThreadingHTTPServer:
    app = DashboardApp(config)
    server = _Server((config.host, config.port), _make_handler(app))
    server.app = app  # type: ignore[attr-defined]
    return server
