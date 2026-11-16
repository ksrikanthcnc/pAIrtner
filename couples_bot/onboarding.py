"""Onboarding interview flow — collects user profile data after pairing."""

from __future__ import annotations

import logging

from couples_bot import db as db_mod
from couples_bot.i18n import t

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Question definitions: (step_num, i18n_key, field_name, validator)
# ---------------------------------------------------------------------------


def _validate_relationship_type(text: str) -> str | None:
    lowered = text.strip().lower()
    if lowered in ("married", "dating", "other"):
        return lowered
    # Accept close variants
    if lowered in ("marriage", "marry"):
        return "married"
    if lowered in ("date", "relationship"):
        return "dating"
    return text.strip()


def _validate_duration(text: str) -> int | None:
    text = text.strip().split()[0] if text.strip() else ""
    try:
        return max(0, int(text))
    except ValueError:
        return None


def _validate_has_kids(text: str) -> int | None:
    lowered = text.strip().lower()
    if lowered in ("yes", "y", "1", "true", "haa", "avunu"):
        return 1
    if lowered in ("no", "n", "0", "false", "ledu", "nahi"):
        return 0
    return None


def _validate_age_range(text: str) -> str | None:
    return text.strip() or None


def _validate_free_text(text: str) -> str | None:
    return text.strip() or None


ONBOARDING_QUESTIONS: list[tuple[int, str, str, object]] = [
    (1, "onboard_q_relationship_type", "relationship_type", _validate_relationship_type),
    (2, "onboard_q_duration", "duration_years", _validate_duration),
    (3, "onboard_q_kids", "has_kids", _validate_has_kids),
    (4, "onboard_q_age_range", "age_range", _validate_age_range),
    (5, "onboard_q_appreciate", "_appreciate", _validate_free_text),
    (6, "onboard_q_wish", "_wish", _validate_free_text),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def should_start_onboarding(db_path: str, user_id: int) -> bool:
    """Return True if the user is paired, onboarding not done, and step is 0."""
    user = await db_mod.get_user(db_path, user_id)
    if not user or not user.get("partner_id"):
        return False
    profile = await db_mod.get_user_profile(db_path, user_id)
    if profile is None:
        return True  # No profile yet → needs onboarding
    return profile["onboarding_done"] == 0 and profile["onboarding_step"] == 0


async def get_onboarding_question(
    db_path: str, user_id: int, lang: str = "en"
) -> tuple[str, int] | None:
    """Return ``(question_text, step_number)`` for the next question, or None."""
    profile = await db_mod.ensure_user_profile(db_path, user_id)
    if profile["onboarding_done"]:
        return None
    step = profile["onboarding_step"]
    for q_step, i18n_key, _field, _validator in ONBOARDING_QUESTIONS:
        if q_step > step:
            return t(lang, i18n_key), q_step
    return None


async def process_onboarding_answer(
    db_path: str, user_id: int, text: str, lang: str = "en"
) -> tuple[bool, str | None]:
    """Validate and store the answer, advance the step.

    Returns ``(consumed, next_question_text_or_none)``.
    If the user sends ``/skip``, onboarding is marked done immediately.
    """
    profile = await db_mod.ensure_user_profile(db_path, user_id)
    if profile["onboarding_done"]:
        return False, None

    # /skip ends onboarding early
    if text.strip().lower() == "/skip":
        await db_mod.update_user_profile(db_path, user_id, onboarding_done=1)
        return True, t(lang, "onboard_skip")

    current_step = profile["onboarding_step"]
    # Find the question for current_step + 1 (next unanswered)
    target_q = None
    for q_step, _key, field, validator in ONBOARDING_QUESTIONS:
        if q_step > current_step:
            target_q = (q_step, field, validator)
            break

    if target_q is None:
        # All questions answered — mark done
        await db_mod.update_user_profile(db_path, user_id, onboarding_done=1)
        return False, None

    q_step, field_name, validator = target_q
    validated = validator(text)

    # Store the answer
    if field_name == "_appreciate":
        # Stash temporarily — we'll combine with _wish later
        await db_mod.update_user_profile(
            db_path, user_id,
            onboarding_step=q_step,
            love_story_brief=f"Appreciates: {validated or text.strip()}",
        )
    elif field_name == "_wish":
        # Combine with existing appreciate text
        existing = (await db_mod.get_user_profile(db_path, user_id)) or {}
        brief = existing.get("love_story_brief") or ""
        combined = f"{brief}. Wishes: {validated or text.strip()}"
        await db_mod.update_user_profile(
            db_path, user_id,
            onboarding_step=q_step,
            onboarding_done=1,
            love_story_brief=combined,
        )
        return True, t(lang, "onboard_complete")
    elif field_name == "duration_years":
        if validated is None:
            # Re-ask same question on invalid input
            return True, t(lang, "onboard_q_duration")
        await db_mod.update_user_profile(
            db_path, user_id,
            onboarding_step=q_step,
            **{field_name: validated},
        )
    elif field_name == "has_kids":
        if validated is None:
            return True, t(lang, "onboard_q_kids")
        await db_mod.update_user_profile(
            db_path, user_id,
            onboarding_step=q_step,
            **{field_name: validated},
        )
    else:
        await db_mod.update_user_profile(
            db_path, user_id,
            onboarding_step=q_step,
            **{field_name: validated or text.strip()},
        )

    # Return next question
    nxt = await get_onboarding_question(db_path, user_id, lang)
    if nxt:
        return True, nxt[0]
    return True, t(lang, "onboard_complete")
