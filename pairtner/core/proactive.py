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
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from pairtner.storage.helpers import _fetch_all, _now_iso

log = logging.getLogger(__name__)

# Date patterns in fact text
_DATE_PATTERNS = [
    # "DOB: 1992-03-15", "Partner birthday: 2027-01-20", "Anniversary: 2020-06-15"
    re.compile(r"(?:DOB|birthday|anniversary|deadline|due|goal.?date)\s*[:]\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE),
    # Also match "... birthday 2027-01-20" without colon
    re.compile(r"(?:DOB|birthday|anniversary|deadline|due)\s+(\d{4}-\d{2}-\d{2})", re.IGNORECASE),
    # "birthday is March 15" or "anniversary on June 20"
    re.compile(r"(?:birthday|anniversary)\s+(?:is\s+)?(?:on\s+)?(\w+\s+\d{1,2})", re.IGNORECASE),
    # Bare ISO date anywhere in a core fact that mentions birthday/anniversary/DOB
    re.compile(r"(\d{4}-\d{2}-\d{2})", re.IGNORECASE),
]

# Month name → number
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


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
      "⏰ Partner's birthday is in 3 days (March 15)"
      "⚠️ Unresolved: 'dishes argument' — 5 days without discussion"

    Returns empty list if nothing needs injection.
    """
    injections: list[str] = []
    now = datetime.now(timezone.utc)
    today = datetime.now().date()  # local date, not UTC (dates are local concept)

    # 1. Date-approaching facts
    date_facts = await _find_date_facts(db_path, user_id)
    for fact_text, target_date in date_facts:
        days_until = (target_date - today).days
        if 0 <= days_until <= lookahead_days:
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
    db_path: str, user_id: int,
) -> list[tuple[str, Any]]:
    """Find facts containing parseable dates."""
    from datetime import date

    rows = await _fetch_all(
        db_path,
        "SELECT text FROM facts WHERE owner_id = ? AND archived_at IS NULL AND kind = 'core'",
        (user_id,),
    )

    results = []
    now_year = datetime.now(timezone.utc).year

    for row in rows:
        text = row.get("text", "")
        # Only scan facts that mention date-related keywords
        lower = text.lower()
        if not any(kw in lower for kw in ("dob", "birthday", "anniversary", "deadline", "due", "goal")):
            continue
        for pattern in _DATE_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            raw_date = match.group(1)
            parsed = _parse_date(raw_date, now_year)
            if parsed:
                results.append((text, parsed))
                break

    return results


def _parse_date(raw: str, current_year: int) -> Any:
    """Parse a date string into a date object, handling various formats."""
    from datetime import date

    # Try ISO format: 1992-03-15
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d").date()
        # For recurring dates (birthdays), use this year or next
        this_year = dt.replace(year=current_year)
        if this_year < date.today():
            return dt.replace(year=current_year + 1)
        return this_year
    except ValueError:
        pass

    # Try "March 15" format
    parts = raw.strip().split()
    if len(parts) == 2:
        month_name = parts[0].lower()
        try:
            day = int(parts[1])
        except ValueError:
            return None
        month = _MONTHS.get(month_name)
        if month:
            try:
                this_year = date(current_year, month, day)
                if this_year < date.today():
                    return date(current_year + 1, month, day)
                return this_year
            except ValueError:
                pass

    return None


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
