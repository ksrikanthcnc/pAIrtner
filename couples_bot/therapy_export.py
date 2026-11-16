"""Structured therapy-ready export — timeline, patterns, goals, growth.

PRIVACY: Only the requesting user's private data + shared data is included.
NEVER include the partner's private facts, raw chat, or private patterns.
"""

from __future__ import annotations

from datetime import datetime, timezone

from couples_bot import db as db_mod
from couples_bot.llm import LLMClient
from couples_bot.prompts import load_task_prompt


async def generate_therapy_report(
    db_path: str,
    llm: LLMClient,
    user_id: int,
    prompts_dir: str = "prompts",
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
            event_lines.append(f"- [{date_str}] {ev['event_type']}: {ev['summary']}")
        sections.append("## Timeline Events\n" + "\n".join(event_lines))

    if timeline_summary:
        summary_line = ", ".join(
            f"{k}: {v}" for k, v in timeline_summary.items()
        )
        sections.append(f"## Event Counts\n{summary_line}")

    # Communication patterns
    if patterns:
        pat_lines = [
            f"- {p['pattern']} (type: {p.get('pattern_type', 'communication')}, "
            f"frequency: {p['frequency']})"
            for p in patterns
        ]
        sections.append("## Communication Patterns\n" + "\n".join(pat_lines))

    # Facts (user's own private + shared only)
    if private_facts:
        fact_lines = [f"- {f['text']}" for f in private_facts[:20]]
        sections.append("## User's Context Notes\n" + "\n".join(fact_lines))

    if shared_facts:
        shared_lines = [f"- {f['text']}" for f in shared_facts[:20]]
        sections.append("## Shared Context\n" + "\n".join(shared_lines))

    # Goals
    goal_parts = []
    if personal_goals:
        goal_parts.extend(
            f"- Personal: {g['text']} ({g['status']})" for g in personal_goals
        )
    if shared_goals:
        goal_parts.extend(
            f"- Shared: {g['text']} ({g['status']})" for g in shared_goals
        )
    if goal_parts:
        sections.append("## Goals\n" + "\n".join(goal_parts))

    # Interaction ratio
    if pos + neg > 0:
        ratio_str = f"{pos / neg:.1f}" if neg > 0 else f"{pos}:0"
        sections.append(
            f"## Interaction Ratio (30 days)\n"
            f"Positive: {pos}, Negative: {neg}, Ratio: {ratio_str}"
        )

    context_block = "\n\n".join(sections)

    # ── Build prompt ─────────────────────────────────────────────────
    task_prompt = load_task_prompt(prompts_dir, "therapy_export")
    if not task_prompt:
        task_prompt = (
            "Generate a structured therapy-ready report. Sections: "
            "Relationship Timeline, Communication Patterns, Recurring Themes, "
            "Goal Progress, Growth Areas. Be factual, non-judgmental. "
            "Never include raw messages — only summaries and patterns."
        )

    system_prompt = (
        "You are a clinical note generator for a couples therapy context. "
        "Your output will be read by a therapist to quickly understand "
        "the couple's dynamics.\n\n"
        f"{task_prompt}\n\n"
        "CRITICAL PRIVACY RULE: The data below only contains the requesting "
        "user's own information and shared data. Do NOT speculate about the "
        "partner's private thoughts. Output as plain text with clear section "
        "headers. Do NOT use JSON format."
    )

    user_content = (
        f"Generate a therapy report for {user_name}.\n\n{context_block}"
    )

    result = await llm.generate(user_content, system_prompt)
    report = result.get("reply", "")

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
