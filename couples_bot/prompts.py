"""Prompt building for LLM calls — privacy-safe by construction.

PRIVACY RULES (violations are bugs):
1. User X's prompt contains ONLY: X's own private facts + shared facts
   from either partner.  NEVER the partner's private facts.
2. Even shared content goes out as distilled context, never raw notes.
3. Never quote or confirm what the partner said privately.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class UserContext:
    """All the per-user data a prompt may reference."""

    user_name: str = ""
    partner_name: str | None = None
    private_facts: list[str] = field(default_factory=list)
    shared_facts: list[str] = field(default_factory=list)
    recent_messages: list[tuple[str, str]] = field(default_factory=list)
    lang: str = "en"


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
# Persona loading
# ---------------------------------------------------------------------------

_persona_text: str | None = None

_FALLBACK_PERSONA = (
    "You are pAIrtner, a warm, non-judgmental friend who helps couples "
    "communicate better. You listen without taking sides, soften hard "
    "messages while preserving the substance, and respect privacy. "
    "You are not a therapist — direct users to professional help when "
    "needed. Never fabricate partner facts, never impersonate a partner, "
    "and never forward raw messages without rewriting them gently."
)


def load_prompt_file(path: str) -> str | None:
    """Read a prompt file and return its content, or ``None`` on error."""
    try:
        with open(path) as f:
            content = f.read().strip()
        return content or None
    except (FileNotFoundError, OSError) as exc:
        logger.warning("Could not load prompt file %r: %s", path, exc)
        return None


_DEFAULT_VOICE_TEXT = (
    "Tone: friendly, warm, concise. Register: informal but respectful. "
    "Speak like a caring friend — not a chatbot, not a therapist."
)


def load_voice(voice_dir: str, voice_name: str) -> str:
    """Load a voice file from ``<voice_dir>/<voice_name>.md``.

    Falls back to ``casual_en.md``, then to a built-in default string.
    """
    primary = os.path.join(voice_dir, f"{voice_name}.md")
    content = load_prompt_file(primary)
    if content:
        return content

    if voice_name != "casual_en":
        fallback = os.path.join(voice_dir, "casual_en.md")
        content = load_prompt_file(fallback)
        if content:
            logger.info(
                "Voice %r not found — falling back to casual_en", voice_name,
            )
            return content

    logger.info("No voice files found — using built-in default voice")
    return _DEFAULT_VOICE_TEXT


def load_task_prompt(prompts_dir: str, task_name: str) -> str | None:
    """Load a task prompt from ``<prompts_dir>/tasks/<task_name>.md``."""
    path = os.path.join(prompts_dir, "tasks", f"{task_name}.md")
    return load_prompt_file(path)


def load_persona(path: str) -> str:
    """Read the persona file, cache it, and return the content.

    Falls back to ``_FALLBACK_PERSONA`` if the file is missing or unreadable.
    """
    global _persona_text
    content = load_prompt_file(path)
    _persona_text = content
    return _persona_text or _FALLBACK_PERSONA


def _get_persona() -> str:
    """Return the cached persona text or fallback."""
    return _persona_text or _FALLBACK_PERSONA


# ---------------------------------------------------------------------------
# System prompt fragments
# ---------------------------------------------------------------------------

_ROLE = (
    "You are a compassionate couples mediator bot. You help partners "
    "communicate more gently and clearly. You listen, remember, and "
    "rewrite hard messages to keep the substance while softening the tone."
)

_NOT_THERAPY = (
    "IMPORTANT: You are NOT a therapist. If someone appears to be in "
    "crisis or danger, direct them to professional help immediately."
)

_JSON_FORMAT = """\
You MUST respond ONLY with valid JSON matching this schema:
{
  "reply": "<your conversational response to the user>",
  "new_facts": [
    {"text": "<a concise fact to remember>", "visibility": "private", "category": "relationship|household|emotional|work|health|other|null", "fact_type": "transient|situational|stable"}
  ],
  "relay_draft": "<a softened version of the message for the partner>" or null,
  "detected_mood": "stressed|happy|angry|reflective|neutral" or null,
  "patterns_observed": ["<communication pattern noticed>"],
  "partner_themes": ["<theme about what user says regarding partner>"],
  "articulation_note": "<tip on how the user could express themselves better>" or null,
  "horsemen_detected": [
    {"type": "criticism|contempt|defensiveness|stonewalling", "evidence": "<what you observed>", "suggestion": "<constructive alternative>"}
  ]
}

Rules:
- "reply" is always required.
- "new_facts" may be empty []. Mark each fact "private" unless the user
  explicitly wants their partner to know, in which case use "shared".
- Classify each fact into one of the categories: relationship, household,
  emotional, work, health, other. Use null if uncertain.
- "fact_type": classify each fact — "transient" (current mood/state),
  "situational" (this week's schedule), "stable" (likes, personality traits).
- "relay_draft": provide a kind, non-confrontational rewrite if the
  message contains something the user might want to share with their
  partner. Set to null otherwise.
- "detected_mood": the user's apparent emotional state. null if unclear.
- "patterns_observed": communication patterns you notice (e.g. "tends to
  generalize", "uses blame language"). May be empty [].
- "partner_themes": themes about what the user says regarding their
  partner (e.g. "feels unheard about household decisions"). May be empty [].
- "articulation_note": if the user's message could be expressed more
  constructively, provide a brief coaching tip. null otherwise.
- "horsemen_detected": detect communication anti-patterns: criticism
  (attacking character rather than behavior), contempt (disrespect,
  mockery), defensiveness (deflecting blame), stonewalling (shutting
  down). May be empty []. NEVER use the term 'horsemen' or any
  Gottman jargon in your reply. Translate patterns to natural,
  friendly language.
- Keep facts concise (one sentence each).
- Never include the partner's private information in your reply.
"""

_TONE = (
    "Tone guidelines: be warm, empathetic, non-judgmental. "
    "Soften harsh language while preserving the core meaning. "
    "Never take sides. Use simple, clear language."
)


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
    parts = ["\n## Your Goals"]
    if personal_goals:
        items = "\n".join(f"- {g}" for g in personal_goals)
        parts.append(f"Personal:\n{items}")
    if shared_goals:
        items = "\n".join(f"- {g}" for g in shared_goals)
        parts.append(f"Shared:\n{items}")
    parts.append(
        "If the conversation touches on any of these goals, "
        "mention the progress or connection."
    )
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
        "\n## What your partner seems to feel\n"
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


def _format_history(messages: list[tuple[str, str]], limit: int = 6) -> str:
    """Format recent conversation (only the user's own history)."""
    if not messages:
        return ""
    recent = messages[-limit:]
    lines = []
    for role, content in recent:
        prefix = "You" if role == "assistant" else "User"
        lines.append(f"{prefix}: {content}")
    return "\n## Recent conversation\n" + "\n".join(lines) + "\n"


def _format_smart_context(smart_ctx: SmartContext, recent_days: int = 7) -> str:
    """Format a :class:`SmartContext` into structured prompt sections.

    Replaces the flat ``## Your private notes`` / ``## Shared notes``
    sections when the smart-context window is active.
    """
    parts: list[str] = []

    if smart_ctx.recent_facts:
        items = "\n".join(f"- {f}" for f in smart_ctx.recent_facts)
        parts.append(f"\n## Recent notes (last {recent_days} days)\n{items}\n")

    if smart_ctx.relevant_facts:
        items = "\n".join(f"- {f}" for f in smart_ctx.relevant_facts)
        parts.append(f"\n## Relevant notes\n{items}\n")

    if smart_ctx.monthly_summaries:
        items = "\n".join(f"- {s}" for s in smart_ctx.monthly_summaries)
        parts.append(f"\n## Monthly summaries\n{items}\n")

    return "".join(parts)


async def build_smart_context(
    db_path: str,
    user_id: int,
    message: str,
    config,
    llm=None,
    prompts_dir: str | None = None,
) -> SmartContext | None:
    """Build a :class:`SmartContext` using FTS5 when fact count exceeds threshold.

    Returns ``None`` when the user's fact count is below
    ``config.search_fts_threshold`` — the caller should fall back to the
    regular :class:`UserContext` facts in that case.
    """
    from datetime import datetime, timedelta, timezone

    from couples_bot.db import get_fact_summaries, get_facts_since
    from couples_bot.search import get_fact_count, search_facts

    total = await get_fact_count(db_path, user_id)

    if total < config.search_fts_threshold:
        return None

    # Recent N-day facts
    since = (
        datetime.now(timezone.utc) - timedelta(days=config.context_recent_days)
    ).isoformat()
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
    if llm and prompts_dir:
        from couples_bot.summarizer import check_and_summarize

        await check_and_summarize(db_path, llm, user_id, prompts_dir)
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
) -> str:
    """Base system prompt shared across prompt types.

    Optionally appends voice, task, and dampening instructions.
    """
    persona = _get_persona()
    parts = [persona, _ROLE, _NOT_THERAPY, _TONE, _JSON_FORMAT]
    if dampening_instruction or voice_text:
        style_parts: list[str] = []
        if dampening_instruction:
            style_parts.append(dampening_instruction)
        if voice_text:
            style_parts.append(voice_text)
        parts.append("## Communication Style\n" + "\n".join(style_parts))
    if task_text:
        parts.append(f"## Task Instructions\n{task_text}")
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
    system = build_system_prompt(
        voice_text=voice_text, dampening_instruction=dampening_instruction,
    )
    if goals_text:
        system += "\n" + goals_text

    parts: list[str] = []
    if smart_ctx:
        parts.append(_format_smart_context(smart_ctx))
    else:
        parts.append(_format_facts("Your private notes", ctx.private_facts))
        parts.append(_format_facts("Shared notes", ctx.shared_facts))
    if partner_summary:
        parts.append(partner_summary)
    parts.append(_format_history(ctx.recent_messages))
    parts.append(f"\n## Current message\n{message}")

    return system, "".join(parts)


def build_tell_prompt(
    ctx: UserContext,
    message: str,
    voice_text: str | None = None,
    dampening_instruction: str | None = None,
    partner_summary: str | None = None,
    goals_text: str | None = None,
    smart_ctx: SmartContext | None = None,
) -> tuple[str, str]:
    """Build prompt for /tell or #send — relay drafting.

    Returns (system_prompt, user_content).
    """
    persona = _get_persona()
    style_section = ""
    if dampening_instruction or voice_text:
        style_parts: list[str] = []
        if dampening_instruction:
            style_parts.append(dampening_instruction)
        if voice_text:
            style_parts.append(voice_text)
        style_section = "\n\n## Communication Style\n" + "\n".join(style_parts)
    goals_section = ""
    if goals_text:
        goals_section = "\n" + goals_text
    system = (
        f"{persona}\n\n{_ROLE}\n\n{_NOT_THERAPY}\n\n{_TONE}\n\n"
        "The user wants to send a message to their partner. "
        "Your job is to rewrite it so it's kind, gentle, and "
        "non-confrontational while preserving the substance.\n\n"
        f"{_JSON_FORMAT}\n"
        "IMPORTANT: relay_draft is REQUIRED for this request — "
        "always provide a softened version of the message."
        f"{style_section}"
        f"{goals_section}"
    )

    parts: list[str] = []
    if smart_ctx:
        parts.append(_format_smart_context(smart_ctx))
    else:
        parts.append(_format_facts("Your private notes", ctx.private_facts))
        parts.append(_format_facts("Shared notes", ctx.shared_facts))
    if partner_summary:
        parts.append(partner_summary)
    parts.append(f"\n## Message to relay to partner\n{message}")

    return system, "".join(parts)


def build_mediate_prompt(shared_facts: list[str]) -> tuple[str, str]:
    """Build prompt for /mediate — neutral perspective.

    PRIVACY: uses ONLY shared facts. No private facts from either partner.

    Returns (system_prompt, user_content).
    """
    persona = _get_persona()
    system = (
        f"{persona}\n\n{_ROLE}\n\n{_NOT_THERAPY}\n\n{_TONE}\n\n"
        "The user is asking for a neutral, balanced perspective on their "
        "relationship situation. Use ONLY the shared context below — "
        "do NOT reference anything private.\n\n"
        f"{_JSON_FORMAT}\n"
        "IMPORTANT: relay_draft should be null for mediation responses."
    )

    parts: list[str] = []
    parts.append(_format_facts(
        "Shared context (both partners agreed to share)", shared_facts
    ))
    parts.append(
        "\n## Request\n"
        "Please provide a neutral, balanced perspective on our situation "
        "based on the shared context above."
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
    persona = _get_persona()
    style_section = ""
    if dampening_instruction or voice_text:
        style_parts: list[str] = []
        if dampening_instruction:
            style_parts.append(dampening_instruction)
        if voice_text:
            style_parts.append(voice_text)
        style_section = "\n\n## Communication Style\n" + "\n".join(style_parts)
    system = (
        f"{persona}\n\n{_ROLE}\n\n{_NOT_THERAPY}\n\n{_TONE}\n\n"
        "The user wants to save some information. Extract key facts "
        "from their message. Do NOT draft a relay.\n\n"
        f"{_JSON_FORMAT}\n"
        "IMPORTANT: relay_draft MUST be null for #keep messages."
        f"{style_section}"
    )

    parts: list[str] = []
    if smart_ctx:
        parts.append(_format_smart_context(smart_ctx))
    else:
        parts.append(_format_facts("Your private notes", ctx.private_facts))
    parts.append(f"\n## Message to save\n{message}")

    return system, "".join(parts)


def build_digest_prompt(
    shared_facts: list[str], sent_relays: list[str]
) -> tuple[str, str]:
    """Build prompt for the daily digest — neutral day summary.

    Returns (system_prompt, user_content).
    """
    persona = _get_persona()
    system = (
        f"{persona}\n\n{_ROLE}\n\n{_NOT_THERAPY}\n\n{_TONE}\n\n"
        "Generate a brief, neutral daily summary of today's shared activity. "
        "Keep it warm and balanced. Do not take sides. Do not use JSON — "
        "reply with plain text only."
    )

    parts: list[str] = []
    if shared_facts:
        items = "\n".join(f"- {f}" for f in shared_facts)
        parts.append(f"## New shared facts today\n{items}\n")
    if sent_relays:
        items = "\n".join(f"- {r}" for r in sent_relays)
        parts.append(f"## Messages exchanged today\n{items}\n")
    parts.append(
        "\nPlease summarise today's activity in a warm, balanced way."
    )

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
    persona = _get_persona()
    system = (
        f"{persona}\n\n{_ROLE}\n\n{_NOT_THERAPY}\n\n{_TONE}\n\n"
        "Generate a warm, blame-free summary of both partners' reflections. "
        "Never reveal which partner said what specifically. Focus on common "
        "themes and positive momentum. Do not use JSON — reply with plain "
        "text only."
    )

    parts: list[str] = []
    parts.append(f"## Partner A reflections\n{partner_a_answers}\n")
    parts.append(f"## Partner B reflections\n{partner_b_answers}\n")
    if shared_facts:
        items = "\n".join(f"- {f}" for f in shared_facts)
        parts.append(f"## Shared context\n{items}\n")
    if goals:
        parts.append(goals)
    parts.append(
        "\nPlease provide a warm, blame-free summary focusing on common "
        "themes and growth opportunities."
    )

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
    persona = _get_persona()
    style_section = ""
    if dampening_instruction or voice_text:
        style_parts: list[str] = []
        if dampening_instruction:
            style_parts.append(dampening_instruction)
        if voice_text:
            style_parts.append(voice_text)
        style_section = "\n\n## Communication Style\n" + "\n".join(style_parts)
    system = (
        f"{persona}\n\n{_ROLE}\n\n{_NOT_THERAPY}\n\n"
        "The user has requested BLUNT MODE. Give your direct, honest "
        "assessment. No sugar coating, no hedging. Be blunt but "
        "constructive — honest doesn't mean cruel. This is for the "
        "sender's own reflection and growth.\n\n"
        f"{_JSON_FORMAT}\n"
        "IMPORTANT: relay_draft should be null for blunt responses "
        "(unless #send is also present, but that is handled separately)."
        f"{style_section}"
    )

    parts: list[str] = []
    if smart_ctx:
        parts.append(_format_smart_context(smart_ctx))
    else:
        parts.append(_format_facts("Your private notes", ctx.private_facts))
        parts.append(_format_facts("Shared notes", ctx.shared_facts))
    parts.append(_format_history(ctx.recent_messages))
    parts.append(f"\n## Current message (blunt feedback requested)\n{message}")

    return system, "".join(parts)
