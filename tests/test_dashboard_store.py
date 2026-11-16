"""Store (read-only connection) and redaction tests."""
from __future__ import annotations

import hashlib
import os
import sqlite3

import pytest

from couples_bot.dashboard import redaction, store
from tests.dashboard_seed import AUTH_CANARY, KEY_CANARY, seed_db


@pytest.fixture
def dash_db(tmp_path) -> str:
    return seed_db(str(tmp_path / "dash.db"))


def _digest(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class TestReadOnly:
    @pytest.mark.parametrize("sql", [
        "INSERT INTO users (tg_id) VALUES (999)",
        "UPDATE users SET name='x'",
        "DELETE FROM users",
        "DROP TABLE users",
        "CREATE TABLE evil (a)",
        "ALTER TABLE users ADD COLUMN z TEXT",
        "PRAGMA writable_schema=ON",
        "PRAGMA query_only=OFF",
        "ATTACH DATABASE ':memory:' AS m",
        "VACUUM",
    ])
    def test_write_statements_fail_and_db_unchanged(self, dash_db, sql):
        before = _digest(dash_db)
        conn = store.open_readonly(dash_db)
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(sql)
        conn.close()
        assert _digest(dash_db) == before

    def test_select_works(self, dash_db):
        conn = store.open_readonly(dash_db)
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 5
        conn.close()

    def test_missing_file_raises_and_creates_nothing(self, tmp_path):
        path = str(tmp_path / "nope.db")
        with pytest.raises(FileNotFoundError):
            store.open_readonly(path)
        with pytest.raises(FileNotFoundError):
            store.open_readwrite(path)
        assert not os.path.exists(path)

    def test_works_while_another_connection_holds_write_lock(self, dash_db):
        writer = sqlite3.connect(dash_db, isolation_level=None)
        writer.execute("BEGIN IMMEDIATE")
        conn = store.open_readonly(dash_db)
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 5
        conn.close()
        writer.execute("ROLLBACK")
        writer.close()


class TestTables:
    def test_fts_and_internal_tables_hidden(self, dash_db):
        conn = store.open_readonly(dash_db)
        names = store.list_tables(conn)
        assert "users" in names and "auth_tokens" in names
        assert not any(n.startswith(("facts_fts", "sqlite_")) for n in names)

    def test_unknown_table_not_found(self, dash_db):
        conn = store.open_readonly(dash_db)
        for bad in ("nope", "users; DROP TABLE users", "facts_fts", "sqlite_master"):
            with pytest.raises(store.NotFound):
                store.table_schema(conn, bad)

    def test_schema(self, dash_db):
        conn = store.open_readonly(dash_db)
        sch = store.table_schema(conn, "facts")
        assert {c["name"] for c in sch["columns"]} >= {"id", "owner_id", "text"}
        assert "CREATE TABLE" in sch["ddl"]
        assert any("owner_id" in i["columns"] for i in sch["indexes"])


class TestRedaction:
    def test_secret_columns_redacted_in_select(self, dash_db):
        conn = store.open_readonly(dash_db)
        for table, col in (("auth_tokens", "token"), ("couple_config", "config_value")):
            for show_raw in (True, False):
                sel = redaction.select_expr(table, [col], show_raw)
                rows = conn.execute(f"SELECT {sel} FROM {table}").fetchall()
                assert rows and all(r[0] == "[redacted]" for r in rows)

    def test_raw_text_hidden_when_off(self, dash_db):
        conn = store.open_readonly(dash_db)
        cols = store.table_columns(conn, "messages")
        sel = redaction.select_expr("messages", cols, False)
        rows = conn.execute(f"SELECT {sel} FROM messages").fetchall()
        assert all(r["content"] == "[hidden]" for r in rows)
        assert all(isinstance(r["user_id"], int) for r in rows)

    def test_raw_text_shown_when_on(self, dash_db):
        conn = store.open_readonly(dash_db)
        sel = redaction.select_expr("messages", ["content"], True)
        assert any("CANARY-RAW" in r[0] for r in conn.execute(f"SELECT {sel} FROM messages"))

    def test_unknown_column_fails_closed_and_pattern_redacts(self):
        assert redaction.select_expr("new_table", ["notes"], False) == "'[hidden]' AS \"notes\""
        assert "[redacted]" in redaction.select_expr("new_table", ["my_api_key"], True)
        assert "[redacted]" in redaction.select_expr("new_table", ["Password"], True)
        assert "[redacted]" in redaction.select_expr("new_table", ["secret_x"], True)

    def test_redact_row_python_fallback(self):
        row = {"user_id": 1, "token": AUTH_CANARY, "text": "hello"}
        assert redaction.redact_row("auth_tokens", row, True)["token"] == "[redacted]"
        off = redaction.redact_row("facts", {"id": 1, "text": "hello"}, False)
        assert off == {"id": 1, "text": "[hidden]"}

    def test_canaries_never_in_redacted_rows(self, dash_db):
        conn = store.open_readonly(dash_db)
        for table in store.list_tables(conn):
            cols = store.table_columns(conn, table)
            for show_raw in (True, False):
                sel = redaction.select_expr(table, cols, show_raw)
                dump = repr([tuple(r) for r in conn.execute(f"SELECT {sel} FROM {table}")])
                assert AUTH_CANARY not in dump and KEY_CANARY not in dump
                if not show_raw:
                    assert "CANARY-RAW" not in dump

    def test_every_schema_column_is_classified(self, dash_db):
        """Fails when someone adds a column without classifying it."""
        conn = store.open_readonly(dash_db)
        for table in store.list_tables(conn):
            for col in store.table_columns(conn, table):
                known = (
                    redaction.is_secret(table, col)
                    or col in redaction.METADATA_COLUMNS.get(table, ())
                    or col in redaction.RAW_TEXT_COLUMNS.get(table, ())
                )
                assert known, f"unclassified column {table}.{col}"
