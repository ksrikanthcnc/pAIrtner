"""Low-level SQLite helpers shared by all storage modules."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiosqlite


def _now_iso() -> str:
    """UTC now as an ISO 8601 string (no fractional seconds)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    """Convert an aiosqlite.Row to a plain dict."""
    return dict(row)


async def _fetch_one(
    db_path: str, sql: str, params: tuple = ()
) -> dict[str, Any] | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            row = await cur.fetchone()
            return _row_to_dict(row) if row else None


async def _fetch_all(
    db_path: str, sql: str, params: tuple = ()
) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            return [_row_to_dict(r) for r in await cur.fetchall()]


async def _execute(
    db_path: str, sql: str, params: tuple = ()
) -> int:
    """Execute a write and return the lastrowid."""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(sql, params)
        await db.commit()
        return cur.lastrowid or 0
