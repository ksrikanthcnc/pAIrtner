"""Context composer -- structural isolation for per-user LLM prompt context.

The engine can read every fact in the DB; the LLM sees only what this module
puts in the prompt. When composing user X's context:

* X's own facts (any visibility) are included.
* The partner's facts are included ONLY when visibility is
  ``shared``. Private partner facts are never
  selected, so they cannot reach the prompt.
* Nudge suggestions are injected as generic guidance (the ``nudge_suggestion``
  column). The source fact's ``text`` is never read for nudges.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Isolation guard
# ---------------------------------------------------------------------------

# The only visibilities that may cross from one partner to the other.
PARTNER_VISIBLE: tuple[str, ...] = ("shared",)

if "private" in PARTNER_VISIBLE:  # pragma: no cover - import-time guard
    raise RuntimeError("PARTNER_VISIBLE must never contain 'private'")

_PARTNER_VIS_SQL = ",".join(f"'{v}'" for v in PARTNER_VISIBLE)


def _assert_partner_safe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fail loudly if any row destined for the partner section is not shareable."""
    for r in rows:
        if r.get("visibility") not in PARTNER_VISIBLE:
            raise AssertionError(
                f"structural isolation violated: fact {r.get('id')} "
                f"visibility={r.get('visibility')!r} reached partner context"
            )
    return rows


SUMMARY_KINDS: tuple[str, ...] = (
    "daily", "triday", "weekly", "biweekly", "monthly",
    "bimonthly", "quarterly", "semiannual", "yearly",
    "biennium", "quinquennium", "decade",
)
_SUMMARY_RANK = {k: i for i, k in enumerate(SUMMARY_KINDS)}

# Word budgets per section (total ~1600).
DEFAULT_BUDGETS: dict[str, int] = {
    "core": 250,
    "working": 350,       # absorbed fts budget (was 300)
    "partner": 150,
    "nudge": 100,
    "summaries": 350,     # absorbed fts budget (was 250)
    "proactive": 50,
    "messages": 350,
}
TOTAL_WORD_BUDGET = sum(DEFAULT_BUDGETS.values())  # 1600

MAX_BUDGET_FACTOR = 2.5


def compute_budgets(context_tokens: int) -> dict[str, int]:
    """Scale DEFAULT_BUDGETS by model context length (floor 1x, cap 2.5x)."""
    factor = min(MAX_BUDGET_FACTOR, max(1.0, context_tokens / 4096))
    return {k: int(v * factor) for k, v in DEFAULT_BUDGETS.items()}


RECENT_MESSAGE_LIMIT = 20
CORE_FACT_CAP = 20
WORKING_TOP_K = 30
PROACTIVE_WINDOW_DAYS = 7


@dataclass
class ComposedContext:
    core_facts: list[str] = field(default_factory=list)
    working_facts: list[str] = field(default_factory=list)
    partner_facts: list[str] = field(default_factory=list)  # ONLY non-private
    nudge_suggestions: list[str] = field(default_factory=list)  # generic guidance
    summaries: list[str] = field(default_factory=list)
    proactive: list[str] = field(default_factory=list)
    recent_messages: list[tuple[str, str, str]] = field(default_factory=list)
    pending_nudge_count: int = 0
    core_cap_warning: str | None = None

    def total_words(self) -> int:
        n = 0
        for section in (
            self.core_facts, self.working_facts,
            self.partner_facts, self.nudge_suggestions, self.summaries,
            self.proactive,
        ):
            n += sum(len(s.split()) for s in section)
        n += sum(len(m[1].split()) for m in self.recent_messages)
        return n


def _apply_word_cap(items: list[str], cap: int) -> list[str]:
    """Keep items until the cumulative word count exceeds *cap*."""
    result: list[str] = []
    total = 0
    for item in items:
        words = len(item.split())
        if total + words > cap:
            break
        result.append(item)
        total += words
    return result


class ContextComposer:
    def __init__(
        self,
        db_path: str,
        budgets: dict[str, int] | None = None,
        context_tokens: int | None = None,
        rolling_summary_enabled: bool = True,
    ):
        self.db_path = db_path
        self.rolling_summary_enabled = rolling_summary_enabled
        base = compute_budgets(context_tokens) if context_tokens else DEFAULT_BUDGETS
        self.budgets = {**base, **(budgets or {})}

    async def build_context(
        self, user_id: int, relationship_id: str, current_message: str | None = None,
    ) -> ComposedContext:
        """Build the full prompt context for *user_id* within a relationship."""
        from pairtner.core.memory_api import compute_relevance_score

        b = self.budgets
        ctx = ComposedContext()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            async def fetch(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
                async with db.execute(sql, params) as cur:
                    return [dict(r) for r in await cur.fetchall()]

            # Relationship membership + partner id.
            rel = await fetch(
                "SELECT user_a_id, user_b_id FROM relationships WHERE id = ?",
                (relationship_id,),
            )
            partner_id: int | None = None
            if rel:
                a, c = rel[0]["user_a_id"], rel[0]["user_b_id"]
                if user_id not in (a, c):
                    raise PermissionError(
                        f"user {user_id} is not a member of relationship {relationship_id}"
                    )
                partner_id = c if user_id == a else a

            live = "archived_at IS NULL AND status NOT IN ('outdated')"

            # 1. Core facts (own, always).
            rows = await fetch(
                f"SELECT text FROM facts WHERE owner_id = ? AND kind = 'core' AND {live} "
                "ORDER BY created_at",
                (user_id,),
            )
            ctx.core_facts = _apply_word_cap([r["text"] for r in rows], b["core"])
            if len(rows) > CORE_FACT_CAP:
                urow = await fetch("SELECT name FROM users WHERE tg_id = ?", (user_id,))
                user_name = (urow[0]["name"] if urow else None) or "The user"
                ctx.core_cap_warning = (
                    f"Note: {user_name} has {len(rows)} permanent preferences/facts. "
                    "Consider reviewing which ones are still relevant."
                )

            # 2. Top-K working facts by score (own).
            rows = await fetch(
                f"SELECT * FROM facts WHERE owner_id = ? AND kind = 'working' AND {live}",
                (user_id,),
            )
            rows.sort(key=compute_relevance_score, reverse=True)
            working_rows = rows[:WORKING_TOP_K]
            ctx.working_facts = _apply_word_cap([r["text"] for r in working_rows], b["working"])

            # 3. Partner facts: structurally restricted to non-private.
            if partner_id is not None:
                prow = await fetch(
                    "SELECT id, text, visibility FROM facts "
                    "WHERE owner_id = ? AND relationship_id = ? "
                    f"AND visibility IN ({_PARTNER_VIS_SQL}) "
                    "AND kind IN ('working','core') "
                    f"AND {live} ORDER BY created_at DESC",
                    (partner_id, relationship_id),
                )
                _assert_partner_safe(prow)
                ctx.partner_facts = _apply_word_cap([r["text"] for r in prow], b["partner"])

            # 5. Pending nudge suggestions: suggestion column ONLY, never fact text.
            nrows = await fetch(
                "SELECT nudge_suggestion FROM facts "
                "WHERE nudge_target_id = ? AND nudge_status = 'pending' "
                "AND relationship_id = ? AND archived_at IS NULL "
                "AND nudge_suggestion IS NOT NULL AND nudge_suggestion != '' "
                "ORDER BY created_at",
                (user_id, relationship_id),
            )
            ctx.pending_nudge_count = len(nrows)
            seen: set[str] = set()
            sugg: list[str] = []
            for r in nrows:
                s = r["nudge_suggestion"].strip()
                if s not in seen:
                    seen.add(s)
                    sugg.append(s)
            ctx.nudge_suggestions = _apply_word_cap(sugg, b["nudge"])

            # 6 + 7. Summaries. Pre-computed rolling summaries (one per level,
            # at most ~5 entries) are preferred; otherwise assemble per level.
            rolling = await self._rolling_entries(fetch, user_id, relationship_id) \
                if self.rolling_summary_enabled else []
            if rolling:
                ctx.summaries = rolling
            else:
                kinds = ",".join(f"'{k}'" for k in SUMMARY_KINDS)
                srows = await fetch(
                    f"SELECT text, kind, created_at FROM facts WHERE kind IN ({kinds}) AND {live} "
                    "AND ((owner_id IS NULL AND relationship_id = ?) OR owner_id = ?)",
                    (relationship_id, user_id),
                )
                # Gap-filling assembly: one entry per level (newest rows first within
                # a level, joined), ordered finest -> coarsest. No word cap: entry
                # count is bounded by the number of levels.
                srows.sort(key=lambda r: r["created_at"] or "", reverse=True)
                by_kind: dict[str, list[str]] = {}
                for r in srows:
                    by_kind.setdefault(r["kind"], []).append(r["text"])
                ctx.summaries = [
                    f"({k}) " + "\n".join(by_kind[k])
                    for k in SUMMARY_KINDS if k in by_kind
                ]

            # 8. Proactive injections: own dated facts inside the window.
            today = date.today()
            horizon = today + timedelta(days=PROACTIVE_WINDOW_DAYS)
            drows = await fetch(
                f"SELECT text, event_date, recurring FROM facts WHERE owner_id = ? "
                f"AND event_date IS NOT NULL AND {live}",
                (user_id,),
            )
            proactive: list[tuple[date, str]] = []
            for r in drows:
                d = _next_occurrence(r["event_date"], bool(r["recurring"]), today)
                if d is not None and today <= d <= horizon:
                    when = "today" if d == today else d.isoformat()
                    proactive.append((d, f"Upcoming ({when}): {r['text']}"))
            proactive.sort(key=lambda x: x[0])
            ctx.proactive = _apply_word_cap([p[1] for p in proactive], b["proactive"])

            # 9. Recent messages (oldest first), newest kept under the word cap.
            mrows = await fetch(
                "SELECT role, content, created_at FROM messages "
                "WHERE user_id = ? AND relationship_id = ? AND deleted_at IS NULL "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (user_id, relationship_id, RECENT_MESSAGE_LIMIT),
            )
            kept: list[tuple[str, str, str]] = []
            words = 0
            for r in mrows:
                w = len(r["content"].split())
                if words + w > b["messages"] and kept:
                    break
                kept.append((r["role"], r["content"], str(r["created_at"] or "")))
                words += w
            ctx.recent_messages = list(reversed(kept))

        return ctx

    @staticmethod
    async def _rolling_entries(fetch, user_id: int, relationship_id: str) -> list[str]:
        """One entry per level from rolling rows visible to *user_id*.

        Visible: the user's own private/shared rolling rows plus the couple
        rolling (owner NULL). The partner's rolling rows are never selected.
        """
        rows = await fetch(
            "SELECT text, owner_id, visibility, metadata FROM facts WHERE kind = 'rolling' "
            "AND archived_at IS NULL AND status NOT IN ('outdated') "
            "AND ((owner_id = ? AND (relationship_id = ? OR relationship_id IS NULL)) "
            "OR (owner_id IS NULL AND relationship_id = ?)) ORDER BY id",
            (user_id, relationship_id, relationship_id),
        )
        by_level: dict[str, list[str]] = {}
        for r in rows:
            try:
                level = json.loads(r["metadata"] or "{}").get("level")
            except (json.JSONDecodeError, TypeError, AttributeError):
                level = None
            if level in _SUMMARY_RANK and r["text"] and r["text"] not in by_level.get(level, []):
                by_level.setdefault(level, []).append(r["text"])
        return [
            f"(rolling {lv}) " + "\n".join(by_level[lv])
            for lv in sorted(by_level, key=_SUMMARY_RANK.__getitem__)
        ]


def _next_occurrence(event_date: str, recurring: bool, today: date) -> date | None:
    try:
        d = datetime.fromisoformat(event_date[:10]).date()
    except (ValueError, TypeError):
        return None
    if recurring and d < today:
        try:
            d = d.replace(year=today.year)
            if d < today:
                d = d.replace(year=today.year + 1)
        except ValueError:  # Feb 29
            return None
    return d
