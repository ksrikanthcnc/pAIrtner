"""End-to-end tests: real HTTP server on an ephemeral port."""
from __future__ import annotations

import hashlib
import http.client
import os
import re
import threading
from urllib.parse import urlencode

import pytest

from pairtner.dashboard.app import create_server
from pairtner.dashboard.config import DashboardConfig
from tests.dashboard_seed import AUTH_CANARY, DASH_TOKEN, KEY_CANARY, seed_db

ENV_SECRETS = {
    "GEMINI_API_KEY": "gem-key-CANARY-ENV-111",
    "TELEGRAM_BOT_TOKEN": "tg-token-CANARY-ENV-222",
    "DB_ENCRYPTION_KEY": "enc-key-CANARY-ENV-333",
    "LLM_API_KEY": "llm-key-CANARY-ENV-444",
}


class Client:
    def __init__(self, port: int):
        self.port = port
        self.cookie: str | None = None

    def request(self, method, path, body=None, headers=None, auth=True):
        h = dict(headers or {})
        if self.cookie and "Cookie" not in h:
            h["Cookie"] = self.cookie
        data = None
        if body is not None:
            data = urlencode(body)
            h["Content-Type"] = "application/x-www-form-urlencoded"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        text = r.read().decode("utf-8", "replace")
        out = (r.status, text, r.getheaders())
        conn.close()
        return out

    def bearer(self, path, method="GET", body=None):
        return self.request(method, path, body, {"Authorization": f"Bearer {DASH_TOKEN}"})

    def login(self):
        status, _, headers = self.request("POST", "/login", {"token": DASH_TOKEN})
        assert status == 303
        cookie = next(v for k, v in headers if k == "Set-Cookie")
        self.cookie = cookie.split(";")[0]

    def csrf(self, path="/admin"):
        _, text, _ = self.request("GET", path)
        return re.search(r'name="csrf" value="([^"]+)"', text).group(1)


def start(tmp_path, monkeypatch, **overrides):
    for k, v in ENV_SECRETS.items():
        monkeypatch.setenv(k, v)
    db = seed_db(str(tmp_path / "dash.db"))
    cfg = DashboardConfig(
        token=DASH_TOKEN, db_path=db, backup_dir=str(tmp_path / "backups"),
        host="127.0.0.1", port=0, **overrides,
    )
    server = create_server(cfg)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, cfg, Client(server.server_address[1])


@pytest.fixture
def dash(tmp_path, monkeypatch):
    server, cfg, client = start(tmp_path, monkeypatch)
    client.login()
    yield cfg, client
    server.shutdown()
    server.server_close()


@pytest.fixture
def dash_no_raw(tmp_path, monkeypatch):
    server, cfg, client = start(tmp_path, monkeypatch, show_raw_text=False)
    client.login()
    yield cfg, client
    server.shutdown()
    server.server_close()


def digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def all_pages(client) -> list[str]:
    pages = ["/", "/usage", "/users", "/users/1", "/users/3", "/couples", "/couples/1:2",
             "/couples/3:4", "/queue", "/db", "/admin", "/admin/clear-all",
             "/admin/clear-user/1"]
    _, text, _ = client.request("GET", "/db")
    tables = re.findall(r'href="/db/([a-z_]+)"', text)
    assert "auth_tokens" in tables and "relationship_config" in tables
    pages += [f"/db/{t}" for t in tables] + [f"/db/{t}?page=2&size=1" for t in tables]
    return pages


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TestAuth:
    def test_unauthenticated_requests_blocked_everywhere(self, tmp_path, monkeypatch):
        server, _, client = start(tmp_path, monkeypatch)
        try:
            for path in ["/", "/usage", "/users", "/users/1", "/couples", "/queue", "/db",
                         "/db/users", "/admin", "/admin/clear-all", "/nonexistent"]:
                status, body, _ = client.request("GET", path)
                assert status in (302, 401), path
                assert "CANARY" not in body
            for path in ["/admin/backup", "/admin/clear-all", "/logout"]:
                assert client.request("POST", path, {})[0] == 401
            assert client.request("GET", "/healthz")[:2] == (200, "ok")
            assert client.request("GET", "/login")[0] == 200
        finally:
            server.shutdown()
            server.server_close()

    def test_wrong_token_and_wrong_bearer(self, tmp_path, monkeypatch):
        server, _, client = start(tmp_path, monkeypatch)
        try:
            assert client.request("POST", "/login", {"token": "nope"})[0] == 401
            status, _, _ = client.request("GET", "/", headers={"Authorization": "Bearer nope"})
            assert status == 401
            assert client.request("GET", "/", headers={"Cookie": "pd_session=1.abc"})[0] == 302
        finally:
            server.shutdown()
            server.server_close()

    def test_bearer_and_cookie_both_work(self, dash):
        _, client = dash
        assert client.request("GET", "/")[0] == 200
        assert client.bearer("/")[0] == 200

    def test_cookie_flags_and_no_token_in_cookie(self, tmp_path, monkeypatch):
        server, _, client = start(tmp_path, monkeypatch)
        try:
            _, _, headers = client.request("POST", "/login", {"token": DASH_TOKEN})
            cookie = next(v for k, v in headers if k == "Set-Cookie")
            assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
            assert DASH_TOKEN not in cookie
        finally:
            server.shutdown()
            server.server_close()

    def test_lockout_after_repeated_failures(self, tmp_path, monkeypatch):
        server, _, client = start(tmp_path, monkeypatch)
        try:
            for _ in range(5):
                client.request("POST", "/login", {"token": "bad"})
            assert client.request("POST", "/login", {"token": DASH_TOKEN})[0] == 429
        finally:
            server.shutdown()
            server.server_close()

    def test_logout_clears_cookie(self, dash):
        _, client = dash
        status, _, headers = client.request("POST", "/logout", {"csrf": client.csrf("/")})
        assert status == 303
        assert "Max-Age=0" in next(v for k, v in headers if k == "Set-Cookie")
        assert client.request("POST", "/logout", {})[0] == 403  # still valid cookie, no csrf

    def test_security_headers(self, dash):
        _, client = dash
        _, _, headers = client.request("GET", "/")
        h = {k.lower(): v for k, v in headers}
        assert h["cache-control"] == "no-store"
        assert h["x-frame-options"] == "DENY"
        assert "default-src 'none'" in h["content-security-policy"]


# ---------------------------------------------------------------------------
# Pages and privacy
# ---------------------------------------------------------------------------

class TestPages:
    def test_every_page_ok_and_has_content(self, dash):
        _, client = dash
        for path in all_pages(client):
            status, body, _ = client.request("GET", path)
            assert status == 200, path
        _, body, _ = client.request("GET", "/")
        assert "messages total" in body

    def test_stats_values_visible(self, dash):
        _, client = dash
        _, usage, _ = client.request("GET", "/usage")
        assert "model-a" in usage and "model-b" in usage and "<svg" in usage
        _, users, _ = client.request("GET", "/users")
        assert "CANARY-RAW-name-1" in users
        _, q, _ = client.request("GET", "/queue")
        assert "CANARY-RAW-draft-12" in q
        _, c, _ = client.request("GET", "/couples/1:2")
        assert "CANARY-RAW-timeline-12" in c and "CANARY-RAW-draft-34" not in c

    def test_explorer_paging(self, dash):
        _, client = dash
        _, body, _ = client.request("GET", "/db/users?page=2&size=2")
        assert "page 2 / 3" in body and "CREATE TABLE" in body

    def test_unknown_pages_404(self, dash):
        _, client = dash
        for path in ["/db/nope", "/db/users%3BDROP", "/db/facts_fts", "/users/99999",
                     "/couples/2:1", "/couples/1:99", "/nothing"]:
            status, _, _ = client.request("GET", path)
            assert status == 404, path

    @pytest.mark.parametrize("fixture_name", ["dash", "dash_no_raw"])
    def test_secrets_never_rendered(self, request, fixture_name):
        cfg, client = request.getfixturevalue(fixture_name)
        secrets = [AUTH_CANARY, KEY_CANARY, DASH_TOKEN, *ENV_SECRETS.values(), "CANARY-SECRET"]
        for path in all_pages(client):
            for fetch in (client.request, lambda m, p: client.bearer(p)):
                status, body, headers = fetch("GET", path)
                blob = body + repr(headers)
                for s in secrets:
                    assert s not in blob, (path, s)
        _, body, _ = client.request("GET", "/db/auth_tokens")
        assert "[redacted]" in body
        _, body, _ = client.request("GET", "/db/relationship_config")
        assert "[redacted]" in body

    def test_no_raw_text_when_switched_off(self, dash_no_raw):
        _, client = dash_no_raw
        for path in all_pages(client):
            _, body, _ = client.request("GET", path)
            assert "CANARY-RAW" not in body, path
        _, home, _ = client.request("GET", "/")
        assert "Raw text is hidden" in home
        _, users, _ = client.request("GET", "/users")
        assert users.count("/users/") == 5                # rows still listed
        _, o, _ = client.request("GET", "/")
        assert "<b>5</b>users" in o and "<b>4</b>messages total" in o

    def test_get_never_modifies_db_and_ignores_sql_params(self, dash):
        cfg, client = dash
        before = digest(cfg.db_path)
        for path in all_pages(client):
            client.request("GET", path + ("&" if "?" in path else "?") +
                           "sql=DROP%20TABLE%20users&q=DELETE%20FROM%20users")
        assert digest(cfg.db_path) == before
        assert not os.path.exists(cfg.backup_dir)

    def test_missing_db_is_503_and_not_created(self, tmp_path, monkeypatch):
        server, cfg, client = start(tmp_path, monkeypatch)
        client.login()
        os.unlink(cfg.db_path)
        try:
            assert client.request("GET", "/")[0] == 503
            assert client.request("GET", "/healthz")[0] == 200
            assert not os.path.exists(cfg.db_path)
        finally:
            server.shutdown()
            server.server_close()


# ---------------------------------------------------------------------------
# Admin actions
# ---------------------------------------------------------------------------

def count_rows(db_path, table):
    import sqlite3
    c = sqlite3.connect(db_path)
    try:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        c.close()


def backup_files(cfg):
    if not os.path.isdir(cfg.backup_dir):
        return []
    return [f for f in os.listdir(cfg.backup_dir) if f.endswith(".db")]


class TestAdminActions:
    def test_backup_now(self, dash):
        cfg, client = dash
        status, body, _ = client.request("POST", "/admin/backup", {"csrf": client.csrf()})
        assert status == 200 and "admin_manual_" in body
        assert len(backup_files(cfg)) == 1
        _, admin, _ = client.request("GET", "/admin")
        assert "admin_manual_" in admin

    def test_post_without_or_with_bad_csrf_refused(self, dash):
        cfg, client = dash
        before = digest(cfg.db_path)
        for path, extra in [("/admin/backup", {}), ("/admin/clear-all", {"confirmation": "CLEAR ALL DATA"}),
                            ("/admin/clear-user/1", {"confirmation": "CLEAR USER 1"})]:
            assert client.request("POST", path, extra)[0] == 403
            assert client.request("POST", path, {**extra, "csrf": "bad"})[0] == 403
        assert digest(cfg.db_path) == before and backup_files(cfg) == []

    def test_clear_user_requires_typed_confirmation(self, dash):
        cfg, client = dash
        csrf = client.csrf()
        for phrase in ("", "clear user 1", "CLEAR USER 2", "yes"):
            status, body, _ = client.request(
                "POST", "/admin/clear-user/1", {"csrf": csrf, "confirmation": phrase})
            assert status == 400 and "Nothing was changed" in body
        assert count_rows(cfg.db_path, "users") == 5 and backup_files(cfg) == []

    def test_clear_user_with_confirmation(self, dash):
        cfg, client = dash
        _, preview, _ = client.request("GET", "/admin/clear-user/1")
        assert "CLEAR USER 1" in preview and "messages" in preview
        assert client.request("GET", "/admin/clear-user?user_id=1")[0] == 302
        status, body, _ = client.request(
            "POST", "/admin/clear-user/1", {"csrf": client.csrf(), "confirmation": "CLEAR USER 1"})
        assert status == 200 and "Done: clear_user" in body
        assert len(backup_files(cfg)) == 1
        assert count_rows(cfg.db_path, "users") == 4
        assert count_rows(cfg.db_path, "messages") == 2

    def test_clear_all_requires_typed_confirmation(self, dash):
        cfg, client = dash
        status, _, _ = client.request(
            "POST", "/admin/clear-all", {"csrf": client.csrf(), "confirmation": "clear all data"})
        assert status == 400
        assert count_rows(cfg.db_path, "users") == 5 and backup_files(cfg) == []

    def test_clear_all_with_confirmation(self, dash):
        cfg, client = dash
        status, body, _ = client.request(
            "POST", "/admin/clear-all", {"csrf": client.csrf(), "confirmation": "CLEAR ALL DATA"})
        assert status == 200
        assert len(backup_files(cfg)) == 1
        for t in ("users", "messages", "facts", "auth_tokens", "usage"):
            assert count_rows(cfg.db_path, t) == 0
        assert client.request("GET", "/")[0] == 200  # dashboard still works on empty DB

    def test_backup_failure_reports_and_deletes_nothing(self, tmp_path, monkeypatch):
        blocker = tmp_path / "backups"
        blocker.write_text("in the way")
        server, cfg, client = start(tmp_path, monkeypatch)
        client.login()
        try:
            status, body, _ = client.request(
                "POST", "/admin/clear-all", {"csrf": client.csrf(), "confirmation": "CLEAR ALL DATA"})
            assert status == 500 and "Nothing was deleted" in body
            assert count_rows(cfg.db_path, "users") == 5
        finally:
            server.shutdown()
            server.server_close()

    def test_admin_actions_disabled(self, tmp_path, monkeypatch):
        server, cfg, client = start(tmp_path, monkeypatch, admin_actions_enabled=False)
        client.login()
        try:
            assert client.request("GET", "/admin")[0] == 200
            csrf = client.csrf("/")
            for path, extra in [("/admin/backup", {}),
                                ("/admin/clear-all", {"confirmation": "CLEAR ALL DATA"}),
                                ("/admin/clear-user/1", {"confirmation": "CLEAR USER 1"})]:
                assert client.request("POST", path, {"csrf": csrf, **extra})[0] == 403
            assert client.request("GET", "/admin/clear-all")[0] == 403
            assert count_rows(cfg.db_path, "users") == 5 and backup_files(cfg) == []
        finally:
            server.shutdown()
            server.server_close()

    def test_oversized_body_rejected(self, dash):
        _, client = dash
        status, _, _ = client.request("POST", "/admin/backup", {"csrf": "x" * 100_000})
        assert status == 413

    def test_unsupported_methods_and_unknown_posts(self, dash):
        _, client = dash
        assert client.request("PUT", "/")[0] == 405
        assert client.request("POST", "/db/users", {"csrf": client.csrf()})[0] == 404
