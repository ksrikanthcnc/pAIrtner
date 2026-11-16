"""Read-only SQLite explorer: tables, schema, paged rows.

No free-form SQL. Table names are validated against ``sqlite_master`` and
identifiers are quoted. Secret columns are redacted inside the SELECT; raw
text columns are masked when ``show_raw`` is off.
"""
from __future__ import annotations

import math
import sqlite3
from typing import Any

from couples_bot.dashboard import store
from couples_bot.dashboard.redaction import quote_ident, select_expr

MAX_PAGE_SIZE = 200


def tables(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        {"name": t, "rows": store.count_rows(conn, t)} for t in store.list_tables(conn)
    ]


def schema(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    return store.table_schema(conn, table)


def rows(
    conn: sqlite3.Connection, table: str, page: int, size: int, show_raw: bool
) -> dict[str, Any]:
    columns = store.table_columns(conn, table)  # validates the table name
    size = max(1, min(int(size), MAX_PAGE_SIZE))
    total = store.count_rows(conn, table)
    pages = max(1, math.ceil(total / size))
    page = max(1, min(int(page), pages))
    sql = (
        f"SELECT {select_expr(table, columns, show_raw)} FROM {quote_ident(table)} "
        "ORDER BY rowid LIMIT ? OFFSET ?"
    )
    try:
        data = conn.execute(sql, (size, (page - 1) * size)).fetchall()
    except sqlite3.OperationalError:
        # WITHOUT ROWID tables have no rowid; fall back to natural order.
        data = conn.execute(
            sql.replace("ORDER BY rowid ", ""), (size, (page - 1) * size)
        ).fetchall()
    return {
        "table": table,
        "columns": columns,
        "rows": [list(r) for r in data],
        "total": total,
        "page": page,
        "pages": pages,
        "size": size,
    }
