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
from pairtner.storage.tagfmt import first_tag

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

SUMMARY_VISIBILITIES = ("private", "shared")  # one summary stream each

MIN_ITEMS = 1          # every fact deserves a summary -- never skip a period with content
BATCH_THRESHOLD = 40   # summarize in one pass up to this many items
BATCH_SIZE = 20        # beyond threshold, batches of this size
DAILY_MIN_USER_MESSAGES = 10
MAX_CATCHUP_PERIODS = 60

# Per-capability overrides (config.model_capability)
_CAPABILITY_BATCH = {"low": (20, 10), "medium": (40, 20), "high": (80, 40)}

SUMMARY_SYSTEM = """You summarise a period of a relationship-coaching bot's memory for one person.
Input is a list of memory items (facts, or lower-level summaries) from the period.
Return ONLY JSON:
{"resolved": ["things that were settled"],
 "ongoing": ["things still open or unresolved"],
 "new": ["new developments, people, plans"],
 "narrative": "a structured bullet list, one bullet per line"}
Rules:
- The narrative must be a structured bullet list. Not prose. Not abstract. Detailed.
- Each bullet starts with [tag] and contains one specific fact with dates/outcomes.
  Example: - [kids] school pickup conflict Nov 8, resolved with shared calendar
- Give more detail to topics discussed more frequently.
- Be COMPREHENSIVE. Every distinct topic in the input must appear in the output. Do not drop minor topics to stay brief.
- Name concrete events, causes, and people (e.g. "argument about chore rota" not "a disagreement").
- Each resolved/ongoing/new item is one short sentence. Use as many items as needed to cover all topics.
- Avoid generic wording like "fluctuating emotions" or "various concerns" - be specific.
- Do not invent details not present in the input."""

TAG_PROMPT_PREFIX = ("You are summarizing facts about ONE topic: {tag_name}. "
                     "Be thorough about this specific topic.\n\n")
OTHER_TAG = "other"


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
    merged["narrative"] = "\n".join(p["narrative"] for p in parts if p["narrative"])
    return merged


async def _summarise(llm: LLMClient, items: list[dict[str, Any]],
                     period_type: str, period_id: str,
                     capability: str = "medium",
                     system: str = SUMMARY_SYSTEM,
                     model: str | None = None) -> dict[str, Any] | None:
    threshold, bsize = _CAPABILITY_BATCH.get(capability, (BATCH_THRESHOLD, BATCH_SIZE))
    batches = ([items] if len(items) <= threshold
               else [items[i:i + bsize] for i in range(0, len(items), bsize)])
    parts: list[dict[str, Any]] = []
    for batch in batches:
        lines = [f"[{it['id']}] ({it['status']}) {it['text']}" for it in batch]
        msg = f"Period: {period_type} {period_id}\nItems:\n" + "\n".join(lines)
        parsed = _parse_summary(await llm.generate(msg, system, model=model))
        if parsed is None:
            log.warning("summary: unparseable LLM output for %s %s", period_type, period_id)
            return None  # abort: never archive on partial failure
        parts.append(parsed)
    return parts[0] if len(parts) == 1 else _merge(parts)


def _tag_of(item: dict[str, Any]) -> str:
    """First tag of the item (JSON array or legacy plain string), for grouping."""
    return first_tag(item.get("tag")) or OTHER_TAG


def _tag_bullets(narrative: str, tag: str) -> str:
    """Ensure every non-empty narrative line is a ``- [tag] ...`` bullet."""
    out = []
    for line in narrative.splitlines():
        line = line.strip()
        if not line:
            continue
        body = line[1:].strip() if line[0] in "-*" else line
        out.append(f"- {body}" if body.startswith("[") else f"- [{tag}] {body}")
    return "\n".join(out)


async def _summarise_by_tag(llm: LLMClient, items: list[dict[str, Any]],
                            period_type: str, period_id: str,
                            capability: str = "medium",
                            model: str | None = None) -> dict[str, Any] | None:
    """Group items by tag, summarise each group, merge with ``[tag]`` labels."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        groups.setdefault(_tag_of(it), []).append(it)
    # Nothing is tagged: behave exactly like the untagged flow (no label).
    label = list(groups) != [OTHER_TAG]
    parts: list[dict[str, Any]] = []
    for tag, group in groups.items():
        system = TAG_PROMPT_PREFIX.format(tag_name=tag) + SUMMARY_SYSTEM
        part = await _summarise(llm, group, period_type, period_id, capability, system=system, model=model)
        if part is None:
            return None  # abort: never archive on partial failure
        if label and part["narrative"]:
            part = {**part, "narrative": _tag_bullets(part["narrative"], tag)}
        parts.append(part)
    if not parts:
        return None
    merged = _merge(parts)
    merged["narrative"] = "\n".join(p["narrative"] for p in parts if p["narrative"])
    merged["tag_counts"] = {t: len(g) for t, g in groups.items()}
    merged["source_count"] = len(items)
    merged["message_count"] = len({it["source_message_id"] for it in items
                                   if it.get("source_message_id") is not None})
    return merged


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
                          period_id: str, visibility: str | None = None) -> bool:
    vis_sql, vis_args = ("", ())
    if visibility is not None:
        vis_sql, vis_args = " AND visibility=?", (visibility,)
    async with db.execute(
        "SELECT 1 FROM facts WHERE kind=? AND owner_id IS ? AND relationship_id IS ? "
        "AND json_extract(metadata,'$.period')=?" + vis_sql,
        (period_type, owner_id, relationship_id, period_id, *vis_args),
    ) as cur:
        return await cur.fetchone() is not None


async def _collect_sources(db: aiosqlite.Connection, user_id: int,
                           relationship_id: str | None, period_type: str,
                           start: date, end: date,
                           visibility: str | None = None) -> list[dict[str, Any]]:
    vis_sql, vis_args = ("", ())
    if visibility is not None:
        vis_sql, vis_args = " AND visibility = ?", (visibility,)
    rel_sql, rel_args = ("", ())
    if relationship_id is not None:
        rel_sql, rel_args = " AND relationship_id = ?", (relationship_id,)

    async with db.execute("PRAGMA table_info(facts)") as cur:
        cols = await cur.fetchall()
    has_tag = any(r[1] == "tag" for r in cols)
    tag_col = ", tag" if has_tag else ""
    has_smid = any(r[1] == "source_message_id" for r in cols)
    if has_smid:
        tag_col += ", source_message_id"

    # working facts created in the window (active + resolved, any msg_source)
    async with db.execute(
        f"SELECT id, text, status, created_at{tag_col} FROM facts WHERE owner_id=? AND kind='working' "
        "AND status IN ('active','resolved') AND archived_at IS NULL "
        "AND created_at >= ? AND created_at < ?" + rel_sql + vis_sql
        + " ORDER BY created_at, id",
        (user_id, start.isoformat(), end.isoformat(), *rel_args, *vis_args),
    ) as cur:
        items = [dict(r) for r in await cur.fetchall()]

    # lower-level summaries whose own window starts inside this window
    lower = _ORDER[:_ORDER.index(period_type)]
    if lower:
        marks = ",".join("?" * len(lower))
        async with db.execute(
            f"SELECT id, text, status, created_at, metadata FROM facts WHERE owner_id=? "
            f"AND kind IN ({marks}) AND archived_at IS NULL" + rel_sql + vis_sql
            + " ORDER BY id",
            (user_id, *lower, *rel_args, *vis_args),
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
                 source_ids: list[int], archive: bool,
                 visibility: str = "private") -> int:
    text = summary["narrative"] or "; ".join(
        summary["resolved"] + summary["ongoing"] + summary["new"])
    meta = {"period": period_id, "start": start.isoformat(), "end": end.isoformat(),
            **summary}
    cur = await db.execute(
        "INSERT INTO facts (owner_id, relationship_id, text, visibility, kind, status, "
        "msg_source, source_ids, metadata) VALUES (?,?,?,?,?,?,?,?,?)",
        (owner_id, relationship_id, text, visibility, period_type, "active", "system",
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
    model: str | None = None,
) -> int | None:
    """Create a summary for a specific period. Returns fact id or None if skipped.

    *model*: optional model override for the summary LLM call (e.g. a heavier model).
    """
    start, end = period_window(period_type, period_id)

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        if period_type == "daily":
            async with db.execute(
                "SELECT COUNT(*) FROM messages WHERE user_id=? AND role='user' "
                "AND deleted_at IS NULL AND created_at >= ? AND created_at < ?",
                (user_id, start.isoformat(), end.isoformat()),
            ) as cur:
                n_msgs = (await cur.fetchone())[0]
            if n_msgs < DAILY_MIN_USER_MESSAGES:
                return None

        # One independent stream per visibility: private first, then shared.
        last_id: int | None = None
        for visibility in SUMMARY_VISIBILITIES:
            if await _summary_exists(db, user_id, relationship_id, period_type,
                                     period_id, visibility):
                continue
            items = await _collect_sources(
                db, user_id, relationship_id, period_type, start, end, visibility)
            if len(items) < MIN_ITEMS:
                continue  # empty stream: skip, no LLM call
            summary = await _summarise_by_tag(llm, items, period_type, period_id, model=model)
            if summary is None:
                continue
            last_id = await _store(
                db, owner_id=user_id, relationship_id=relationship_id,
                period_type=period_type, period_id=period_id, start=start, end=end,
                summary=summary, source_ids=[i["id"] for i in items], archive=True,
                visibility=visibility)
        return last_id


async def run_periodic_summaries(
    db_path: str, llm: LLMClient, user_id: int,
    relationship_id: str | None = None,
    *, now: datetime | None = None,
    intervals: list[str] | None = None,
    model: str | None = None,
) -> dict[str, int]:
    """Run all due summaries for a user. Returns {period_type: count_created}.

    *model*: optional model override for all summary LLM calls.
    """
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
            if await create_summary(db_path, llm, user_id, period_type, pid, relationship_id, model=model):
                created[period_type] = created.get(period_type, 0) + 1
    return created


async def create_couple_summary(
    db_path: str, llm: LLMClient, relationship_id: str,
    period_type: str, period_id: str,
) -> int | None:
    """Create a couple-level summary combining both partners' perspectives.

    Built from each partner's SHARED-visibility summary of the same period
    (private summaries are never read). Partner summaries are
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
            "AND visibility='shared' "
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


# ---------------------------------------------------------------------------
# Rolling pre-computed summaries
# ---------------------------------------------------------------------------
# One ``kind='rolling'`` fact per (owner, relationship, stream, level). The
# daily maintenance run replaces it in place, so the prompt never has to
# assemble history per message. Rolling at level N compresses what is still
# unarchived at level N-1 (the in-progress period); the first level compresses
# pending working facts and the top level also folds in its own completed
# summaries. Every unarchived item is therefore covered exactly once.
#
# Streams mirror the 3-stream design and never mix sources:
#   private -- owner's private-visibility items
#   shared  -- owner's shared-visibility items
#   couple  -- couple summaries (owner NULL) of the level itself that are not yet
#              covered by a higher-level couple summary (they are never
#              archived); built only from shared partner summaries

ROLLING_KIND = "rolling"
ROLLING_STREAMS = ("private", "shared", "couple")
_BULLET_RE = re.compile(r"^[-*]?\s*\[([^\]]+)\]\s*(.+)$")


def _bullet_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Split summary narratives into one item per bullet, keeping its ``[tag]``."""
    items: list[dict[str, Any]] = []
    for r in rows:
        for line in (r.get("text") or "").splitlines():
            line = line.strip()
            if not line:
                continue
            m = _BULLET_RE.match(line)
            tag, body = (m[1].strip(), m[2].strip()) if m else (None, line.lstrip("-* ").strip())
            if body:
                items.append({"id": r["id"], "status": "active", "text": body, "tag": tag})
    return items


async def _rolling_sources(
    db: aiosqlite.Connection, stream: str, user_id: int,
    relationship_id: str | None, levels: list[str], idx: int,
) -> tuple[list[dict[str, Any]], list[int]]:
    """Items and distinct source fact ids feeding rolling level ``levels[idx]``."""
    level = levels[idx]
    lower = levels[idx - 1] if idx > 0 else None
    is_top = idx == len(levels) - 1
    rel_sql, rel_args = ("", ())
    if relationship_id is not None:
        rel_sql, rel_args = " AND relationship_id = ?", (relationship_id,)

    items: list[dict[str, Any]] = []
    ids: list[int] = []

    if stream in ("private", "shared"):
        if lower is None:
            async with db.execute("PRAGMA table_info(facts)") as cur:
                tag_col = ", tag" if any(r[1] == "tag" for r in await cur.fetchall()) else ""
            async with db.execute(
                f"SELECT id, text, status{tag_col} FROM facts WHERE owner_id=? AND kind='working' "
                "AND status IN ('active','resolved') AND archived_at IS NULL "
                "AND visibility=?" + rel_sql + " ORDER BY created_at, id",
                (user_id, stream, *rel_args),
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
            items.extend(rows)
            ids.extend(r["id"] for r in rows)
        kinds = ([lower] if lower else []) + ([level] if is_top else [])
        if kinds:
            marks = ",".join("?" * len(kinds))
            async with db.execute(
                f"SELECT id, text FROM facts WHERE owner_id=? AND kind IN ({marks}) "
                "AND archived_at IS NULL AND visibility=?" + rel_sql + " ORDER BY id",
                (user_id, *kinds, stream, *rel_args),
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
            items.extend(_bullet_items(rows))
            ids.extend(r["id"] for r in rows)
    else:  # couple
        if relationship_id is None:
            return [], []
        # Couple summaries are never archived, so "not yet folded into the next
        # level up" (the cutoff below) stands in for "unarchived".
        rows = []
        for kind in [level]:
            higher = levels[levels.index(kind) + 1] if kind != levels[-1] else None
            cutoff: str | None = None
            if higher:
                async with db.execute(
                    "SELECT MAX(json_extract(metadata,'$.end')) FROM facts WHERE kind=? "
                    "AND owner_id IS NULL AND relationship_id=?",
                    (higher, relationship_id),
                ) as cur:
                    cutoff = (await cur.fetchone())[0]
            async with db.execute(
                "SELECT id, text, metadata FROM facts WHERE kind=? AND owner_id IS NULL "
                "AND relationship_id=? AND archived_at IS NULL ORDER BY id",
                (kind, relationship_id),
            ) as cur:
                for r in await cur.fetchall():
                    start = _meta(dict(r)).get("start")
                    if cutoff and (not start or start < cutoff):
                        continue  # already folded into a higher-level couple summary
                    rows.append(dict(r))
        items.extend(_bullet_items(rows))
        ids.extend(r["id"] for r in rows)
    return items, sorted(set(ids))


async def _replace_rolling(
    db: aiosqlite.Connection, *, owner_id: int | None, relationship_id: str | None,
    stream: str, level: str, summary: dict[str, Any] | None, source_ids: list[int],
) -> int | None:
    """Delete the previous rolling row for this key and insert the new one."""
    await db.execute(
        "DELETE FROM facts WHERE kind=? AND owner_id IS ? AND relationship_id IS ? "
        "AND json_extract(metadata,'$.stream')=? AND json_extract(metadata,'$.level')=?",
        (ROLLING_KIND, owner_id, relationship_id, stream, level),
    )
    new_id: int | None = None
    if summary is not None:
        text = summary["narrative"] or "; ".join(
            summary["resolved"] + summary["ongoing"] + summary["new"])
        meta = {"rolling": True, "level": level, "period_type": f"rolling_{level}",
                "stream": stream, "computed_at": _now_iso(), **summary}
        cur = await db.execute(
            "INSERT INTO facts (owner_id, relationship_id, text, visibility, kind, status, "
            "msg_source, source_ids, metadata) VALUES (?,?,?,?,?,?,?,?,?)",
            (owner_id, relationship_id, text,
             "shared" if stream == "shared" else "private", ROLLING_KIND, "active",
             "system", json.dumps(source_ids), json.dumps(meta)),
        )
        new_id = cur.lastrowid
    await db.commit()
    return new_id


async def compute_rolling_summaries(
    db_path: str, llm: LLMClient, user_id: int,
    relationship_id: str | None = None,
    *, intervals: list[str] | None = None, capability: str = "medium",
) -> dict[str, int]:
    """Recompute the rolling summary of every level and stream for *user_id*.

    Returns ``{"<stream>/<level>": fact_id}`` for rows written. A key whose
    sources are unchanged since the last run is skipped (no LLM call); a key
    with no sources loses its stale row; an unparseable LLM answer keeps the
    previous row.
    """
    levels = [lv for lv in _ORDER if lv in set(intervals or ACTIVE_INTERVALS)]
    written: dict[str, int] = {}
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        for stream in ROLLING_STREAMS:
            owner = None if stream == "couple" else user_id
            for idx, level in enumerate(levels):
                items, source_ids = await _rolling_sources(
                    db, stream, user_id, relationship_id, levels, idx)
                async with db.execute(
                    "SELECT source_ids FROM facts WHERE kind=? AND owner_id IS ? "
                    "AND relationship_id IS ? AND json_extract(metadata,'$.stream')=? "
                    "AND json_extract(metadata,'$.level')=?",
                    (ROLLING_KIND, owner, relationship_id, stream, level),
                ) as cur:
                    existing = await cur.fetchone()
                if not items:
                    if existing:
                        await _replace_rolling(
                            db, owner_id=owner, relationship_id=relationship_id,
                            stream=stream, level=level, summary=None, source_ids=[])
                    continue
                if existing and json.loads(existing["source_ids"] or "[]") == source_ids:
                    continue  # nothing new since the last run
                summary = await _summarise_by_tag(
                    llm, items, f"rolling_{level}", "current", capability)
                if summary is None:
                    continue  # keep the previous rolling row
                new_id = await _replace_rolling(
                    db, owner_id=owner, relationship_id=relationship_id, stream=stream,
                    level=level, summary=summary, source_ids=source_ids)
                if new_id:
                    written[f"{stream}/{level}"] = new_id
    return written


async def run_rolling_summaries(
    db_path: str, llm: LLMClient, user_id: int,
    relationship_id: str | None = None, **kwargs: Any,
) -> dict[str, int]:
    """Daily-maintenance hook around :func:`compute_rolling_summaries`."""
    written = await compute_rolling_summaries(db_path, llm, user_id, relationship_id, **kwargs)
    if written:
        log.info("rolling summaries: user %s rel %s wrote %s", user_id, relationship_id,
                 sorted(written))
    return written
