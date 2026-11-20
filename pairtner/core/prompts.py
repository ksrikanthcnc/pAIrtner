"""Prompt building for LLM calls — privacy-safe by construction.

PRIVACY RULES (violations are bugs):
1. User X's prompt contains ONLY: X's own private facts + shared facts
   from either partner.  NEVER the partner's private facts.
2. Even shared content goes out as distilled context, never raw notes.
3. Never quote or confirm what the partner said privately.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from typing import TYPE_CHECKING

from pairtner import pack

if TYPE_CHECKING:
    from pairtner.core.context_composer import ComposedContext

logger = logging.getLogger(__name__)


@dataclass
class UserContext:
    """All the per-user data a prompt may reference."""

    user_name: str = ""
    partner_name: str | None = None
    private_facts: list[str] = field(default_factory=list)
    shared_facts: list[str] = field(default_factory=list)
    recent_messages: list[tuple[str, str] | tuple[str, str, str]] = field(default_factory=list)
    lang: str = "en"
    profile_summary: str = ""


@dataclass
class SmartContext:
    """Curated context produced by FTS5 search when fact count exceeds the
    configured threshold.  Replaces the flat facts lists in prompt building.
    """

    recent_facts: list[str] = field(default_factory=list)
    relevant_facts: list[str] = field(default_factory=list)
    monthly_summaries: list[str] = field(default_factory=list)
    total_fact_count: int = 0


# ---------------------------------------------------------------------------
# Pack access
# ---------------------------------------------------------------------------
#
# Every word the model reads comes from the active prompt pack
# (``packs/<name>/``, see ``pairtner/pack.py``).  This module keeps only
# structure: block order, separators and markdown markers (``## ``, ``- ``).


def load_voice(voice_name: str) -> str:
    """Return the pack voice *voice_name*, falling back to ``casual_en``."""
    return pack.current().voice(voice_name)


def load_task_prompt(task_name: str) -> str | None:
    """Return the pack task prompt *task_name*, or ``None`` if the pack has none."""
    return pack.current().task(task_name)


def _trace(p: pack.Pack, task: str) -> None:
    """Record which pack snapshot built the prompt for *task* (read by call tracing)."""
    pack.last_trace.set((task, p.snapshot()))


def _head(p: pack.Pack, *, tone: bool = True) -> str:
    """Persona and shared blocks that open every system prompt."""
    parts = [p.persona(), p.block("role"), p.block("not_therapy")]
    if tone:
        parts.append(p.block("tone"))
    return "\n\n".join(parts)


def _style_section(
    p: pack.Pack, voice_text: str | None, dampening_instruction: str | None,
) -> str:
    """The ``Communication Style`` section (leading blank line included), or ``""``."""
    if not (dampening_instruction or voice_text):
        return ""
    style_parts: list[str] = []
    if dampening_instruction:
        style_parts.append(dampening_instruction)
    if voice_text:
        style_parts.append(voice_text)
    return f"\n\n## {p.s('style_heading')}\n" + "\n".join(style_parts)


# ---------------------------------------------------------------------------
# Goals section (FEAT-004)
# ---------------------------------------------------------------------------

def build_goals_section(
    personal_goals: list[str],
    shared_goals: list[str],
) -> str:
    """Format goals into a prompt section for the system prompt.

    Returns an empty string when no goals exist.
    """
    if not personal_goals and not shared_goals:
        return ""
    p = pack.current()
    parts = [f"\n## {p.s('goals_heading')}"]
    if personal_goals:
        items = "\n".join(f"- {g}" for g in personal_goals)
        parts.append(f"{p.s('goals_personal')}:\n{items}")
    if shared_goals:
        items = "\n".join(f"- {g}" for g in shared_goals)
        parts.append(f"{p.s('goals_shared')}:\n{items}")
    parts.append(p.s("goals_closing"))
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# Partner summary (FEAT-003)
# ---------------------------------------------------------------------------

def build_partner_summary_block(perceptions: list[dict]) -> str:
    """Format partner perceptions into a prompt section.

    PRIVACY CRITICAL: this block contains ONLY synthesized themes,
    NEVER raw partner text.
    """
    if not perceptions:
        return ""
    lines = []
    for p in perceptions:
        theme = p.get("theme", "")
        sentiment = p.get("sentiment", "neutral")
        if theme:
            lines.append(f"- {theme} ({sentiment})")
    if not lines:
        return ""
    return (
        f"\n## {pack.current().s('partner_summary_heading')}\n"
        + "\n".join(lines)
        + "\n"
    )


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _format_facts(label: str, facts: list[str]) -> str:
    if not facts:
        return ""
    items = "\n".join(f"- {f}" for f in facts)
    return f"\n## {label}\n{items}\n"


def _format_history(
    messages: list[tuple[str, str] | tuple[str, str, str]], limit: int = 6,
) -> str:
    """Format recent conversation with absolute timestamps and gap markers.

    Uses absolute timestamps (not relative like '5h ago') so the prompt
    doesn't go stale if cached or reused.  The LLM has ``Current time:``
    in the system prompt and can compute recency itself.
    """
    if not messages:
        return ""
    p = pack.current()
    recent = messages[-limit:]
    lines: list[str] = []
    prev_ts: datetime | None = None

    for entry in recent:
        role = entry[0]
        content = entry[1]
        ts_str = entry[2] if len(entry) > 2 else ""  # type: ignore[misc]
        prefix = p.s("history_assistant" if role == "assistant" else "history_user")

        if ts_str:
            ts = _parse_msg_ts(ts_str)
            # Insert a gap marker if >2 hours since previous message
            if prev_ts and ts and (ts - prev_ts).total_seconds() > 7200:
                gap = _humanize_gap(ts - prev_ts)
                lines.append(f"--- {gap} gap ---")
            ts_label = _format_timestamp(ts) if ts else ""
            if ts_label:
                lines.append(f"[{ts_label}] {prefix}: {content}")
            else:
                lines.append(f"{prefix}: {content}")
            prev_ts = ts
        else:
            lines.append(f"{prefix}: {content}")

    return f"\n## {p.s('label_recent_conversation')}\n" + "\n".join(lines) + "\n"


def _parse_msg_ts(raw: str) -> datetime | None:
    """Parse a message timestamp from the DB (ISO or SQLite default format)."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _format_timestamp(ts: datetime) -> str:
    """Format a timestamp as relative time for the LLM prompt.

    Relative format saves LLM computation — 'Current time:' is already
    in the system prompt as an absolute reference if needed.
    """
    now = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = now - ts
    secs = int(delta.total_seconds())
    if secs < 0:
        return "just now"
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    days = secs // 86400
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days}d ago"
    if days < 30:
        return f"{days // 7}w ago"
    return f"{days // 30}mo ago"


def _humanize_gap(delta: timedelta) -> str:
    """Describe a time gap between messages in human terms."""
    secs = int(delta.total_seconds())
    if secs < 3600:
        return f"{secs // 60} min"
    if secs < 86400:
        return f"{secs // 3600} hours"
    return f"{secs // 86400} days"


def _format_smart_context(smart_ctx: SmartContext, recent_days: int = 7) -> str:
    """Format a :class:`SmartContext` into structured prompt sections.

    Replaces the flat ``## Your private notes`` / ``## Shared notes``
    sections when the smart-context window is active.
    """
    p = pack.current()
    parts: list[str] = []

    if smart_ctx.recent_facts:
        items = "\n".join(f"- {f}" for f in smart_ctx.recent_facts)
        label = p.s("smart_recent", recent_days=recent_days)
        parts.append(f"\n## {label}\n{items}\n")

    if smart_ctx.relevant_facts:
        items = "\n".join(f"- {f}" for f in smart_ctx.relevant_facts)
        parts.append(f"\n## {p.s('smart_relevant')}\n{items}\n")

    if smart_ctx.monthly_summaries:
        items = "\n".join(f"- {s}" for s in smart_ctx.monthly_summaries)
        parts.append(f"\n## {p.s('smart_monthly')}\n{items}\n")

    return "".join(parts)


async def build_smart_context(
    db_path: str,
    user_id: int,
    message: str,
    config,
    llm=None,
) -> SmartContext | None:
    """Build a :class:`SmartContext` using FTS5 when fact count exceeds threshold.

    Returns ``None`` when the user's fact count is below
    ``config.search_fts_threshold`` — the caller should fall back to the
    regular :class:`UserContext` facts in that case.
    """
    from datetime import datetime, timedelta, timezone

    from pairtner.db import get_fact_summaries, get_facts_since
    from pairtner.storage.helpers import _to_iso
    from pairtner.core.search import get_fact_count, search_facts

    total = await get_fact_count(db_path, user_id)

    if total < config.search_fts_threshold:
        return None

    # Recent N-day facts
    since = _to_iso(
        datetime.now(timezone.utc) - timedelta(days=config.context_recent_days)
    )
    recent_rows = await get_facts_since(db_path, user_id, since)
    recent_facts = [f["text"] for f in recent_rows]

    # FTS5 search for relevant facts
    try:
        relevant_rows = await search_facts(
            db_path, user_id, message,
            limit=config.context_max_relevant_facts,
        )
        # De-duplicate against recent facts
        recent_ids = {f["id"] for f in recent_rows}
        relevant_facts = [
            f["text"] for f in relevant_rows if f["id"] not in recent_ids
        ]
    except Exception:
        relevant_facts = []

    # Monthly summaries
    summaries = await get_fact_summaries(db_path, user_id)
    monthly_summaries = [
        f"{s['year_month']}: {s['summary_text']}" for s in summaries
    ]

    # Optionally trigger on-demand summary generation for missing months
    if llm:
        from pairtner.core.summarizer import check_and_summarize

        await check_and_summarize(db_path, llm, user_id)
        # Re-fetch after generation
        summaries = await get_fact_summaries(db_path, user_id)
        monthly_summaries = [
            f"{s['year_month']}: {s['summary_text']}" for s in summaries
        ]

    return SmartContext(
        recent_facts=recent_facts,
        relevant_facts=relevant_facts,
        monthly_summaries=monthly_summaries,
        total_fact_count=total,
    )


def build_system_prompt(
    voice_text: str | None = None,
    task_text: str | None = None,
    dampening_instruction: str | None = None,
    v2: bool = False,
) -> str:
    """Base system prompt shared across prompt types.

    With ``v2=True`` the pack's v2 extraction contract is used (falls back
    to the v1 contract when the pack has no ``extract_v2.md``).

    Optionally appends voice, task, and dampening instructions.
    Includes current UTC time for temporal awareness.
    """
    p = pack.current()
    _trace(p, "system")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    contract = p.contract_v2() if v2 else p.contract()
    parts = [_head(p), f"Current time: {now}\n\n" + contract + "\n"]
    style = _style_section(p, voice_text, dampening_instruction)
    if style:
        parts.append(style.lstrip("\n"))
    if task_text:
        parts.append(f"## {p.s('task_heading')}\n{task_text}")
    return "\n\n".join(parts)


def build_chat_prompt(
    ctx: UserContext,
    message: str,
    voice_text: str | None = None,
    dampening_instruction: str | None = None,
    partner_summary: str | None = None,
    goals_text: str | None = None,
    smart_ctx: SmartContext | None = None,
) -> tuple[str, str]:
    """Build prompt for a regular chat message.

    Returns (system_prompt, user_content).
    """
    p = pack.current()
    system = build_system_prompt(
        voice_text=voice_text, dampening_instruction=dampening_instruction,
    )
    _trace(p, "chat")
    if goals_text:
        system += "\n" + goals_text

    parts: list[str] = []
    if ctx.profile_summary:
        parts.append(f"\n## Relationship Profile\n{ctx.profile_summary}\n")
    if smart_ctx:
        parts.append(_format_smart_context(smart_ctx))
    else:
        parts.append(_format_facts(p.s("label_private_notes"), ctx.private_facts))
        parts.append(_format_facts(p.s("label_shared_notes"), ctx.shared_facts))
    if partner_summary:
        parts.append(partner_summary)
    parts.append(_format_history(ctx.recent_messages))
    parts.append(f"\n## {p.s('label_current_message')}\n{message}")

    return system, "".join(parts)


def build_chat_prompt_v2(
    composed: "ComposedContext",
    message: str,
    voice_text: str | None = None,
    dampening_instruction: str | None = None,
    goals_text: str | None = None,
) -> tuple[str, str]:
    """Build the chat prompt from a v2 :class:`ComposedContext`.

    The composed context is already isolation-safe (partner facts are
    non-private only; nudges are generic guidance). Returns
    (system_prompt, user_content).
    """
    p = pack.current()
    system = build_system_prompt(
        voice_text=voice_text, dampening_instruction=dampening_instruction,
        v2=True,
    )
    _trace(p, "chat")
    if goals_text:
        system += "\n" + goals_text

    parts: list[str] = []
    parts.append(_format_facts(p.s("label_private_notes"), composed.core_facts))
    parts.append(_format_facts(p.s("label_private_notes"), composed.working_facts))
    parts.append(_format_facts(p.s("label_shared_notes"), composed.partner_facts))
    parts.append(_format_facts("Gentle guidance to weave in if natural", composed.nudge_suggestions))
    parts.append(_format_facts(p.s("label_period"), composed.summaries))
    parts.append(_format_facts("Coming up", composed.proactive))
    parts.append(_format_history(composed.recent_messages, limit=max(6, len(composed.recent_messages))))
    parts.append(f"\n## {p.s('label_current_message')}\n{message}")
    return system, "".join(parts)


def build_tell_prompt(
    ctx: UserContext,
    message: str,
    voice_text: str | None = None,
    dampening_instruction: str | None = None,
    partner_summary: str | None = None,
    goals_text: str | None = None,
    smart_ctx: SmartContext | None = None,
    mode: str = "mediator",
) -> tuple[str, str]:
    """Build prompt for /tell or #send — relay drafting.

    mode: 'mediator' (third-person, default) or 'voice' (first-person, writes as the user).
    Returns (system_prompt, user_content).
    """
    p = pack.current()
    _trace(p, "tell")
    style_section = _style_section(p, voice_text, dampening_instruction)
    goals_section = ""
    if goals_text:
        goals_section = "\n" + goals_text

    mode_instruction = ""
    if mode == "voice":
        mode_instruction = (
            "\n\n## Relay Mode: VOICE (first-person)\n"
            "Write the relay draft in FIRST PERSON, as if the sender wrote it themselves. "
            "Use 'I', 'me', 'my'. Match the sender's natural voice and style. "
            "The receiver should feel like they're reading a message from their partner, "
            "not from a bot. Still soften and dampen, but keep the personal voice."
        )

    system = (
        f"{_head(p)}\n\n"
        f"{p.s('tell_intro')}\n\n"
        f"{p.contract()}\n\n"
        f"{p.s('tell_tail')}"
        f"{style_section}"
        f"{goals_section}"
        f"{mode_instruction}"
    )

    parts: list[str] = []
    if smart_ctx:
        parts.append(_format_smart_context(smart_ctx))
    else:
        parts.append(_format_facts(p.s("label_private_notes"), ctx.private_facts))
        parts.append(_format_facts(p.s("label_shared_notes"), ctx.shared_facts))
    if partner_summary:
        parts.append(partner_summary)
    parts.append(f"\n## {p.s('label_relay_message')}\n{message}")

    return system, "".join(parts)


def build_mediate_prompt(
    shared_facts: list[str],
    *,
    perceptions_a: list[str] | None = None,
    perceptions_b: list[str] | None = None,
    name_a: str = "",
    name_b: str = "",
    window_label: str = "",
) -> tuple[str, str]:
    """Build prompt for /mediate — neutral perspective.

    PRIVACY: uses ONLY shared facts and pre-filtered perception themes.
    No raw messages and no private facts from either partner.
    ``perceptions_a`` are A's impressions of B; ``perceptions_b`` are B's of A.
    Returns (system_prompt, user_content).
    """
    p = pack.current()
    _trace(p, "mediate")
    system = (
        f"{_head(p)}\n\n"
        f"{p.s('mediate_intro')}\n\n"
        f"{p.contract()}\n\n"
        f"{p.s('mediate_tail')}"
    )
    parts: list[str] = []
    if window_label:
        parts.append(f"## {p.s('label_period')}\n{window_label}\n\n")
    if shared_facts:
        parts.append(_format_facts(p.s("label_shared_context"), shared_facts))
    has_a = bool(perceptions_a)
    has_b = bool(perceptions_b)
    if has_a or has_b:
        na = name_a or p.s("partner_a_fallback")
        nb = name_b or p.s("partner_b_fallback")
        parts.append(
            f"\n## {p.s('label_impressions')}\n"
            f"{p.s('impressions_intro')}\n"
        )
        for who, about, items in (
            (na, nb, perceptions_a), (nb, na, perceptions_b),
        ):
            if items:
                parts.append(p.s("impressions_of", who=who, about=about) + "\n")
                parts.extend(f"- {item}\n" for item in items)
        if has_a != has_b:
            parts.append(f"{p.s('impressions_one_sided')}\n")
    parts.append(
        f"\n## {p.s('label_request')}\n"
        f"{p.s('request_text')}"
    )
    return system, "".join(parts)


def build_keep_prompt(
    ctx: UserContext,
    message: str,
    voice_text: str | None = None,
    dampening_instruction: str | None = None,
    smart_ctx: SmartContext | None = None,
) -> tuple[str, str]:
    """Build prompt for #keep — fact extraction only.

    Returns (system_prompt, user_content).
    """
    p = pack.current()
    _trace(p, "keep")
    style_section = _style_section(p, voice_text, dampening_instruction)
    system = (
        f"{_head(p)}\n\n"
        f"{p.s('keep_intro')}\n\n"
        f"{p.contract()}\n\n"
        f"{p.s('keep_tail')}"
        f"{style_section}"
    )

    parts: list[str] = []
    if smart_ctx:
        parts.append(_format_smart_context(smart_ctx))
    else:
        parts.append(_format_facts(p.s("label_private_notes"), ctx.private_facts))
    parts.append(f"\n## {p.s('label_keep_message')}\n{message}")

    return system, "".join(parts)


def build_digest_prompt(
    shared_facts: list[str], sent_relays: list[str]
) -> tuple[str, str]:
    """Build prompt for the daily digest — neutral day summary.

    Returns (system_prompt, user_content).
    """
    p = pack.current()
    _trace(p, "digest")
    system = f"{_head(p)}\n\n{p.s('digest_system')}"

    parts: list[str] = []
    if shared_facts:
        items = "\n".join(f"- {f}" for f in shared_facts)
        parts.append(f"## {p.s('digest_facts_heading')}\n{items}\n")
    if sent_relays:
        items = "\n".join(f"- {r}" for r in sent_relays)
        parts.append(f"## {p.s('digest_relays_heading')}\n{items}\n")
    parts.append(f"\n{p.s('digest_request')}")

    return system, "\n".join(parts)


def build_checkin_summary_prompt(
    partner_a_answers: str,
    partner_b_answers: str,
    shared_facts: list[str],
    goals: str | None = None,
) -> tuple[str, str]:
    """Build prompt for the weekly check-in summary — blame-free.

    Returns (system_prompt, user_content).
    """
    p = pack.current()
    _trace(p, "checkin_summary")
    system = f"{_head(p)}\n\n{p.s('checkin_system')}"

    parts: list[str] = []
    parts.append(f"## {p.s('checkin_partner_a_heading')}\n{partner_a_answers}\n")
    parts.append(f"## {p.s('checkin_partner_b_heading')}\n{partner_b_answers}\n")
    if shared_facts:
        items = "\n".join(f"- {f}" for f in shared_facts)
        parts.append(f"## {p.s('checkin_shared_heading')}\n{items}\n")
    if goals:
        parts.append(goals)
    parts.append(f"\n{p.s('checkin_request')}")

    return system, "\n".join(parts)


def build_blunt_prompt(
    ctx: UserContext,
    message: str,
    voice_text: str | None = None,
    dampening_instruction: str | None = None,
    smart_ctx: SmartContext | None = None,
) -> tuple[str, str]:
    """Build prompt for #blunt / #honest — direct, unfiltered feedback.

    The response is for the sender's own reflection only.

    Returns (system_prompt, user_content).
    """
    p = pack.current()
    _trace(p, "blunt")
    style_section = _style_section(p, voice_text, dampening_instruction)
    system = (
        f"{_head(p, tone=False)}\n\n"
        f"{p.s('blunt_intro')}\n\n"
        f"{p.contract()}\n\n"
        f"{p.s('blunt_tail')}"
        f"{style_section}"
    )

    parts: list[str] = []
    if smart_ctx:
        parts.append(_format_smart_context(smart_ctx))
    else:
        parts.append(_format_facts(p.s("label_private_notes"), ctx.private_facts))
        parts.append(_format_facts(p.s("label_shared_notes"), ctx.shared_facts))
    parts.append(_format_history(ctx.recent_messages))
    parts.append(f"\n## {p.s('label_current_message_blunt')}\n{message}")

    return system, "".join(parts)



def build_analysis_prompt(
    messages: list[dict], user_id: int,
) -> tuple[str, str]:
    """Build prompt for background analysis (patterns, horsemen, voice traits).

    Messages should be in chronological order (oldest first).

    Returns (system_prompt, user_content).
    """
    p = pack.current()
    _trace(p, "analysis")

    # Load the analysis task prompt
    task_text = p.task("analysis") or ""

    system = (
        f"{p.persona()}\n\n"
        f"{p.contract()}\n\n"
        f"{task_text}"
    )

    # Format messages as a readable batch
    parts: list[str] = [
        "## Messages to analyze\n\n"
        f"User ID: {user_id}\n"
        f"Message count: {len(messages)}\n\n"
    ]
    for m in reversed(messages):  # oldest first (messages come newest-first from DB)
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "user":
            parts.append(f"USER: {content}\n")
        else:
            parts.append(f"BOT: {content}\n")

    return system, "".join(parts)



def build_consolidation_prompt(
    memories: list[dict], period: str,
) -> tuple[str, str]:
    """Build prompt for consolidating old memories into an episode summary.

    Returns (system_prompt, user_content).
    """
    p = pack.current()
    _trace(p, "consolidation")

    system = (
        f"{p.persona()}\n\n"
        "You are consolidating old memories into a concise episode summary. "
        "This summary replaces the individual memories in the user's prompt window. "
        "Write a 2-3 sentence narrative that captures the key events and emotional arc.\n\n"
        "Rules:\n"
        "- Be factual, not interpretive\n"
        "- Preserve the emotional tone (was it stressful? joyful? mixed?)\n"
        "- Include specific details that would help recall the period\n"
        "- Do NOT add information not present in the memories\n"
        "- Write in third person (about the user)\n\n"
        f"{p.contract()}"
    )

    parts: list[str] = [
        f"## Memories to consolidate (period: {period})\n\n"
    ]
    for m in memories:
        status = m.get("status", "active")
        parts.append(f"- [{m['id']}] ({status}) {m['text']}\n")

    parts.append(
        "\n## Your task\n"
        "Write a concise episode summary (2-3 sentences) that captures these memories. "
        "Return it as your reply."
    )

    return system, "".join(parts)
