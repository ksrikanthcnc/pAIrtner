"""Memory diagnostics behind the /debug and /warnings commands.

Read-only queries over the v2 ``facts`` table, scoped to one user (their own
facts plus their relationship's summaries).  Formatting lives here too so the
handlers stay thin.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

import aiosqlite

from pairtner.core.memory import detect_contradiction, is_near_duplicate

STALE_DAYS = 60
_FACT_KINDS = ("core", "working")
_MAX_LISTED = 5
_MAX_DUP_SCAN = 300  # pairwise scan cap per user (O(n^2))


async def _scope(db: aiosqlite.Connection, user_id: int) -> tuple[str, tuple]:
    """SQL fragment + params selecting this user's facts and relationship summaries."""
    cur = await db.execute(
        "SELECT id FROM relationships WHERE (user_a_id = ? OR user_b_id = ?) "
        "AND status = 'active' LIMIT 1", (user_id, user_id),
    )
    row = await cur.fetchone()
    if row:
        return "(owner_id = ? OR relationship_id = ?)", (user_id, row[0])
    return "owner_id = ?", (user_id,)


def _short(text: str, n: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


# ── /debug ─────────────────────────────────────────────────────────────────

async def collect_debug(db_path: str, user_id: int) -> dict[str, Any]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        scope, params = await _scope(db, user_id)

        async def rows(sql: str, extra: tuple = ()) -> list[aiosqlite.Row]:
            cur = await db.execute(sql.format(scope=scope), params + extra)
            return await cur.fetchall()

        by_kind = {r["kind"]: r["n"] for r in await rows(
            "SELECT kind, COUNT(*) n FROM facts WHERE {scope} GROUP BY kind")}
        by_status = {r["status"]: r["n"] for r in await rows(
            "SELECT status, COUNT(*) n FROM facts WHERE {scope} "
            "AND kind IN ('core','working') GROUP BY status")}
        span = (await rows(
            "SELECT MIN(created_at) lo, MAX(created_at) hi FROM facts "
            "WHERE {scope} AND kind IN ('core','working')"))[0]

        fts: dict[str, Any] = {"present": False}
        cur = await db.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'facts_fts'")
        if await cur.fetchone():
            fts["present"] = True
            fts["rows"] = (await (await db.execute(
                "SELECT COUNT(*) FROM facts_fts")).fetchone())[0]
            fts["facts"] = (await (await db.execute(
                "SELECT COUNT(*) FROM facts")).fetchone())[0]
    return {
        "by_kind": by_kind, "by_status": by_status,
        "oldest": span["lo"], "newest": span["hi"], "fts": fts,
    }


def format_debug(data: dict[str, Any], *, pack_name: str, pack_version: str,
                 provider: str, model: str) -> str:
    kinds = data["by_kind"]
    fact_total = sum(kinds.get(k, 0) for k in _FACT_KINDS)
    summaries = {k: v for k, v in kinds.items() if k not in _FACT_KINDS}

    lines = ["🔧 Memory debug", ""]
    lines.append(f"Facts: {fact_total}")
    for k in _FACT_KINDS:
        lines.append(f"  • {k}: {kinds.get(k, 0)}")
    lines.append("")
    lines.append("By status:")
    if data["by_status"]:
        for st, n in sorted(data["by_status"].items()):
            lines.append(f"  • {st}: {n}")
    else:
        lines.append("  • none")
    lines.append("")
    lines.append(f"Oldest fact: {str(data['oldest'] or '—')[:10]}")
    lines.append(f"Newest fact: {str(data['newest'] or '—')[:10]}")
    lines.append("")
    fts = data["fts"]
    if not fts["present"]:
        lines.append("FTS5 index: missing")
    else:
        sync = "in sync" if fts["rows"] == fts["facts"] else (
            f"OUT OF SYNC ({fts['rows']} indexed vs {fts['facts']} facts)")
        lines.append(f"FTS5 index: present, {sync}")
    lines.append("")
    lines.append(f"Summaries: {sum(summaries.values())}")
    for k, n in sorted(summaries.items()):
        lines.append(f"  • {k}: {n}")
    lines.append("")
    lines.append(f"Pack: {pack_name} v{pack_version}")
    lines.append(f"LLM: {provider} / {model}")
    return "\n".join(lines)


# ── /warnings ──────────────────────────────────────────────────────────────

def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _find_contradictions(facts: list[dict]) -> list[tuple[dict, dict]]:
    out = []
    for i, a in enumerate(facts):
        for b in facts[i + 1:]:
            if detect_contradiction(a["text"], b["text"]):
                out.append((a, b))
    return out


def _find_near_duplicates(facts: list[dict]) -> list[tuple[dict, dict]]:
    out = []
    for i, a in enumerate(facts):
        for b in facts[i + 1:]:
            if is_near_duplicate(a["text"], b["text"]):
                out.append((a, b))
    return out


async def collect_warnings(
    db_path: str, user_id: int, *, now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        scope, params = await _scope(db, user_id)
        cur = await db.execute(
            f"SELECT * FROM facts WHERE {scope} ORDER BY id", params)
        allrows = [dict(r) for r in await cur.fetchall()]

    active = [f for f in allrows
              if f["kind"] in _FACT_KINDS and f["status"] == "active"
              and not f.get("archived_at")]
    scan = active[-_MAX_DUP_SCAN:]
    # contradiction/duplicate checks only make sense within one owner
    by_owner: dict[Any, list[dict]] = {}
    for f in scan:
        by_owner.setdefault(f["owner_id"], []).append(f)
    contradictions: list[tuple[dict, dict]] = []
    duplicates: list[tuple[dict, dict]] = []
    for group in by_owner.values():
        contradictions += _find_contradictions(group)
        duplicates += _find_near_duplicates(group)

    # a contradicting pair is reported as a contradiction, not as a duplicate
    contra_ids = {frozenset((a["id"], b["id"])) for a, b in contradictions}
    duplicates = [(a, b) for a, b in duplicates
                  if frozenset((a["id"], b["id"])) not in contra_ids]

    # explicit contradiction links recorded at write time
    ids = {f["id"] for f in active}
    linked = [f for f in active if f.get("contradicts_id") in ids]
    seen = {(a["id"], b["id"]) for a, b in contradictions} | {
        (b["id"], a["id"]) for a, b in contradictions}
    byid = {f["id"]: f for f in active}
    for f in linked:
        other = byid[f["contradicts_id"]]
        if (f["id"], other["id"]) not in seen:
            contradictions.append((other, f))

    cutoff = now - timedelta(days=STALE_DAYS)
    stale = []
    for f in active:
        if f["kind"] != "working" or (f.get("citation_count") or 0) > 0:
            continue
        created = _parse_ts(f.get("created_at"))
        if created and created < cutoff:
            stale.append(f)

    all_ids = {f["id"] for f in allrows}
    orphans = []
    for f in allrows:
        if f["kind"] in _FACT_KINDS:
            continue
        try:
            src = json.loads(f.get("source_ids") or "[]")
        except (json.JSONDecodeError, TypeError):
            src = []
        if not isinstance(src, list) or not src or not any(s in all_ids for s in src):
            orphans.append(f)

    summarised = set()
    for f in allrows:
        if f["kind"] != "monthly":
            continue
        try:
            period = (json.loads(f.get("metadata") or "{}") or {}).get("period")
        except (json.JSONDecodeError, TypeError, AttributeError):
            period = None
        if period:
            summarised.add(str(period))
    this_month = f"{now.year}-{now.month:02d}"
    months: dict[str, int] = {}
    for f in allrows:
        if f["kind"] not in _FACT_KINDS:
            continue
        created = _parse_ts(f.get("created_at"))
        if created:
            key = f"{created.year}-{created.month:02d}"
            if key < this_month:
                months[key] = months.get(key, 0) + 1
    missing = sorted(m for m in months if m not in summarised)

    return {
        "contradictions": contradictions, "duplicates": duplicates,
        "stale": stale, "orphans": orphans,
        "missing_summaries": [(m, months[m]) for m in missing],
    }


def format_warnings(w: dict[str, Any]) -> str:
    total = (len(w["contradictions"]) + len(w["duplicates"]) + len(w["stale"])
             + len(w["orphans"]) + len(w["missing_summaries"]))
    if total == 0:
        return "✅ No memory quality warnings."

    lines = ["⚠️ Memory warnings", ""]

    def section(title: str, items: list, render) -> None:
        lines.append(f"{title}: {len(items)}")
        for it in items[:_MAX_LISTED]:
            lines.append("  • " + render(it))
        if len(items) > _MAX_LISTED:
            lines.append(f"  … and {len(items) - _MAX_LISTED} more")
        lines.append("")

    section("Contradicting facts", w["contradictions"],
            lambda p: f"#{p[0]['id']} “{_short(p[0]['text'], 40)}” vs #{p[1]['id']} “{_short(p[1]['text'], 40)}”")
    lines.append(f"Near-duplicate facts: {len(w['duplicates'])} pair(s)")
    for a, b in w["duplicates"][:_MAX_LISTED]:
        lines.append(f"  • #{a['id']} ≈ #{b['id']}: {_short(a['text'], 50)}")
    if len(w["duplicates"]) > _MAX_LISTED:
        lines.append(f"  … and {len(w['duplicates']) - _MAX_LISTED} more")
    lines.append("")
    section(f"Stale working facts (>{STALE_DAYS}d, never cited)", w["stale"],
            lambda f: f"#{f['id']} {_short(f['text'])} ({str(f['created_at'])[:10]})")
    section("Orphaned summaries (no source facts)", w["orphans"],
            lambda f: f"#{f['id']} [{f['kind']}] {_short(f['text'])}")
    section("Months with facts but no summary", w["missing_summaries"],
            lambda m: f"{m[0]} ({m[1]} fact(s))")
    return "\n".join(lines).rstrip()
