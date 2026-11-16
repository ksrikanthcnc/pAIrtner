"""Prompt building for LLM calls — privacy-safe by construction.

PRIVACY RULES (violations are bugs):
1. User X's prompt contains ONLY: X's own private facts + shared facts
   from either partner.  NEVER the partner's private facts.
2. Even shared content goes out as distilled context, never raw notes.
3. Never quote or confirm what the partner said privately.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UserContext:
    """All the per-user data a prompt may reference."""

    user_name: str = ""
    partner_name: str | None = None
    private_facts: list[str] = field(default_factory=list)
    shared_facts: list[str] = field(default_factory=list)
    recent_messages: list[tuple[str, str]] = field(default_factory=list)
    lang: str = "en"


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
    {"text": "<a concise fact to remember>", "visibility": "private"}
  ],
  "relay_draft": "<a softened version of the message for the partner>" or null
}

Rules:
- "reply" is always required.
- "new_facts" may be empty []. Mark each fact "private" unless the user
  explicitly wants their partner to know, in which case use "shared".
- "relay_draft": provide a kind, non-confrontational rewrite if the
  message contains something the user might want to share with their
  partner. Set to null otherwise.
- Keep facts concise (one sentence each).
- Never include the partner's private information in your reply.
"""

_TONE = (
    "Tone guidelines: be warm, empathetic, non-judgmental. "
    "Soften harsh language while preserving the core meaning. "
    "Never take sides. Use simple, clear language."
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


def build_system_prompt() -> str:
    """Base system prompt shared across prompt types."""
    return f"{_ROLE}\n\n{_NOT_THERAPY}\n\n{_TONE}\n\n{_JSON_FORMAT}"


def build_chat_prompt(ctx: UserContext, message: str) -> tuple[str, str]:
    """Build prompt for a regular chat message.

    Returns (system_prompt, user_content).
    """
    system = build_system_prompt()

    parts: list[str] = []
    parts.append(_format_facts("Your private notes", ctx.private_facts))
    parts.append(_format_facts("Shared notes", ctx.shared_facts))
    parts.append(_format_history(ctx.recent_messages))
    parts.append(f"\n## Current message\n{message}")

    return system, "".join(parts)


def build_tell_prompt(ctx: UserContext, message: str) -> tuple[str, str]:
    """Build prompt for /tell or #send — relay drafting.

    Returns (system_prompt, user_content).
    """
    system = (
        f"{_ROLE}\n\n{_NOT_THERAPY}\n\n{_TONE}\n\n"
        "The user wants to send a message to their partner. "
        "Your job is to rewrite it so it's kind, gentle, and "
        "non-confrontational while preserving the substance.\n\n"
        f"{_JSON_FORMAT}\n"
        "IMPORTANT: relay_draft is REQUIRED for this request — "
        "always provide a softened version of the message."
    )

    parts: list[str] = []
    parts.append(_format_facts("Your private notes", ctx.private_facts))
    parts.append(_format_facts("Shared notes", ctx.shared_facts))
    parts.append(f"\n## Message to relay to partner\n{message}")

    return system, "".join(parts)


def build_mediate_prompt(shared_facts: list[str]) -> tuple[str, str]:
    """Build prompt for /mediate — neutral perspective.

    PRIVACY: uses ONLY shared facts. No private facts from either partner.

    Returns (system_prompt, user_content).
    """
    system = (
        f"{_ROLE}\n\n{_NOT_THERAPY}\n\n{_TONE}\n\n"
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


def build_keep_prompt(ctx: UserContext, message: str) -> tuple[str, str]:
    """Build prompt for #keep — fact extraction only.

    Returns (system_prompt, user_content).
    """
    system = (
        f"{_ROLE}\n\n{_NOT_THERAPY}\n\n{_TONE}\n\n"
        "The user wants to save some information. Extract key facts "
        "from their message. Do NOT draft a relay.\n\n"
        f"{_JSON_FORMAT}\n"
        "IMPORTANT: relay_draft MUST be null for #keep messages."
    )

    parts: list[str] = []
    parts.append(_format_facts("Your private notes", ctx.private_facts))
    parts.append(f"\n## Message to save\n{message}")

    return system, "".join(parts)
