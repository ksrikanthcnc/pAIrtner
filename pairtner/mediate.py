"""Pure helpers for /mediate: window parsing and perception filtering.

No I/O here. ``filter_perceptions`` is deterministic so weak models that
misattribute a partner's own words as a theme about the other partner are
handled at read time, without changing stored data or tuning prompts.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

MAX_THEME_CHARS = 160

# First-person markers (English + Telugu). English words are matched as
# standalone tokens; Telugu words as whitespace-separated tokens.
_FIRST_PERSON_EN = {"i", "i'm", "i've", "i'll", "i'd", "my", "me", "mine", "myself"}
_FIRST_PERSON_TE = {"నేను", "నా", "నాకు", "నన్ను"}
_FIRST_PERSON = _FIRST_PERSON_EN | _FIRST_PERSON_TE

# \w would split Telugu words at combining vowel signs, so split on separators.
_TOKEN_RE = re.compile(r"[^\s.,;:!?\"()\[\]{}<>/\\|*_\-–—…]+")
_WINDOW_RE = re.compile(r"^(\d+)\s*([dw]?)$")


def parse_window(
    arg: str | None, default_days: int, max_days: int
) -> tuple[int, str] | None:
    """Parse a /mediate window argument into ``(days, label)``.

    Accepts nothing (default), ``week``, ``month``, ``Nd``, ``Nw`` or a bare
    integer N (days). Values above ``max_days`` are clamped. Returns None for
    an unusable argument (including 0).
    """
    raw = (arg or "").strip().lower()
    if not raw:
        days = default_days
    elif raw == "week":
        days = 7
    elif raw == "month":
        days = 30
    else:
        m = _WINDOW_RE.match(raw)
        if not m:
            return None
        days = int(m.group(1)) * (7 if m.group(2) == "w" else 1)
        if days <= 0:
            return None
    days = max(1, min(days, max_days))
    label = "the last day" if days == 1 else f"the last {days} days"
    return days, label


def since_timestamp(days: int, now: datetime | None = None) -> str:
    """UTC timestamp ``days`` ago in SQLite's ``YYYY-MM-DD HH:MM:SS`` format."""
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _tokens(text: str) -> list[str]:
    return [tok.strip("'") for tok in _TOKEN_RE.findall(text.lower().replace("’", "'")) if tok.strip("'")]


def _normalise(text: str) -> str:
    return " ".join(_tokens(text))


def is_first_person(text: str) -> bool:
    """True if the text talks about the speaker (contains a first-person word)."""
    return any(tok in _FIRST_PERSON for tok in _tokens(text))


def overlaps_own_fact(theme: str, own_facts: Iterable[str]) -> bool:
    """True if the theme closely matches one of the observer's own facts."""
    t_norm = _normalise(theme)
    t_set = set(t_norm.split())
    if not t_set:
        return False
    for fact in own_facts:
        f_norm = _normalise(fact)
        f_set = set(f_norm.split())
        if not f_set:
            continue
        jaccard = len(t_set & f_set) / len(t_set | f_set)
        if jaccard >= 0.6:
            return True
        shorter, longer = sorted((t_norm, f_norm), key=len)
        if len(shorter.split()) >= 4 and shorter in longer:
            return True
    return False


def filter_perceptions(
    rows: list[dict[str, Any]],
    observer_facts: Iterable[str],
    min_confidence: float,
    max_per_side: int,
) -> list[str]:
    """Return clean theme strings from one observer's perception rows.

    ``rows`` are expected newest first. Drops low-confidence, first-person and
    own-fact-duplicate themes, de-duplicates by normalised text, truncates to
    160 chars and caps at ``max_per_side``.
    """
    facts = list(observer_facts)
    seen: set[str] = set()
    out: list[str] = []
    for row in rows:
        theme = (row.get("theme") or "").strip()
        if not theme:
            continue
        conf = row.get("confidence")
        if conf is not None and conf < min_confidence:
            continue
        if is_first_person(theme) or overlaps_own_fact(theme, facts):
            continue
        key = _normalise(theme)
        if key in seen:
            continue
        seen.add(key)
        out.append(theme[:MAX_THEME_CHARS])
        if len(out) >= max_per_side:
            break
    return out
