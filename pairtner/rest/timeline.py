"""Admin timeline: one chronological stream of everything in a relationship.

``GET /api/admin/timeline/{relationship_id}?limit=50&before=<ts>&types=msg,fact``
is served by the dashboard app (admin auth); this module owns the query.

Event types in the output: ``message`` (user), ``bot_reply``, ``fact``,
``nudge``, ``summary``, ``archival``, ``setting``. The ``types`` filter uses
the coarse groups in :data:`TYPE_GROUPS`.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
REL_RE = re.compile(r"^\d+_\d+$")
WORKING_KINDS = ("working", "core")

# Public filter names -> SQL branches they enable.
TYPE_GROUPS = ("msg", "fact", "nudge", "summary", "archive", "setting", "health")
_ALIASES = {"message": "msg", "messages": "msg", "bot": "msg", "bot_reply": "msg",
            "facts": "fact", "nudges": "nudge", "summaries": "summary",
            "archival": "archive", "archived": "archive", "settings": "setting",
            "health_score": "health"}


class BadRequest(ValueError):
    """Invalid query parameter."""


def parse_types(raw: str | None) -> set[str]:
    if not raw:
        return set(TYPE_GROUPS)
    out: set[str] = set()
    for part in raw.split(","):
        p = part.strip().lower()
        if not p:
            continue
        p = _ALIASES.get(p, p)
        if p not in TYPE_GROUPS:
            raise BadRequest(f"unknown type: {part.strip()!r}")
        out.add(p)
    return out or set(TYPE_GROUPS)


def parse_limit(raw: str | None) -> int:
    if raw in (None, ""):
        return DEFAULT_LIMIT
    try:
        n = int(raw)
    except ValueError as exc:
        raise BadRequest("limit must be an integer") from exc
    if n < 1:
        raise BadRequest("limit must be >= 1")
    return min(n, MAX_LIMIT)


def parse_before(raw: str | None) -> str | None:
    """Normalise an ISO timestamp to SQLite's ``YYYY-MM-DD HH:MM:SS``."""
    if not raw:
        return None
    m = re.match(r"^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}(?::\d{2})?))?", raw.strip())
    if not m:
        raise BadRequest("before must be an ISO timestamp")
    time = m.group(2) or "00:00:00"
    if len(time) == 5:
        time += ":00"
    return f"{m.group(1)} {time}"


def _iso(ts: str | None) -> str | None:
    return ts.replace(" ", "T") if ts else ts


def _narrative(meta: str | None, fallback: str) -> str:
    if meta:
        try:
            data = json.loads(meta)
        except ValueError:
            return fallback
        if isinstance(data, dict) and isinstance(data.get("narrative"), str):
            return data["narrative"]
    return fallback


def relationship_exists(conn: sqlite3.Connection, rid: str) -> bool:
    return conn.execute("SELECT 1 FROM relationships WHERE id = ?", (rid,)).fetchone() is not None


def build_timeline(
    conn: sqlite3.Connection, rid: str, *, limit: int = DEFAULT_LIMIT,
    before: str | None = None, types: set[str] | None = None, show_raw: bool = True,
) -> list[dict[str, Any]]:
    """Newest ``limit`` events older than ``before``, returned oldest-first."""
    types = types or set(TYPE_GROUPS)
    # Every branch yields: ts, etype, user_id, name, role, text, a, b, c, d
    # (a-d carry per-type extras, mapped back to named keys below).
    branches: list[str] = []
    params: list[Any] = []

    def add(sql: str, *p: Any) -> None:
        branches.append(sql)
        params.extend(p)

    if "msg" in types:
        add("SELECT datetime(m.created_at) AS ts, 'msg' AS etype, m.user_id, u.name, m.role, "
            "m.content AS text, NULL AS a, NULL AS b, NULL AS c, m.id AS d "
            "FROM messages m LEFT JOIN users u ON u.tg_id = m.user_id "
            "WHERE m.relationship_id = ? AND m.deleted_at IS NULL", rid)
    # A fact carrying a nudge is shown as a nudge card; keep it out of the fact
    # branch whenever the nudge branch is active so it is not listed twice.
    fact_dedup = " AND f.nudge_target_id IS NULL" if "nudge" in types else ""
    if "fact" in types:
        add("SELECT datetime(f.created_at), 'fact', f.owner_id, u.name, NULL, f.text, "
            "f.visibility, f.intensity, f.status, f.id "
            "FROM facts f LEFT JOIN users u ON u.tg_id = f.owner_id "
            f"WHERE f.relationship_id = ? AND f.kind IN ('working','core'){fact_dedup}", rid)
    if "nudge" in types:
        add("SELECT datetime(f.created_at), 'nudge', f.owner_id, u.name, f.nudge_type, "
            "f.nudge_suggestion, f.nudge_status, f.nudge_target_id, "
            "COALESCE(f.nudge_delivered_at, f.nudge_cancelled_at), f.id "
            "FROM facts f LEFT JOIN users u ON u.tg_id = f.owner_id "
            "WHERE f.relationship_id = ? AND f.nudge_target_id IS NOT NULL", rid)
    if "summary" in types:
        add("SELECT datetime(f.created_at), 'summary', f.owner_id, u.name, f.kind, f.text, "
            "f.metadata, f.visibility, NULL, f.id "
            "FROM facts f LEFT JOIN users u ON u.tg_id = f.owner_id "
            "WHERE f.relationship_id = ? AND f.kind NOT IN ('working','core')", rid)
    if "archive" in types:
        add("SELECT datetime(f.archived_at), 'archival', f.owner_id, u.name, f.kind, f.text, "
            "f.status, NULL, NULL, f.id "
            "FROM facts f LEFT JOIN users u ON u.tg_id = f.owner_id "
            "WHERE f.relationship_id = ? AND f.archived_at IS NOT NULL", rid)
    if "setting" in types:
        add("SELECT datetime(COALESCE(c.updated_at, c.created_at)), 'setting', NULL, NULL, "
            "c.config_key, c.config_value, NULL, NULL, NULL, NULL "
            "FROM relationship_config c WHERE c.relationship_id = ?", rid)

    if "health" in types:
        add("SELECT datetime(a.created_at), 'health', NULL, NULL, NULL, a.content, "
            "NULL, NULL, NULL, a.id FROM analysis_results a "
            "WHERE a.relationship_id = ? AND a.result_type = 'health_score'", rid)

    # The first SELECT of a UNION names the columns; pin them with an empty row set.
    names = ("SELECT NULL AS ts, NULL AS etype, NULL AS user_id, NULL AS name, NULL AS role, "
             "NULL AS text, NULL AS a, NULL AS b, NULL AS c, NULL AS d WHERE 0")
    sql = ("SELECT * FROM (" + names + " UNION ALL " + " UNION ALL ".join(branches)
           + ") WHERE ts IS NOT NULL")
    if before:
        sql += " AND ts < ?"
        params.append(before)
    sql += (" ORDER BY ts DESC, CASE etype WHEN 'msg' THEN 0 WHEN 'fact' THEN 1 "
            "WHEN 'nudge' THEN 2 WHEN 'summary' THEN 3 WHEN 'archival' THEN 4 ELSE 5 END DESC, "
            "d DESC LIMIT ?")
    params.append(limit)

    prev = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.row_factory = prev

    hidden = "[hidden]"
    events: list[dict[str, Any]] = []
    for r in reversed(rows):
        text = r["text"] if show_raw else hidden
        ev: dict[str, Any] = {"ts": _iso(r["ts"])}
        t = r["etype"]
        if t == "msg":
            if r["role"] == "user":
                ev.update(type="message", user=r["name"] or f"User {r['user_id']}",
                          role="user", content=text)
            else:
                ev.update(type="bot_reply", content=text, to=r["name"])
        elif t == "fact":
            ev.update(type="fact", text=text, visibility=r["a"], intensity=r["b"],
                      status=r["c"], user=r["name"])
        elif t == "nudge":
            ev.update(type="nudge", nudge_type=r["role"], status=r["a"],
                      suggestion=text, target_id=r["b"], user=r["name"],
                      resolved_at=_iso(r["c"]))
        elif t == "summary":
            narrative = _narrative(r["a"], r["text"]) if show_raw else hidden
            ev.update(type="summary", kind=r["role"], narrative=narrative,
                      user=r["name"], visibility=r["b"])
        elif t == "archival":
            ev.update(type="archival", kind=r["role"], text=text, status=r["a"],
                      user=r["name"])
        elif t == "health":
            try:
                data = json.loads(r["text"])
            except (TypeError, ValueError):
                data = {}
            dims = {k: v for k, v in data.items()
                    if k not in ("total", "computed_at", "window_days")}
            ev.update(type="health_score", total=data.get("total"), dimensions=dims,
                      window_days=data.get("window_days"))
        else:
            ev.update(type="setting", key=r["role"], value=r["text"])
        events.append(ev)
    return events
