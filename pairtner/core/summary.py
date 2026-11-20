"""Unified period summaries (v2) — replaces v1 consolidation.

One function summarises any period (daily .. yearly). Differences from v1:
- summarises ALL activity (active + resolved working facts, bot-source included)
- fixed calendar periods (ISO weeks, calendar months/quarters/years)
- sources are archived (``archived_at``) once the summary exists
- structured output: resolved / ongoing / new + narrative prose
- higher levels summarise the lower-level summaries in their window
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import aiosqlite

from pairtner.llm import LLMClient
from pairtner.storage.helpers import _now_iso

log = logging.getLogger(__name__)

SUMMARY_INTERVALS: list[tuple[str, int]] = [
    ("daily", 1), ("triday", 3), ("weekly", 7), ("biweekly", 15),
    ("monthly", 30), ("bimonthly", 60), ("quarterly", 90),
    ("semiannual", 180), ("yearly", 365),
    ("biennium", 730), ("quinquennium", 1825), ("decade", 3650),
]
ACTIVE_INTERVALS: list[str] = ["daily", "weekly", "monthly", "quarterly", "yearly"]  # configurable

_DAYS = dict(SUMMARY_INTERVALS)
_ORDER = [name for name, _ in SUMMARY_INTERVALS]

MIN_ITEMS = 3
BATCH_THRESHOLD = 20
BATCH_SIZE = 10
DAILY_MIN_USER_MESSAGES = 10
MAX_CATCHUP_PERIODS = 60

SUMMARY_SYSTEM = """You summarise a period of a relationship-coaching bot's memory for one person.
Input is a list of memory items (facts, or lower-level summaries) from the period.
Return ONLY JSON:
{"resolved": ["things that were settled"],
 "ongoing": ["things still open or unresolved"],
 "new": ["new developments, people, plans"],
 "narrative": "a short prose summary of the period"}
Keep each list item to one short sentence. Do not invent details."""


# ---------------------------------------------------------------------------
# Period maths
# ---------------------------------------------------------------------------

def period_id_for(period_type: str, d: date) -> str:
    """Period id of the period containing date *d*."""
    if period_type == "weekly":
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}"
    if period_type == "monthly":
        return f"{d.year}-{d.month:02d}"
    if period_type == "quarterly":
        return f"{d.year}-Q{(d.month - 1) // 3 + 1}"
    if period_type == "yearly":
        return f"{d.year}"
    if period_type == "biennium":
        return f"{d.year - d.year % 2}"  # 2024, 2026, 2028...
    if period_type == "quinquennium":
        return f"{d.year - d.year % 5}"  # 2020, 2025, 2030...
    if period_type == "decade":
        return f"{d.year - d.year % 10}s"  # 2020s, 2030s...
    if period_type in _DAYS:  # daily + rolling intervals: id = start date
        return d.isoformat()
    raise ValueError(f"unknown period_type {period_type!r}")


def period_window(period_type: str, period_id: str) -> tuple[date, date]:
    """Return (start, end_exclusive) dates for a period."""
    if period_type not in _DAYS:
        raise ValueError(f"unknown period_type {period_type!r}")
    try:
        if period_type == "weekly":
            m = re.fullmatch(r"(\d{4})-W(\d{2})", period_id)
            start = date.fromisocalendar(int(m[1]), int(m[2]), 1)
            return start, start + timedelta(days=7)
        if period_type == "monthly":
            m = re.fullmatch(r"(\d{4})-(\d{2})", period_id)
            start = date(int(m[1]), int(m[2]), 1)
            end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
            return start, end
        if period_type == "quarterly":
            m = re.fullmatch(r"(\d{4})-Q([1-4])", period_id)
            start = date(int(m[1]), (int(m[2]) - 1) * 3 + 1, 1)
            end = date(start.year + (start.month == 10), (start.month + 2) % 12 + 1, 1)
            return start, end
        if period_type == "yearly":
            y = int(period_id)
            return date(y, 1, 1), date(y + 1, 1, 1)
        if period_type == "biennium":
            y = int(period_id)
            return date(y, 1, 1), date(y + 2, 1, 1)
        if period_type == "quinquennium":
            y = int(period_id)
            return date(y, 1, 1), date(y + 5, 1, 1)
        if period_type == "decade":
            y = int(period_id.rstrip("s"))
            return date(y, 1, 1), date(y + 10, 1, 1)
        start = date.fromisoformat(period_id)
        return start, start + timedelta(days=_DAYS[period_type])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"bad period_id {period_id!r} for {period_type}") from exc


def _next_start(period_type: str, period_id: str) -> date:
    return period_window(period_type, period_id)[1]


# ---------------------------------------------------------------------------
# LLM output parsing
# ---------------------------------------------------------------------------

def _parse_summary(result: dict[str, Any]) -> dict[str, Any] | None:
    data: Any = None
    if any(k in result for k in ("narrative", "resolved", "ongoing")):
        data = result
    else:
        reply = result.get("reply", "")
        if isinstance(reply, str):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", reply.strip())
            try:
                data = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return None
    if not isinstance(data, dict):
        return None

    def _list(key: str) -> list[str]:
        v = data.get(key) or []
        return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []

    out = {
        "resolved": _list("resolved"),
        "ongoing": _list("ongoing"),
        "new": _list("new"),
        "narrative": str(data.get("narrative") or "").strip(),
    }
    if not (out["narrative"] or out["resolved"] or out["ongoing"] or out["new"]):
        return None
    return out


def _merge(parts: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {"resolved": [], "ongoing": [], "new": [], "narrative": ""}
    for p in parts:
        for k in ("resolved", "ongoing", "new"):
            merged[k].extend(x for x in p[k] if x not in merged[k])
    merged["narrative"] = " ".join(p["narrative"] for p in parts if p["narrative"])
    return merged


async def _summarise(llm: LLMClient, items: list[dict[str, Any]],
                     period_type: str, period_id: str) -> dict[str, Any] | None:
    batches = ([items] if len(items) <= BATCH_THRESHOLD
               else [items[i:i + BATCH_SIZE] for i in range(0, len(items), BATCH_SIZE)])
    parts: list[dict[str, Any]] = []
    for batch in batches:
        lines = [f"[{it['id']}] ({it['status']}) {it['text']}" for it in batch]
        msg = f"Period: {period_type} {period_id}\nItems:\n" + "\n".join(lines)
        parsed = _parse_summary(await llm.generate(msg, SUMMARY_SYSTEM))
        if parsed is None:
            log.warning("summary: unparseable LLM output for %s %s", period_type, period_id)
            return None  # abort: never archive on partial failure
        parts.append(parsed)
    return parts[0] if len(parts) == 1 else _merge(parts)


# ---------------------------------------------------------------------------
# DB access
# ---------------------------------------------------------------------------

def _meta(row: dict[str, Any]) -> dict[str, Any]:
    try:
        m = json.loads(row.get("metadata") or "{}")
        return m if isinstance(m, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


async def _summary_exists(db: aiosqlite.Connection, owner_id: int | None,
                          relationship_id: str | None, period_type: str,
                          period_id: str) -> bool:
    async with db.execute(
        "SELECT 1 FROM facts WHERE kind=? AND owner_id IS ? AND relationship_id IS ? "
        "AND json_extract(metadata,'$.period')=?",
        (period_type, owner_id, relationship_id, period_id),
    ) as cur:
        return await cur.fetchone() is not None


async def _collect_sources(db: aiosqlite.Connection, user_id: int,
                           relationship_id: str | None, period_type: str,
                           start: date, end: date) -> list[dict[str, Any]]:
    rel_sql, rel_args = ("", ())
    if relationship_id is not None:
        rel_sql, rel_args = " AND relationship_id = ?", (relationship_id,)

    # working facts created in the window (active + resolved, any msg_source)
    async with db.execute(
        "SELECT id, text, status, created_at FROM facts WHERE owner_id=? AND kind='working' "
        "AND status IN ('active','resolved') AND archived_at IS NULL "
        "AND created_at >= ? AND created_at < ?" + rel_sql + " ORDER BY created_at, id",
        (user_id, start.isoformat(), end.isoformat(), *rel_args),
    ) as cur:
        items = [dict(r) for r in await cur.fetchall()]

    # lower-level summaries whose own window starts inside this window
    lower = _ORDER[:_ORDER.index(period_type)]
    if lower:
        marks = ",".join("?" * len(lower))
        async with db.execute(
            f"SELECT id, text, status, created_at, metadata FROM facts WHERE owner_id=? "
            f"AND kind IN ({marks}) AND archived_at IS NULL" + rel_sql + " ORDER BY id",
            (user_id, *lower, *rel_args),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        for r in rows:
            s = _meta(r).get("start")
            if s and start.isoformat() <= s < end.isoformat():
                items.append(r)
    return items


async def _store(db: aiosqlite.Connection, *, owner_id: int | None,
                 relationship_id: str | None, period_type: str, period_id: str,
                 start: date, end: date, summary: dict[str, Any],
                 source_ids: list[int], archive: bool) -> int:
    text = summary["narrative"] or "; ".join(
        summary["resolved"] + summary["ongoing"] + summary["new"])
    meta = {"period": period_id, "start": start.isoformat(), "end": end.isoformat(),
            **summary}
    cur = await db.execute(
        "INSERT INTO facts (owner_id, relationship_id, text, visibility, kind, status, "
        "msg_source, source_ids, metadata) VALUES (?,?,?,?,?,?,?,?,?)",
        (owner_id, relationship_id, text, "private", period_type, "active", "system",
         json.dumps(source_ids), json.dumps(meta)),
    )
    new_id = cur.lastrowid or 0
    if archive and source_ids:
        now = _now_iso()
        marks = ",".join("?" * len(source_ids))
        await db.execute(
            f"UPDATE facts SET archived_at=?, updated_at=? WHERE id IN ({marks}) "
            f"AND archived_at IS NULL", (now, now, *source_ids))
    await db.commit()
    return new_id


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def create_summary(
    db_path: str, llm: LLMClient, user_id: int,
    period_type: str, period_id: str,
    relationship_id: str | None = None,
) -> int | None:
    """Create a summary for a specific period. Returns fact id or None if skipped."""
    start, end = period_window(period_type, period_id)

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        if await _summary_exists(db, user_id, relationship_id, period_type, period_id):
            return None

        if period_type == "daily":
            async with db.execute(
                "SELECT COUNT(*) FROM messages WHERE user_id=? AND role='user' "
                "AND deleted_at IS NULL AND created_at >= ? AND created_at < ?",
                (user_id, start.isoformat(), end.isoformat()),
            ) as cur:
                n_msgs = (await cur.fetchone())[0]
            if n_msgs < DAILY_MIN_USER_MESSAGES:
                return None

        items = await _collect_sources(db, user_id, relationship_id, period_type, start, end)
        if len(items) < MIN_ITEMS:
            return None

        summary = await _summarise(llm, items, period_type, period_id)
        if summary is None:
            return None
        return await _store(
            db, owner_id=user_id, relationship_id=relationship_id,
            period_type=period_type, period_id=period_id, start=start, end=end,
            summary=summary, source_ids=[i["id"] for i in items], archive=True)


async def run_periodic_summaries(
    db_path: str, llm: LLMClient, user_id: int,
    relationship_id: str | None = None,
    *, now: datetime | None = None,
    intervals: list[str] | None = None,
) -> dict[str, int]:
    """Run all due summaries for a user. Returns {period_type: count_created}."""
    today = (now or datetime.now(timezone.utc)).date()
    active = set(intervals if intervals is not None else ACTIVE_INTERVALS)
    created: dict[str, int] = {}

    for period_type in (p for p in _ORDER if p in active):  # low -> high levels
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT metadata FROM facts WHERE owner_id=? AND kind=?",
                (user_id, period_type),
            ) as cur:
                ends = [_meta(dict(r)).get("end") for r in await cur.fetchall()]
            ends = [e for e in ends if e]
            if ends:
                cursor = date.fromisoformat(max(ends))
            else:
                # Bootstrap: find the earliest relevant date from either
                # unarchived working facts OR lower-level summary starts.
                # Lower levels archive working facts, so relying only on
                # unarchived facts would strand higher levels forever.
                candidates: list[str] = []
                async with db.execute(
                    "SELECT MIN(created_at) FROM facts WHERE owner_id=? AND kind='working' "
                    "AND archived_at IS NULL", (user_id,),
                ) as cur:
                    first = (await cur.fetchone())[0]
                if first:
                    candidates.append(first[:10])
                # Also check lower-level summary start dates
                lower = _ORDER[:_ORDER.index(period_type)]
                lower_active = [lv for lv in lower if lv in active]
                if lower_active:
                    marks = ",".join("?" * len(lower_active))
                    async with db.execute(
                        f"SELECT MIN(json_extract(metadata,'$.start')) FROM facts "
                        f"WHERE owner_id=? AND kind IN ({marks})",
                        (user_id, *lower_active),
                    ) as cur:
                        low_first = (await cur.fetchone())[0]
                    if low_first:
                        candidates.append(low_first[:10])
                if not candidates:
                    continue
                cursor = date.fromisoformat(min(candidates))

        # enumerate completed periods from cursor up to today
        pids: list[str] = []
        pid = period_id_for(period_type, cursor)
        while period_window(period_type, pid)[1] <= today:
            pids.append(pid)
            pid = period_id_for(period_type, _next_start(period_type, pid))
        for pid in pids[-MAX_CATCHUP_PERIODS:]:
            if await create_summary(db_path, llm, user_id, period_type, pid, relationship_id):
                created[period_type] = created.get(period_type, 0) + 1
    return created


async def create_couple_summary(
    db_path: str, llm: LLMClient, relationship_id: str,
    period_type: str, period_id: str,
) -> int | None:
    """Create a couple-level summary combining both partners' perspectives.

    Built from each partner's own summary of the same period. Partner summaries are
    NOT archived (they remain the per-user record); the couple summary is stored
    with owner_id NULL and private visibility so it is never shown verbatim.
    """
    start, end = period_window(period_type, period_id)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        if await _summary_exists(db, None, relationship_id, period_type, period_id):
            return None
        async with db.execute(
            "SELECT id, owner_id, text, metadata FROM facts WHERE relationship_id=? "
            "AND kind=? AND owner_id IS NOT NULL AND archived_at IS NULL "
            "AND json_extract(metadata,'$.period')=? ORDER BY owner_id, id",
            (relationship_id, period_type, period_id),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        if len({r["owner_id"] for r in rows}) < 2:
            return None  # need both partners' perspectives

        items = [{"id": r["id"], "status": f"partner {r['owner_id']}", "text": r["text"]}
                 for r in rows]
        summary = await _summarise(llm, items, period_type, period_id)
        if summary is None:
            return None
        return await _store(
            db, owner_id=None, relationship_id=relationship_id,
            period_type=period_type, period_id=period_id, start=start, end=end,
            summary=summary, source_ids=[r["id"] for r in rows], archive=False)
