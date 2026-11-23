"""Structured therapy-ready export — timeline, patterns, goals, growth.

PRIVACY: Only the requesting user's private data + shared data is included.
NEVER include the partner's private facts, raw chat, or private patterns.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from pairtner import db as db_mod
from pairtner import pack
from pairtner.rest import dashboard_data as dd
from pairtner.llm import LLMClient, get_reply_text
from pairtner.core.prompts import load_task_prompt


async def generate_therapy_report(
    db_path: str,
    llm: LLMClient,
    user_id: int,
) -> str | None:
    """Gather context and generate a structured therapy report.

    Returns formatted text suitable for file download, or ``None`` if
    there is insufficient data.
    """
    user = await db_mod.get_user(db_path, user_id)
    if not user or not user.get("partner_id"):
        return None

    partner_id = user["partner_id"]
    couple_id = db_mod.make_couple_id(user_id, partner_id)
    user_name = user.get("name") or "User"

    # ── Gather data ──────────────────────────────────────────────────

    # Timeline events
    events = await db_mod.get_timeline_events(db_path, couple_id, limit=100)
    timeline_summary = await db_mod.get_timeline_summary(db_path, couple_id)

    # Communication patterns (requesting user only — privacy)
    patterns = await db_mod.get_user_patterns(db_path, user_id, limit=15)

    # User's own facts only — NEVER partner's private facts
    private_facts = await db_mod.get_active_facts(db_path, user_id, "private")
    shared_facts = await db_mod.get_shared_facts_for_pair(
        db_path, user_id, partner_id,
    )

    # Goals
    personal_goals = await db_mod.get_active_goals(db_path, user_id)
    shared_goals = await db_mod.get_shared_goals(db_path, couple_id)

    # Interaction ratio
    pos, neg = await db_mod.get_interaction_ratio(db_path, couple_id, days=30)

    # ── Check for minimum data ───────────────────────────────────────
    if not events and not patterns and not private_facts and not shared_facts:
        return None

    # ── Build context for LLM ────────────────────────────────────────
    p = pack.current()
    pack.set_trace(p, "therapy_export")
    sections: list[str] = []


    # Timeline
    if events:
        event_lines = []
        for ev in reversed(events):  # chronological
            try:
                dt = datetime.fromisoformat(ev["created_at"])
                date_str = dt.strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                date_str = "?"
            line = p.s(
                "therapy_timeline_line", date=date_str,
                event_type=ev["event_type"], summary=ev["summary"],
            )
            event_lines.append(f"- {line}")
        sections.append(
            f"## {p.s('therapy_timeline_heading')}\n" + "\n".join(event_lines)
        )

    if timeline_summary:
        summary_line = ", ".join(
            f"{k}: {v}" for k, v in timeline_summary.items()
        )
        sections.append(f"## {p.s('therapy_counts_heading')}\n{summary_line}")

    # Communication patterns
    if patterns:
        pat_lines = [
            "- " + p.s(
                "therapy_pattern_line", pattern=pat["pattern"],
                pattern_type=pat.get("pattern_type", "communication"),
                frequency=pat["frequency"],
            )
            for pat in patterns
        ]
        sections.append(
            f"## {p.s('therapy_patterns_heading')}\n" + "\n".join(pat_lines)
        )

    # Facts (user's own private + shared only)
    if private_facts:
        fact_lines = [f"- {f['text']}" for f in private_facts[:20]]
        sections.append(
            f"## {p.s('therapy_notes_heading')}\n" + "\n".join(fact_lines)
        )

    if shared_facts:
        shared_lines = [f"- {f['text']}" for f in shared_facts[:20]]
        sections.append(
            f"## {p.s('therapy_shared_heading')}\n" + "\n".join(shared_lines)
        )

    # Goals
    goal_parts = []
    if personal_goals:
        goal_parts.extend(
            "- " + p.s("therapy_goal_personal", text=g["text"], status=g["status"])
            for g in personal_goals
        )
    if shared_goals:
        goal_parts.extend(
            "- " + p.s("therapy_goal_shared", text=g["text"], status=g["status"])
            for g in shared_goals
        )
    if goal_parts:
        sections.append(
            f"## {p.s('therapy_goals_heading')}\n" + "\n".join(goal_parts)
        )

    # Interaction ratio
    if pos + neg > 0:
        ratio_str = f"{pos / neg:.1f}" if neg > 0 else f"{pos}:0"
        ratio_line = p.s(
            "therapy_ratio_line", positive=pos, negative=neg, ratio=ratio_str,
        )
        sections.append(f"## {p.s('therapy_ratio_heading')}\n{ratio_line}")

    context_block = "\n\n".join(sections)

    # ── Build prompt ─────────────────────────────────────────────────
    task_prompt = load_task_prompt("therapy_export") or ""

    system_prompt = (
        f"{p.s('therapy_intro')}\n\n"
        f"{task_prompt}\n\n"
        f"{p.s('therapy_privacy')}"
    )

    user_content = (
        f"{p.s('therapy_request', user_name=user_name)}\n\n{context_block}"
    )

    result = await llm.generate(user_content, system_prompt)
    report = get_reply_text(result)

    if not report:
        return None

    # ── Format as downloadable text ──────────────────────────────────
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = (
        f"Therapy Report — {user_name}\n"
        f"Generated: {now}\n"
        f"{'=' * 50}\n\n"
    )
    footer = (
        f"\n{'=' * 50}\n"
        "This report was generated by pAIrtner bot to assist therapy sessions.\n"
        "It contains only the requesting user's data and shared information.\n"
        "Partner's private data is never included.\n"
    )

    return header + report + footer


# ═══════════════════════════════════════════════════════════════════════
# Data-only therapist notes (no LLM): deterministic markdown from the DB
# ═══════════════════════════════════════════════════════════════════════

_CHILD_RE = re.compile(
    r"\b(son|daughter|child|children|kid|kids|baby|toddler|newborn|"
    r"pregnan\w*)\b",
    re.IGNORECASE,
)
_DYNAMIC_KINDS = ("core", "working")


def _date(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _fmt_date(raw: Any) -> str:
    dt = _date(raw)
    return dt.strftime("%Y-%m-%d") if dt else "unknown date"


def _duration(start: datetime | None, now: datetime) -> str:
    if start is None:
        return "unknown"
    days = max((now - start).days, 0)
    if days >= 365:
        years, rem = divmod(days, 365)
        return f"{years} year(s), {rem // 30} month(s)"
    if days >= 30:
        return f"{days // 30} month(s)"
    return f"{days} day(s)"


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items)


async def build_therapy_notes(db_path: str, user_id: int) -> str | None:
    """Build a therapist-facing markdown document straight from stored data.

    No LLM is involved: every line is a stored fact, count or timestamp.

    PRIVACY: contains only the requesting user's private data plus shared
    data from both partners. The partner's private facts and patterns are
    never read.

    Returns ``None`` if the user is unpaired or there is nothing to report.
    """
    user = await db_mod.get_user(db_path, user_id)
    if not user or not user.get("partner_id"):
        return None
    partner_id = user["partner_id"]
    partner = await db_mod.get_user(db_path, partner_id) or {}
    couple_id = db_mod.make_couple_id(user_id, partner_id)
    name = user.get("name") or "User"
    partner_name = partner.get("name") or "Partner"
    now = datetime.now(timezone.utc)

    events = await db_mod.get_timeline_events(db_path, couple_id, limit=200)
    events = list(reversed(events))  # chronological
    conflicts = await db_mod.get_all_conflicts(db_path, couple_id)
    patterns = await db_mod.get_user_patterns(db_path, user_id, limit=50)
    own_facts = await db_mod.get_active_facts(db_path, user_id, "private")
    shared = await db_mod.get_shared_facts_for_pair(db_path, user_id, partner_id)
    shared = [f for f in shared if not f.get("archived_at")]
    personal_goals = await db_mod.get_active_goals(db_path, user_id)
    shared_goals = await db_mod.get_shared_goals(db_path, couple_id)
    pos30, neg30 = await db_mod.get_interaction_ratio(db_path, couple_id, days=30)
    pos_all, neg_all = await db_mod.get_interaction_ratio(
        db_path, couple_id, days=36500,
    )

    if not (events or conflicts or patterns or own_facts or shared
            or personal_goals or shared_goals or pos_all or neg_all):
        return None

    out: list[str] = [
        f"# Therapy Report — {name} & {partner_name}",
        f"*Generated {now.strftime('%Y-%m-%d %H:%M UTC')} by pAIrtner from "
        f"stored data. Contains {name}'s own notes and shared information "
        f"only; {partner_name}'s private data is excluded.*",
    ]

    # 1. Overview
    paired_ev = next((e for e in events if e["event_type"] == "paired"), None)
    start = _date(paired_ev["created_at"]) if paired_ev else _date(user.get("created_at"))
    all_visible = own_facts + shared
    child_facts = [f for f in all_visible if _CHILD_RE.search(f["text"])]
    core = [f for f in all_visible if f.get("kind") == "core"]
    ov = [f"**Time using pAIrtner together:** {_duration(start, now)} "
          f"(since {_fmt_date(start.isoformat()) if start else 'unknown'})"]
    ov.append("**Children:** " + (
        "; ".join(f["text"] for f in child_facts[:5])
        if child_facts else "none recorded"))
    ov.append(f"**Records on file:** {len(own_facts)} private note(s), "
              f"{len(shared)} shared fact(s), {len(conflicts)} conflict(s)")
    out.append("## 1. Relationship overview\n" + _bullets(ov))
    if core:
        out.append("**Core background:**\n" + _bullets(
            [f["text"] for f in core[:15]]))

    # 2. Communication
    comm: list[str] = []
    if pos30 + neg30:
        ratio = f"{pos30 / neg30:.1f}:1" if neg30 else f"{pos30}:0 (no negatives)"
        comm.append(
            f"**Gottman positive:negative ratio (last 30 days):** {ratio} "
            f"({pos30} positive, {neg30} negative). Research target is 5:1 "
            "or higher.")
    if pos_all + neg_all and (pos_all, neg_all) != (pos30, neg30):
        comm.append(f"**All time:** {pos_all} positive, {neg_all} negative")
    horsemen = [p for p in patterns if p.get("pattern_type") == "horsemen"]
    other = [p for p in patterns if p.get("pattern_type") != "horsemen"]
    if horsemen:
        comm.append(f"**Four Horsemen detected in {name}'s messages:**")
        comm.extend(f"  - {p['pattern']} (seen {p['frequency']}x, last "
                    f"{_fmt_date(p.get('last_seen'))})" for p in horsemen)
    else:
        comm.append("**Four Horsemen detected:** none recorded")
    if other:
        comm.append(f"**Other communication patterns ({name}):**")
        comm.extend(f"  - {p['pattern']} ({p.get('pattern_type', 'communication')}, "
                    f"{p['frequency']}x)" for p in other[:10])
    out.append("## 2. Communication patterns\n" + "\n".join(
        c if c.startswith("  ") else f"- {c}" for c in comm))

    # 3. Themes
    groups: dict[str, list[str]] = {}
    for f in all_visible:
        if f.get("kind") in ("episode",) or f.get("status") not in (None, "active"):
            continue
        tags = dd.fact_tags(f.get("tag")) or ["general"]
        owner = name if f["owner_id"] == user_id else partner_name
        entry = f"{f['text']} *({owner}, {f['visibility']})*"
        for t in tags:
            groups.setdefault(t, []).append(entry)
    if groups:
        blocks = []
        for tag in sorted(groups, key=lambda k: (-len(groups[k]), k)):
            blocks.append(f"### {tag.replace('_', ' ').title()}\n"
                          + _bullets(groups[tag][:15]))
        out.append("## 3. Key themes\n" + "\n\n".join(blocks))

    # 4. Conflicts
    if conflicts:
        lines = []
        for c in conflicts:
            relays = dd.parse_json_list(c.get("relay_ids"))
            line = (f"**{c['theme']}**: {c['status']}, opened "
                    f"{_fmt_date(c.get('opened_at'))}")
            if c.get("resolved_at"):
                line += f", resolved {_fmt_date(c['resolved_at'])}"
            if relays:
                line += f", {len(relays)} related relay(s)"
            lines.append(line)
        counts = Counter(c["status"] for c in conflicts)
        summary = ", ".join(f"{n} {s}" for s, n in sorted(counts.items()))
        out.append(f"## 4. Conflicts and their evolution\n*{summary}*\n\n"
                   + _bullets(lines))
    else:
        out.append("## 4. Conflicts and their evolution\nNone recorded.")

    # 5. Timeline
    if events:
        out.append("## 5. Timeline of significant events\n" + _bullets(
            [f"{_fmt_date(e['created_at'])} — *{e['event_type']}*: {e['summary']}"
             for e in events]))

    # 6. Goals
    goals: list[str] = []
    goals.extend(f"**Personal ({name}):** {g['text']}"
                 + (f" — {g['progress']}" if g.get("progress") else "")
                 for g in personal_goals)
    goals.extend(f"**Shared:** {g['text']}"
                 + (f" — {g['progress']}" if g.get("progress") else "")
                 for g in shared_goals)
    out.append("## 6. Goals\n" + (_bullets(goals) if goals else "None recorded."))

    # 7. Strengths (only what the data supports)
    strengths: list[str] = []
    if neg30 == 0 and pos30 > 0 or (neg30 and pos30 / neg30 >= 5):
        strengths.append("Positive interactions meet the 5:1 Gottman target "
                         "over the last 30 days.")
    resolved = [c for c in conflicts if c["status"] == "resolved"]
    if resolved:
        strengths.append(f"{len(resolved)} conflict(s) resolved: "
                         + ", ".join(c["theme"] for c in resolved[:5]))
    for p in patterns:
        if p.get("pattern_type") == "articulation":
            strengths.append(f"Articulation: {p['pattern']} ({p['frequency']}x)")
    achieved = await _achieved_goals(db_path, user_id, couple_id)
    strengths.extend(f"Goal achieved: {t}" for t in achieved)
    if len(shared_goals):
        strengths.append(f"{len(shared_goals)} active shared goal(s) show "
                         "joint commitment.")
    out.append("## 7. Strengths identified\n" + (
        _bullets(strengths) if strengths else "None identified from the data yet."))

    return "\n\n".join(out) + "\n"


async def _achieved_goals(db_path: str, user_id: int, couple_id: str) -> list[str]:
    rows = await db_mod._fetch_all(  # noqa: SLF001 — read-only query
        db_path,
        "SELECT text FROM goals WHERE user_id = ? AND status = 'achieved' "
        "UNION ALL SELECT text FROM goals "
        "WHERE couple_id = ? AND status = 'achieved'",
        (user_id, couple_id),
    )
    return [r["text"] for r in rows]
