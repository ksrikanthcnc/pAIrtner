"""SQLite explorer: tables, schema, paging, redaction."""
from __future__ import annotations

import sqlite3

import pytest

from pairtner.dashboard import explorer, store
from tests.dashboard_seed import AUTH_CANARY, KEY_CANARY, seed_db


@pytest.fixture
def conn(tmp_path):
    path = seed_db(str(tmp_path / "dash.db"))
    c = store.open_readonly(path)
    yield c
    c.close()


def test_tables_with_counts(conn):
    t = {x["name"]: x["rows"] for x in explorer.tables(conn)}
    assert t["users"] == 5 and t["messages"] == 4
    assert not any(n.startswith("facts_fts") for n in t)


def test_paging_boundaries(conn):
    p1 = explorer.rows(conn, "users", 1, 2, True)
    assert (p1["total"], p1["pages"], p1["page"], len(p1["rows"])) == (5, 3, 1, 2)
    p3 = explorer.rows(conn, "users", 3, 2, True)
    assert len(p3["rows"]) == 1
    assert explorer.rows(conn, "users", 99, 2, True)["page"] == 3   # clamped
    assert explorer.rows(conn, "users", -4, 2, True)["page"] == 1
    ids = [r[0] for r in p1["rows"]] + [r[0] for r in explorer.rows(conn, "users", 2, 2, True)["rows"]]
    assert ids == sorted(ids) and len(set(ids)) == 4


def test_size_clamped(conn):
    assert explorer.rows(conn, "users", 1, 10_000, True)["size"] == explorer.MAX_PAGE_SIZE
    assert explorer.rows(conn, "users", 1, 0, True)["size"] == 1


@pytest.mark.parametrize("name", [
    "nope", "users; DROP TABLE users", "users--", '"users"', "facts_fts",
    "facts_fts_data", "sqlite_master", "",
])
def test_bad_table_names_rejected(conn, name):
    with pytest.raises(store.NotFound):
        explorer.rows(conn, name, 1, 10, True)
    with pytest.raises(store.NotFound):
        explorer.schema(conn, name)
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 5


@pytest.mark.parametrize("show_raw", [True, False])
def test_secret_tables_redacted(conn, show_raw):
    for table, col in (("auth_tokens", "token"), ("couple_config", "config_value")):
        data = explorer.rows(conn, table, 1, 50, show_raw)
        idx = data["columns"].index(col)
        assert data["rows"] and all(r[idx] == "[redacted]" for r in data["rows"])
        assert AUTH_CANARY not in repr(data) and KEY_CANARY not in repr(data)


def test_raw_text_masked_when_off(conn):
    on = explorer.rows(conn, "facts", 1, 50, True)
    off = explorer.rows(conn, "facts", 1, 50, False)
    assert "CANARY-RAW" in repr(on)
    assert "CANARY-RAW" not in repr(off)
    assert off["total"] == on["total"] == 3
    ti = off["columns"].index("text")
    assert all(r[ti] == "[hidden]" for r in off["rows"])


def test_schema(conn):
    s = explorer.schema(conn, "messages")
    assert [c["name"] for c in s["columns"]][:3] == ["id", "user_id", "role"]
    assert s["indexes"]


def test_explorer_cannot_write(conn):
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM users")
