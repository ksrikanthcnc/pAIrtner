"""Relationship health score (0-100) from messages, facts and nudges.

Pure SQL + arithmetic; no LLM or network calls. Results are stored in
``analysis_results`` (result_type='health_score', relationship-level).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)

NEUTRAL = 50.0
WEIGHTS = {
    "communication_balance": 0.20,
    "sentiment_trend": 0.25,
    "nudge_effectiveness": 0.15,
    "conflict_resolution": 0.20,
    "engagement": 0.20,
}

_POSITIVE_NUDGES = {"appreciation", "quality_time"}
_NEGATIVE_NUDGES = {"attention", "communication", "support", "conflict_prevention"}
_POS_RE = re.compile(
    r"\b(love[sd]?|happy|grateful|thank\w*|appreciat\w*|enjoy\w*|proud|"
    r"great|wonderful|fun|laugh\w*|kind|sweet|supportive|close)\b", re.I)
_NEG_RE = re.compile(
    r"\b(fight\w*|argu\w*|angry|upset|ignor\w*|hurt\w*|sad|frustrat\w*|lonely|"
    r"resent\w*|annoy\w*|yell\w*|stress\w*|conflict|tension|disappoint\w*|"
    r"neglect\w*|worried|anxious)\b", re.I)

# Days after which an unresolved negative fact counts as fully unresolved.
_UNRESOLVED_CAP_DAYS = 14.0
# Resolution time (days) at/above which a resolved conflict scores 0.
_SLOW_DAYS = 14.0


def _clamp(x: float) -> float:
    return max(0.0, min(100.0, x))


def valence(text: str, nudge_type: str | None = None, metadata: str | None = None) -> str:
    """Classify a fact as 'positive', 'negative' or 'neutral'."""
    if metadata:
        try:
            meta = json.loads(metadata)
        except ValueError:
            meta = None
        if isinstance(meta, dict) and meta.get("valence") in ("positive", "negative", "neutral"):
            return meta["valence"]
    if nudge_type in _POSITIVE_NUDGES:
        return "positive"
    if nudge_type in _NEGATIVE_NUDGES:
        return "negative"
    pos, neg = len(_POS_RE.findall(text or "")), len(_NEG_RE.findall(text or ""))
    if neg > pos:
        return "negative"
    if pos > neg:
        return "positive"
    return "neutral"


def score_communication(counts: list[int]) -> float:
    """100 = 50/50 split, 0 = one partner silent. No messages = neutral."""
    total = sum(counts)
    if total == 0:
        return NEUTRAL
    if len(counts) < 2:
        return 0.0
    return _clamp(2 * min(counts) / total * 100)


def score_sentiment(pos: float, neg: float) -> float:
    total = pos + neg
    if total == 0:
        return NEUTRAL
    return _clamp(NEUTRAL + NEUTRAL * (pos - neg) / total)


def score_nudges(addressed: int, total: int) -> float:
    return NEUTRAL if total == 0 else _clamp(addressed / total * 100)


def score_conflicts(resolved_days: list[float], unresolved_days: list[float]) -> float:
    """Average per-conflict score; resolved fast = 100, slow/unresolved = 0."""
    scores = [100 * (1 - min(d, _SLOW_DAYS) / _SLOW_DAYS) for d in resolved_days]
    scores += [50 * (1 - min(d, _UNRESOLVED_CAP_DAYS) / _UNRESOLVED_CAP_DAYS)
               for d in unresolved_days]
    return NEUTRAL if not scores else _clamp(sum(scores) / len(scores))


def score_engagement(active_days: list[int], window_days: int) -> float:
    """Blend of daily coverage and longest silent gap (days indexed 0..window-1)."""
    if not active_days:
        return NEUTRAL
    days = sorted(set(active_days))
    coverage = len(days) / window_days * 100
    points = [-1, *days, window_days]
    max_gap = max(b - a - 1 for a, b in zip(points, points[1:]))
    gap_score = 100 * (1 - max_gap / window_days)
    return _clamp(0.5 * coverage + 0.5 * gap_score)


async def compute_health_score(
    db_path: str, relationship_id: str, window_days: int = 30,
) -> dict[str, Any]:
    """Compute relationship health (0-100) from multiple dimensions."""
    window = f"-{int(window_days)} days"
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row

        async def rows(sql: str, *params: Any) -> list[aiosqlite.Row]:
            async with db.execute(sql, params) as cur:
                return await cur.fetchall()

        msgs = await rows(
            "SELECT user_id, COUNT(*) AS n FROM messages WHERE relationship_id = ? "
            "AND role = 'user' AND deleted_at IS NULL "
            "AND datetime(created_at) >= datetime('now', ?) GROUP BY user_id",
            relationship_id, window)
        rel = await rows("SELECT user_a_id, user_b_id FROM relationships WHERE id = ?",
                         relationship_id)
        counts = {r["user_id"]: r["n"] for r in msgs}
        if rel:
            for uid in (rel[0]["user_a_id"], rel[0]["user_b_id"]):
                counts.setdefault(uid, 0)

        day_rows = await rows(
            "SELECT DISTINCT CAST(julianday(date('now')) - julianday(date(created_at)) AS INTEGER) "
            "AS ago FROM messages WHERE relationship_id = ? AND role = 'user' "
            "AND deleted_at IS NULL AND datetime(created_at) >= datetime('now', ?)",
            relationship_id, window)
        active_days = [window_days - 1 - r["ago"] for r in day_rows
                       if 0 <= window_days - 1 - r["ago"] < window_days]

        facts = await rows(
            "SELECT text, intensity, nudge_type, metadata, status, "
            "julianday(COALESCE(updated_at, created_at)) - julianday(created_at) AS res_days, "
            "julianday('now') - julianday(created_at) AS age_days "
            "FROM facts WHERE relationship_id = ? AND kind IN ('working','core') "
            "AND msg_source != 'bot' AND datetime(created_at) >= datetime('now', ?)",
            relationship_id, window)

        nudges = await rows(
            "SELECT nudge_status, nudge_cancel_reason FROM facts WHERE relationship_id = ? "
            "AND nudge_target_id IS NOT NULL AND datetime(created_at) >= datetime('now', ?)",
            relationship_id, window)

    pos = neg = 0.0
    resolved: list[float] = []
    unresolved: list[float] = []
    for f in facts:
        v = valence(f["text"], f["nudge_type"], f["metadata"])
        w = f["intensity"] if f["intensity"] is not None else 0.5
        if v == "positive":
            pos += w
        elif v == "negative":
            neg += w
            if f["status"] == "resolved":
                resolved.append(max(0.0, f["res_days"] or 0.0))
            elif f["status"] == "active":
                unresolved.append(max(0.0, f["age_days"] or 0.0))

    decided = [n for n in nudges if n["nudge_status"] != "pending"]
    addressed = sum(
        1 for n in decided
        if n["nudge_status"] == "delivered"
        or (n["nudge_status"] == "cancelled"
            and "address" in (n["nudge_cancel_reason"] or "").lower()))

    dims = {
        "communication_balance": score_communication(list(counts.values())),
        "sentiment_trend": score_sentiment(pos, neg),
        "nudge_effectiveness": score_nudges(addressed, len(decided)),
        "conflict_resolution": score_conflicts(resolved, unresolved),
        "engagement": score_engagement(active_days, window_days),
    }
    total = sum(dims[k] * w for k, w in WEIGHTS.items())
    result: dict[str, Any] = {"total": round(total)}
    result.update({k: round(v) for k, v in dims.items()})
    result["computed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    result["window_days"] = window_days
    return result


async def store_health_score(db_path: str, relationship_id: str, score: dict[str, Any]) -> int:
    """Persist a score in analysis_results; returns the row id."""
    computed_at = score.get("computed_at") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "INSERT INTO analysis_results (relationship_id, result_type, content, source, "
            "first_seen, last_seen, created_at, updated_at) "
            "VALUES (?, 'health_score', ?, 'computed', ?, ?, ?, ?)",
            (relationship_id, json.dumps(score), computed_at, computed_at,
             computed_at, computed_at))
        await db.commit()
        return cur.lastrowid


async def get_latest_health_score(db_path: str, relationship_id: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT content FROM analysis_results WHERE relationship_id = ? "
            "AND result_type = 'health_score' ORDER BY datetime(created_at) DESC, id DESC LIMIT 1",
            (relationship_id,),
        ) as cur:
            row = await cur.fetchone()
    return json.loads(row[0]) if row else None


async def maybe_compute_weekly(
    db_path: str, relationship_id: str, window_days: int = 30, min_interval_days: int = 7,
) -> dict[str, Any] | None:
    """Compute+store a score unless one exists from the last ``min_interval_days``."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT 1 FROM analysis_results WHERE relationship_id = ? "
            "AND result_type = 'health_score' AND datetime(created_at) >= datetime('now', ?)",
            (relationship_id, f"-{min_interval_days} days"),
        ) as cur:
            if await cur.fetchone():
                return None
    score = await compute_health_score(db_path, relationship_id, window_days)
    await store_health_score(db_path, relationship_id, score)
    return score
