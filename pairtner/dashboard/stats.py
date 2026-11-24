"""Read-only stats and timeline queries for the dashboard.

Every function takes a read-only connection and a ``show_raw`` flag. With
``show_raw=False`` user text is never selected into the result: text fields
are ``None`` and only counts / metadata remain.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from pairtner.db import make_couple_id
from pairtner.dashboard.store import NotFound
from pairtner.storage.tagfmt import parse_fact_tags

MAX_TIMELINE_EVENTS = 300
_COUPLE_RE = re.compile(r"^(\d+):(\d+)$")


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    return conn.execute(sql, params).fetchone()[0] or 0


def _group(conn: sqlite3.Connection, table: str, col: str) -> dict[str, int]:
    # table / col are module constants, never user input.
    rows = conn.execute(f"SELECT {col}, COUNT(*) FROM {table} GROUP BY {col}")
    return {r[0]: r[1] for r in rows}


def _group_where(conn: sqlite3.Connection, table: str, col: str, where: str) -> dict[str, int]:
    rows = conn.execute(f"SELECT {col}, COUNT(*) FROM {table} WHERE {where} GROUP BY {col}")
    return {r[0]: r[1] for r in rows}


def _text(value: Any, show_raw: bool) -> Any:
    return value if show_raw else None


def overview(conn: sqlite3.Connection) -> dict[str, Any]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {
        "users": _scalar(conn, "SELECT COUNT(*) FROM users"),
        "couples": _scalar(
            conn,
            "SELECT COUNT(DISTINCT MIN(tg_id, partner_id) || ':' || "
            "MAX(tg_id, partner_id)) FROM users WHERE partner_id IS NOT NULL",
        ),
        "messages_total": _scalar(conn, "SELECT COUNT(*) FROM messages"),
        "messages_24h": _scalar(
            conn,
            "SELECT COUNT(*) FROM messages "
            "WHERE datetime(created_at) >= datetime('now', '-1 day')",
        ),
        "messages_7d": _scalar(
            conn,
            "SELECT COUNT(*) FROM messages "
            "WHERE datetime(created_at) >= datetime('now', '-7 days')",
        ),
        "facts_total": _scalar(conn, "SELECT COUNT(*) FROM facts"),
        "facts_active": _scalar(
            conn, "SELECT COUNT(*) FROM facts WHERE archived_at IS NULL"
        ),
        "pending_by_status": _group(conn, "relay_queue", "status"),
        "batches_by_status": _group(conn, "relay_batch", "status"),
        "conflicts_by_status": _group(conn, "conflicts", "status"),
        "user_goals_by_status": _group_where(conn, "goals", "status", "scope = 'personal'"),
        "shared_goals_by_status": _group_where(conn, "goals", "status", "scope = 'shared'"),
        "usage_today": _scalar(
            conn, "SELECT SUM(count) FROM usage WHERE date = ?", (today,)
        ),
    }


def _day_range(days: int) -> list[str]:
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]


def messages_per_day(conn: sqlite3.Connection, days: int = 14) -> list[dict[str, Any]]:
    days = max(1, min(days, 365))
    rows = conn.execute(
        "SELECT date(created_at) AS day, COUNT(*) AS n, "
        "SUM(role = 'user') AS from_users "
        "FROM messages WHERE datetime(created_at) >= datetime('now', ?) "
        "GROUP BY day",
        (f"-{days} days",),
    ).fetchall()
    by_day = {r["day"]: r for r in rows}
    return [
        {
            "day": d,
            "count": by_day[d]["n"] if d in by_day else 0,
            "from_users": (by_day[d]["from_users"] or 0) if d in by_day else 0,
        }
        for d in _day_range(days)
    ]


def usage_per_model_per_day(
    conn: sqlite3.Connection, days: int = 14
) -> list[dict[str, Any]]:
    days = max(1, min(days, 365))
    rows = conn.execute(
        "SELECT date, model, count FROM usage WHERE date >= date('now', ?) "
        "ORDER BY date DESC, model",
        (f"-{days - 1} days",),
    ).fetchall()
    return [dict(r) for r in rows]


def user_activity(conn: sqlite3.Connection, show_raw: bool) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT u.tg_id, u.name, u.partner_id, u.paused, u.created_at, "
        " (SELECT COUNT(*) FROM messages m WHERE m.user_id = u.tg_id) AS messages, "
        " (SELECT COUNT(*) FROM facts f WHERE f.owner_id = u.tg_id) AS facts, "
        " (SELECT COUNT(*) FROM relay_queue p WHERE p.sender_id = u.tg_id) AS relays_sent, "
        " (SELECT COUNT(*) FROM relay_queue p WHERE p.receiver_id = u.tg_id) AS relays_received, "
        " (SELECT COUNT(*) FROM goals g WHERE g.user_id = u.tg_id) AS goals, "
        " (SELECT MAX(datetime(m.created_at)) FROM messages m "
        "   WHERE m.user_id = u.tg_id) AS last_seen "
        "FROM users u ORDER BY messages DESC, u.tg_id"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["name"] = _text(d["name"], show_raw)
        out.append(d)
    return out


def queue(conn: sqlite3.Connection, show_raw: bool) -> dict[str, list[dict[str, Any]]]:
    relays = [
        dict(r)
        for r in conn.execute(
            "SELECT id, sender_id, receiver_id, status, send_after, created_at, "
            "length(COALESCE(edited_draft, draft)) AS draft_len, "
            "COALESCE(edited_draft, draft) AS draft "
            "FROM relay_queue WHERE status IN ('awaiting_approval', 'approved') "
            "ORDER BY datetime(created_at)"
        )
    ]
    for r in relays:
        r["draft"] = _text(r["draft"], show_raw)
    batches = [
        dict(r)
        for r in conn.execute(
            "SELECT b.id, b.sender_id, b.receiver_id, b.status, b.send_after, "
            "b.created_at, (SELECT COUNT(*) FROM relay_batch_items i "
            "  WHERE i.batch_id = b.id) AS items "
            "FROM relay_batch b "
            "WHERE b.status IN ('accumulating', 'reviewing', 'approved') "
            "ORDER BY datetime(b.created_at)"
        )
    ]
    return {"relays": relays, "batches": batches}


def conflicts(conn: sqlite3.Connection, show_raw: bool) -> list[dict[str, Any]]:
    out = []
    for r in conn.execute(
        "SELECT id, couple_id, theme, status, opened_at, resolved_at "
        "FROM conflicts ORDER BY datetime(opened_at) DESC"
    ):
        d = dict(r)
        d["theme"] = _text(d["theme"], show_raw)
        out.append(d)
    return out


def goals(conn: sqlite3.Connection, show_raw: bool) -> dict[str, list[dict[str, Any]]]:
    user_goals = []
    for r in conn.execute(
        "SELECT id, user_id, text, status, progress, created_at "
        "FROM goals ORDER BY datetime(created_at) DESC"
    ):
        d = dict(r)
        d["text"] = _text(d["text"], show_raw)
        d["progress"] = _text(d["progress"], show_raw)
        user_goals.append(d)
    return {"goals": user_goals}


def couples(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    pairs = conn.execute(
        "SELECT DISTINCT MIN(tg_id, partner_id) AS a, MAX(tg_id, partner_id) AS b "
        "FROM users WHERE partner_id IS NOT NULL ORDER BY a, b"
    ).fetchall()
    out = []
    for p in pairs:
        cid = make_couple_id(p["a"], p["b"])
        out.append(
            {
                "couple_id": cid,
                "user_a": p["a"],
                "user_b": p["b"],
                "open_conflicts": _scalar(
                    conn,
                    "SELECT COUNT(*) FROM conflicts WHERE couple_id=? AND status='open'",
                    (cid,),
                ),
                "timeline_events": _scalar(
                    conn, "SELECT COUNT(*) FROM timeline_events WHERE couple_id=?", (cid,)
                ),
                "goals": _scalar(
                    conn, "SELECT COUNT(*) FROM goals WHERE couple_id=?", (cid,)
                ),
            }
        )
    return out


def _event(at: str | None, kind: str, label: str, text: Any, show_raw: bool) -> dict:
    return {"at": at or "", "kind": kind, "label": label, "text": _text(text, show_raw)}


def _finish(events: list[dict], limit: int) -> list[dict]:
    events.sort(key=lambda e: e["at"], reverse=True)
    return events[: max(1, min(limit, MAX_TIMELINE_EVENTS))]


def user_timeline(
    conn: sqlite3.Connection, user_id: int, show_raw: bool,
    limit: int = MAX_TIMELINE_EVENTS,
) -> list[dict[str, Any]]:
    if _scalar(conn, "SELECT COUNT(*) FROM users WHERE tg_id=?", (user_id,)) == 0:
        raise NotFound(f"no such user: {user_id}")
    ev: list[dict] = []
    for r in conn.execute(
        "SELECT role, content, length(content) AS n, datetime(created_at) AS at "
        "FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, MAX_TIMELINE_EVENTS),
    ):
        ev.append(_event(r["at"], "message", f"{r['role']} message ({r['n']} chars)",
                         r["content"], show_raw))
    for r in conn.execute(
        "SELECT visibility, tag, text, datetime(created_at) AS at "
        "FROM facts WHERE owner_id=? ORDER BY id DESC LIMIT ?",
        (user_id, MAX_TIMELINE_EVENTS),
    ):
        ev.append(_event(r["at"], "fact",
                         f"{r['visibility']} fact ({', '.join(parse_fact_tags(r['tag'])) or 'untagged'})",
                         r["text"], show_raw))
    for r in conn.execute(
        "SELECT id, sender_id, receiver_id, status, "
        "COALESCE(edited_draft, draft) AS draft, datetime(created_at) AS at "
        "FROM relay_queue WHERE sender_id=? OR receiver_id=? ORDER BY id DESC LIMIT ?",
        (user_id, user_id, MAX_TIMELINE_EVENTS),
    ):
        direction = "sent" if r["sender_id"] == user_id else "received"
        ev.append(_event(r["at"], "relay",
                         f"relay #{r['id']} {direction} ({r['status']})",
                         r["draft"], show_raw))
    for r in conn.execute(
        "SELECT status, text, datetime(created_at) AS at FROM goals "
        "WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, MAX_TIMELINE_EVENTS),
    ):
        ev.append(_event(r["at"], "goal", f"goal ({r['status']})", r["text"], show_raw))
    return _finish(ev, limit)


def parse_couple_id(couple_id: str) -> tuple[int, int]:
    m = _COUPLE_RE.match(couple_id or "")
    if not m:
        raise NotFound(f"bad couple id: {couple_id!r}")
    a, b = int(m.group(1)), int(m.group(2))
    if a >= b:
        raise NotFound(f"bad couple id: {couple_id!r}")
    return a, b


def couple_timeline(
    conn: sqlite3.Connection, couple_id: str, show_raw: bool,
    limit: int = MAX_TIMELINE_EVENTS,
) -> list[dict[str, Any]]:
    a, b = parse_couple_id(couple_id)
    if _scalar(conn, "SELECT COUNT(*) FROM users WHERE tg_id IN (?, ?)", (a, b)) != 2:
        raise NotFound(f"no such couple: {couple_id}")
    ev: list[dict] = []
    for r in conn.execute(
        "SELECT event_type, summary, datetime(created_at) AS at "
        "FROM timeline_events WHERE couple_id=? ORDER BY id DESC LIMIT ?",
        (couple_id, MAX_TIMELINE_EVENTS),
    ):
        ev.append(_event(r["at"], "timeline", r["event_type"], r["summary"], show_raw))
    for r in conn.execute(
        "SELECT id, theme, status, datetime(opened_at) AS opened, "
        "datetime(resolved_at) AS resolved FROM conflicts WHERE couple_id=?",
        (couple_id,),
    ):
        ev.append(_event(r["opened"], "conflict", f"conflict #{r['id']} opened",
                         r["theme"], show_raw))
        if r["resolved"]:
            ev.append(_event(r["resolved"], "conflict", f"conflict #{r['id']} resolved",
                             None, show_raw))
    for r in conn.execute(
        "SELECT id, sender_id, receiver_id, status, "
        "COALESCE(edited_draft, draft) AS draft, datetime(created_at) AS at "
        "FROM relay_queue WHERE (sender_id=? AND receiver_id=?) "
        "OR (sender_id=? AND receiver_id=?) ORDER BY id DESC LIMIT ?",
        (a, b, b, a, MAX_TIMELINE_EVENTS),
    ):
        ev.append(_event(r["at"], "relay",
                         f"relay #{r['id']} {r['sender_id']} -> {r['receiver_id']} "
                         f"({r['status']})", r["draft"], show_raw))
    for r in conn.execute(
        "SELECT id, status, text, datetime(created_at) AS at "
        "FROM goals WHERE couple_id=?",
        (couple_id,),
    ):
        ev.append(_event(r["at"], "goal", f"shared goal #{r['id']} ({r['status']})",
                         r["text"], show_raw))
    for r in conn.execute(
        "SELECT id, week_start, status, summary, datetime(created_at) AS at "
        "FROM checkins WHERE couple_id=?",
        (couple_id,),
    ):
        ev.append(_event(r["at"], "checkin",
                         f"check-in week {r['week_start']} ({r['status']})",
                         r["summary"], show_raw))
    return _finish(ev, limit)


# ---------------------------------------------------------------------------
# Conversation view (admin)
# ---------------------------------------------------------------------------

def user_name(conn: sqlite3.Connection, user_id: int) -> str:
    """Get a user's name, or 'User <id>' if not found."""
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT name FROM users WHERE tg_id = ?", (user_id,)).fetchone()
    return (row["name"] if row and row["name"] else f"User {user_id}")


def couple_conversation(
    conn: sqlite3.Connection, user_a: int, user_b: int, show_raw: bool,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Get interleaved conversation messages for a couple.

    Returns both users' messages + bot responses, ordered chronologically.
    Text is redacted when show_raw is False.
    """
    conn.row_factory = sqlite3.Row
    content_col = "m.content" if show_raw else "NULL"
    rows = conn.execute(
        f"SELECT m.id, m.user_id, m.role, {content_col} as content, "
        "datetime(m.created_at) as created_at, u.name as user_name "
        "FROM messages m "
        "JOIN users u ON m.user_id = u.tg_id "
        "WHERE m.user_id IN (?, ?) "
        "ORDER BY m.created_at ASC LIMIT ?",
        (user_a, user_b, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Overview / couple-detail extras (tolerate DBs without the v2 tables)
# ---------------------------------------------------------------------------

def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _parse_health(content: Any) -> dict[str, Any] | None:
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def latest_health(conn: sqlite3.Connection, couple_id: str) -> dict[str, Any] | None:
    """Latest health score (``total`` + dimension ints) for ``a:b``, or None."""
    if not _has_table(conn, "analysis_results"):
        return None
    a, b = parse_couple_id(couple_id)
    row = conn.execute(
        "SELECT content FROM analysis_results WHERE relationship_id=? "
        "AND result_type='health_score' ORDER BY datetime(created_at) DESC, id DESC LIMIT 1",
        (f"{a}_{b}",),
    ).fetchone()
    return _parse_health(row[0]) if row else None


def health_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Average of each relationship's latest total score."""
    if not _has_table(conn, "analysis_results"):
        return {"scored": 0, "average": None}
    totals = []
    for r in conn.execute(
        "SELECT content FROM analysis_results WHERE id IN ("
        "SELECT MAX(id) FROM analysis_results WHERE result_type='health_score' "
        "GROUP BY relationship_id)"
    ):
        d = _parse_health(r[0])
        if d and isinstance(d.get("total"), (int, float)) and not isinstance(d["total"], bool):
            totals.append(d["total"])
    return {"scored": len(totals),
            "average": round(sum(totals) / len(totals)) if totals else None}


def recent_activity(
    conn: sqlite3.Connection, show_raw: bool, limit: int = 10,
) -> list[dict[str, Any]]:
    """Newest events across all couples: messages, relays, timeline, conflicts."""
    ev: list[dict] = []
    for r in conn.execute(
        "SELECT user_id, role, length(content) AS n, content, datetime(created_at) AS at "
        "FROM messages ORDER BY id DESC LIMIT ?", (limit,)
    ):
        ev.append(_event(r["at"], "message",
                         f"{r['role']} message, user {r['user_id']} ({r['n']} chars)",
                         r["content"], show_raw))
    for r in conn.execute(
        "SELECT id, sender_id, receiver_id, status, COALESCE(edited_draft, draft) AS draft, "
        "datetime(created_at) AS at FROM relay_queue ORDER BY id DESC LIMIT ?", (limit,)
    ):
        ev.append(_event(r["at"], "relay",
                         f"relay #{r['id']} {r['sender_id']} -> {r['receiver_id']} "
                         f"({r['status']})", r["draft"], show_raw))
    for r in conn.execute(
        "SELECT couple_id, event_type, summary, datetime(created_at) AS at "
        "FROM timeline_events ORDER BY id DESC LIMIT ?", (limit,)
    ):
        ev.append(_event(r["at"], "timeline", f"{r['event_type']} ({r['couple_id']})",
                         r["summary"], show_raw))
    for r in conn.execute(
        "SELECT id, couple_id, theme, datetime(opened_at) AS at FROM conflicts "
        "ORDER BY id DESC LIMIT ?", (limit,)
    ):
        ev.append(_event(r["at"], "conflict", f"conflict #{r['id']} opened ({r['couple_id']})",
                         r["theme"], show_raw))
    ev.sort(key=lambda e: e["at"], reverse=True)
    return ev[:limit]


def couple_quick_stats(conn: sqlite3.Connection, couple_id: str) -> dict[str, Any]:
    a, b = parse_couple_id(couple_id)
    ids = (a, b)
    out: dict[str, Any] = {
        "messages_week": _scalar(
            conn,
            "SELECT COUNT(*) FROM messages WHERE user_id IN (?, ?) "
            "AND datetime(created_at) >= datetime('now', '-7 days')", ids),
        "active_facts": _scalar(
            conn,
            "SELECT COUNT(*) FROM facts WHERE owner_id IN (?, ?) AND archived_at IS NULL",
            ids),
        "pending_nudges": None,
    }
    cols = {r[1] for r in conn.execute("PRAGMA table_info(facts)")}
    if "nudge_status" in cols:
        out["pending_nudges"] = _scalar(
            conn,
            "SELECT COUNT(*) FROM facts WHERE owner_id IN (?, ?) "
            "AND nudge_target_id IS NOT NULL AND nudge_status='pending'", ids)
    return out
