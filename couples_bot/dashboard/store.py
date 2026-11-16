"""SQLite access for the dashboard.

Reads use a ``mode=ro`` connection with ``query_only`` and an authorizer that
only allows SELECT-style operations. Writes are only opened by
``admin_actions`` through :func:`open_readwrite`. No connection is kept open
between requests, so the bot's own short-lived connections are never blocked
for long.
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from couples_bot.dashboard.redaction import quote_ident

BUSY_TIMEOUT_SECONDS = 5.0
_HIDDEN_TABLE_PREFIXES = ("sqlite_", "facts_fts")
_ALLOWED_PRAGMAS = {"table_info", "table_xinfo", "index_list", "index_info"}
_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),
}


class NotFound(LookupError):
    """Unknown table, user, or couple."""


def _read_only_authorizer(action, arg1, arg2, db_name, trigger):
    if action in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and arg1 in _ALLOWED_PRAGMAS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _uri(db_path: str, mode: str) -> str:
    return Path(db_path).resolve().as_uri() + f"?mode={mode}"


def _require_file(db_path: str) -> None:
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"database not found: {db_path}")


def open_readonly(db_path: str) -> sqlite3.Connection:
    """Open the DB read-only. Never creates a missing file."""
    _require_file(db_path)
    conn = sqlite3.connect(
        _uri(db_path, "ro"), uri=True, timeout=BUSY_TIMEOUT_SECONDS,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.set_authorizer(_read_only_authorizer)
    return conn


def open_readonly_raw(db_path: str) -> sqlite3.Connection:
    """Read-only connection without the authorizer (used as backup source)."""
    _require_file(db_path)
    conn = sqlite3.connect(
        _uri(db_path, "ro"), uri=True, timeout=BUSY_TIMEOUT_SECONDS,
        check_same_thread=False,
    )
    conn.execute("PRAGMA query_only=ON")
    return conn


def open_readwrite(db_path: str) -> sqlite3.Connection:
    """Open the DB read-write in manual-transaction mode. Admin actions only."""
    _require_file(db_path)
    return sqlite3.connect(
        _uri(db_path, "rw"), uri=True, timeout=BUSY_TIMEOUT_SECONDS,
        isolation_level=None, check_same_thread=False,
    )


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return [
        r[0] for r in rows if not r[0].startswith(_HIDDEN_TABLE_PREFIXES)
    ]


def require_table(conn: sqlite3.Connection, table: str) -> str:
    """Return *table* if it is a visible table, else raise :class:`NotFound`."""
    if table not in list_tables(conn):
        raise NotFound(f"no such table: {table!r}")
    return table


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    require_table(conn, table)
    return [
        r[1] for r in conn.execute(f"PRAGMA table_info({quote_ident(table)})")
    ]


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    require_table(conn, table)
    return conn.execute(f"SELECT COUNT(*) FROM {quote_ident(table)}").fetchone()[0]


def table_schema(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    require_table(conn, table)
    ddl = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()[0]
    columns = [
        {
            "cid": r[0], "name": r[1], "type": r[2], "notnull": bool(r[3]),
            "default": r[4], "pk": r[5],
        }
        for r in conn.execute(f"PRAGMA table_info({quote_ident(table)})")
    ]
    indexes = []
    for r in conn.execute(f"PRAGMA index_list({quote_ident(table)})").fetchall():
        cols = [
            c[2] for c in conn.execute(f"PRAGMA index_info({quote_ident(r[1])})")
        ]
        indexes.append({"name": r[1], "unique": bool(r[2]), "columns": cols})
    return {"name": table, "ddl": ddl, "columns": columns, "indexes": indexes}


_ID_RE = re.compile(r"^\d+$")


def parse_user_id(value: str) -> int:
    if not _ID_RE.match(value or ""):
        raise NotFound(f"bad user id: {value!r}")
    return int(value)
