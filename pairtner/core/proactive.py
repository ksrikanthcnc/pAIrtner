"""Proactive context injection — facts the LLM should see regardless of score.

Scans knowledge for time-sensitive or attention-worthy facts and marks them
for injection into the next conversation context. The context builder includes
these unconditionally, bypassing the score-based ranking.

Use cases:
- Approaching dates (birthdays, anniversaries, deadlines)
- Unresolved conflicts older than N days
- Goals with approaching deadlines
- Partner inactivity (context, not notification)

This module is additive — it reads from the knowledge table and returns
injection candidates. It does NOT modify the context builder's core logic.

Date detection is LLM-driven: the extraction contract includes ``date`` and
``recurring`` fields.  Facts with structured dates are stored in
``event_date`` / ``recurring`` columns and queried directly — no regex
parsing of prose text.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from pairtner.storage.helpers import _fetch_all, _now_iso

log = logging.getLogger(__name__)


async def get_proactive_injections(
    db_path: str,
    user_id: int,
    *,
    lookahead_days: int = 7,
    conflict_stale_days: int = 5,
) -> list[str]:
    """Return context lines for proactive injection.

    These are added to the LLM prompt unconditionally, before scored facts.
    Each line is a formatted string like:
      "⏰ Partner's birthday is in 3 days (2027-01-20)"
      "⚠️ Unresolved: 'dishes argument' — 5 days without discussion"

    Returns empty list if nothing needs injection.
    """
    injections: list[str] = []
    now = datetime.now(timezone.utc)
    today = datetime.now().date()  # local date (dates are a local concept)

    # 1. Date-approaching facts — query the structured event_date column
    date_facts = await _find_date_facts(db_path, user_id, today, lookahead_days)
    for fact_text, target_date in date_facts:
        days_until = (target_date - today).days
        if days_until == 0:
            injections.append(f"⏰ TODAY: {fact_text}")
        elif days_until == 1:
            injections.append(f"⏰ TOMORROW: {fact_text}")
        else:
            injections.append(f"⏰ In {days_until} days: {fact_text}")

    # 2. Stale unresolved working facts (conflicts, tensions)
    stale = await _find_stale_active(db_path, user_id, conflict_stale_days)
    for fact in stale:
        age = (now - _parse_dt(fact.get("created_at", ""))).days
        injections.append(
            f"⚠️ Unresolved ({age}d): {fact['text']}"
        )

    return injections


async def _find_date_facts(
    db_path: str,
    user_id: int,
    today: date,
    lookahead_days: int,
) -> list[tuple[str, date]]:
    """Find facts with structured event_date within the lookahead window.

    For recurring dates (birthdays, anniversaries), the date is adjusted to
    this year or next year.  For one-time dates (deadlines), the exact date
    is used.
    """
    rows = await _fetch_all(
        db_path,
        "SELECT text, event_date, recurring FROM facts "
        "WHERE owner_id = ? AND archived_at IS NULL AND event_date IS NOT NULL",
        (user_id,),
    )

    results: list[tuple[str, date]] = []
    for row in rows:
        raw = row.get("event_date", "")
        if not raw:
            continue
        try:
            parsed = datetime.strptime(raw, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue

        is_recurring = bool(row.get("recurring", 0))
        if is_recurring:
            # For recurring dates, find this year's or next year's occurrence
            this_year = parsed.replace(year=today.year)
            if this_year < today:
                target = parsed.replace(year=today.year + 1)
            else:
                target = this_year
        else:
            target = parsed

        days_until = (target - today).days
        if 0 <= days_until <= lookahead_days:
            results.append((row["text"], target))

    return results


async def _find_stale_active(
    db_path: str, user_id: int, stale_days: int,
) -> list[dict[str, Any]]:
    """Find active working facts that haven't been discussed recently.

    These are potential unresolved conflicts/tensions worth surfacing.
    Looks for facts with high intensity (>0.6) that are older than stale_days
    and haven't been cited recently.
    """
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=stale_days)).isoformat()

    rows = await _fetch_all(
        db_path,
        "SELECT * FROM facts WHERE owner_id = ? AND archived_at IS NULL "
        "AND kind = 'working' AND status = 'active' "
        "AND COALESCE(intensity, 0.5) > 0.6 "
        "AND created_at < ? "
        "AND (last_cited_at IS NULL OR last_cited_at < ?)",
        (user_id, cutoff, cutoff),
    )

    return rows


def _parse_dt(iso: str) -> datetime:
    """Parse ISO timestamp, defaulting to epoch if unparseable."""
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return datetime(2020, 1, 1, tzinfo=timezone.utc)
