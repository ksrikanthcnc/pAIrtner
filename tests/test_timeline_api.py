"""Admin timeline endpoint + page (v2 schema)."""
from __future__ import annotations

import http.client
import json
import sqlite3
import threading

import pytest

from pairtner.dashboard.app import create_server
from pairtner.dashboard.config import DashboardConfig
from pairtner.rest import timeline as tl
from pairtner.storage.schema_v2 import SCHEMA_V2_SQL, make_relationship_id

TOKEN = "dash-token-0123456789abcdef-XYZ"
REL = make_relationship_id(1, 2)
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _seed(path: str) -> None:
    c = sqlite3.connect(path)
    c.executescript(SCHEMA_V2_SQL)
    c.execute("INSERT INTO users (tg_id, name) VALUES (1,'Alice'),(2,'Bob'),(3,'Eve')")
    c.execute("INSERT INTO relationships (id,user_a_id,user_b_id) VALUES (?,1,2)", (REL,))
    m = "INSERT INTO messages (user_id,relationship_id,role,content,created_at) VALUES (?,?,?,?,?)"
    c.execute(m, (1, REL, "user", "hi bot", "2026-11-15 09:30:00"))
    c.execute(m, (1, REL, "assistant", "hello Alice", "2026-11-15 09:30:02"))
    c.execute(m, (3, None, "user", "other couple", "2026-11-15 09:30:03"))
    c.execute(
        "INSERT INTO facts (owner_id,relationship_id,text,visibility,intensity,created_at) "
        "VALUES (1,?,'likes tea','private',0.8,'2026-11-15 09:30:01')", (REL,))
    c.execute(
        "INSERT INTO facts (owner_id,relationship_id,text,nudge_target_id,nudge_type,"
        "nudge_suggestion,nudge_status,created_at) VALUES "
        "(1,?,'felt ignored',2,'appreciation','say thanks','pending','2026-11-15 09:31:00')", (REL,))
    c.execute(
        "INSERT INTO facts (owner_id,relationship_id,text,kind,metadata,created_at) VALUES "
        "(NULL,?,'raw','weekly','{\"narrative\":\"A good week\"}','2026-11-19 00:00:00')", (REL,))
    c.execute(
        "INSERT INTO facts (owner_id,relationship_id,text,archived_at,created_at) VALUES "
        "(2,?,'old thing','2026-11-18 12:00:00','2026-11-10 08:00:00')", (REL,))
    c.execute("INSERT INTO facts (owner_id,relationship_id,text,created_at) VALUES "
              "(3,'9_9','foreign fact','2026-11-15 09:30:05')")
    c.execute("INSERT INTO relationship_config (relationship_id,config_key,config_value,updated_at)"
              " VALUES (?,'nudges','off','2026-11-16 10:00:00')", (REL,))
    c.commit()
    c.close()


def _get(port: int, path: str, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path, headers=headers or {})
    r = conn.getresponse()
    body = r.read().decode()
    conn.close()
    return r.status, body


@pytest.fixture
def srv(tmp_path):
    db = str(tmp_path / "t.db")
    _seed(db)
    server = create_server(DashboardConfig(
        token=TOKEN, db_path=db, backup_dir=str(tmp_path / "b"), host="127.0.0.1", port=0))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def api(port, qs="", rid=REL):
    s, b = _get(port, f"/api/admin/timeline/{rid}{qs}", AUTH)
    return s, (json.loads(b) if b.startswith(("[", "{")) else b)


def test_interleaved_sorted_and_scoped(srv):
    s, ev = api(srv)
    assert s == 200
    ts = [e["ts"] for e in ev]
    assert ts == sorted(ts)
    types = [e["type"] for e in ev]
    assert types == ["fact", "message", "fact", "bot_reply", "nudge",
                     "setting", "archival", "summary"]
    assert ev[1] == {"ts": "2026-11-15T09:30:00", "type": "message", "user": "Alice",
                     "role": "user", "content": "hi bot"}
    fact = ev[2]
    assert [e["text"] for e in ev if e["type"] == "fact"].count("felt ignored") == 0
    assert (fact["visibility"], fact["intensity"]) == ("private", 0.8)
    nudge = next(e for e in ev if e["type"] == "nudge")
    assert (nudge["nudge_type"], nudge["status"], nudge["suggestion"]) == (
        "appreciation", "pending", "say thanks")
    summ = ev[-1]
    assert (summ["type"], summ["kind"], summ["narrative"]) == ("summary", "weekly", "A good week")
    blob = json.dumps(ev)
    assert "other couple" not in blob and "foreign fact" not in blob
    assert {"setting", "archival"} <= set(types)


def test_nudge_fact_not_duplicated(srv):
    _, ev = api(srv)
    assert sum(1 for e in ev if e["type"] == "nudge") == 1
    assert "felt ignored" not in [e.get("text") for e in ev]
    # Without the nudge branch the fact still appears as a plain fact.
    _, ev = api(srv, "?types=fact")
    assert "felt ignored" in [e.get("text") for e in ev]


def test_help_lists_new_commands():
    from pairtner import i18n
    import inspect
    src = inspect.getsource(i18n)
    for cmd in ("/dryrun", "/debug", "/model", "/search"):
        assert cmd in src


def test_type_filter(srv):
    _, ev = api(srv, "?types=nudge")
    assert {e["type"] for e in ev} == {"nudge"}
    _, ev = api(srv, "?types=msg,summary")
    assert {e["type"] for e in ev} == {"message", "bot_reply", "summary"}
    s, _ = api(srv, "?types=bogus")
    assert s == 400


def test_pagination_before_and_limit(srv):
    _, allev = api(srv)
    _, page1 = api(srv, "?limit=3")
    assert page1 == allev[-3:]
    _, page2 = api(srv, f"?limit=3&before={page1[0]['ts']}")
    assert page2 == allev[-6:-3]
    assert all(e["ts"] < page1[0]["ts"] for e in page2)
    assert api(srv, "?limit=0")[0] == 400
    assert api(srv, "?before=garbage")[0] == 400


def test_unknown_relationship_404(srv):
    assert api(srv, rid="7_8")[0] == 404
    assert api(srv, rid="nope")[0] == 404


def test_admin_auth_required(srv):
    s, _ = _get(srv, f"/api/admin/timeline/{REL}")
    assert s in (302, 401)
    s, _ = _get(srv, f"/api/admin/timeline/{REL}", {"Authorization": "Bearer wrong"})
    assert s == 401
    s, _ = _get(srv, f"/timeline/{REL}")
    assert s in (302, 401)


def test_page_renders_and_escapes(srv, tmp_path):
    s, html = _get(srv, f"/timeline/{REL}", AUTH)
    assert s == 200
    for needle in ("ev-msg", "ev-bot", "ev-fact", "ev-nudge", "ev-summary",
                   "ev-archive", "ev-setting", "f-nudge", "st-pending", "hi bot"):
        assert needle in html
    assert html.count("<script") == html.count('<script src="/static/pwa.js" defer>')  # only the PWA loader


def test_page_load_older_link(srv):
    _, html = _get(srv, f"/timeline/{REL}?limit=3", AUTH)
    assert "Load older events" in html and "before=" in html
    _, html = _get(srv, f"/timeline/{REL}?limit=50", AUTH)
    assert "Load older events" not in html


def test_couple_page_links_to_timeline(tmp_path):
    # The couple page still reads the v1 schema, so use the v1 seed here.
    from tests.dashboard_seed import seed_db
    server = create_server(DashboardConfig(
        token=TOKEN, db_path=seed_db(str(tmp_path / "v1.db")), backup_dir=str(tmp_path / "b"),
        host="127.0.0.1", port=0))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        _, html = _get(server.server_address[1], "/couples/1:2", AUTH)
        assert "/timeline/1_2" in html
    finally:
        server.shutdown()
        server.server_close()


def test_redaction_when_raw_hidden(tmp_path):
    db = str(tmp_path / "r.db")
    _seed(db)
    c = sqlite3.connect(db)
    ev = tl.build_timeline(c, REL, show_raw=False)
    assert "hi bot" not in json.dumps(ev) and "likes tea" not in json.dumps(ev)
