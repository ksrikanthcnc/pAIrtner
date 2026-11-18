"""Onboarding interview flow — collects user profile data after pairing."""

from __future__ import annotations

import logging

from pairtner import db as db_mod
from pairtner.i18n import t

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
    # Step 0 is free-text (handled specially in process_onboarding_answer)
    (1, "onboard_q_relationship_type", "relationship_type", _validate_relationship_type),
    (2, "onboard_q_duration", "duration_years", _validate_duration),
    (3, "onboard_q_kids", "has_kids", _validate_has_kids),
    (4, "onboard_q_age_range", "age_range", _validate_age_range),
    (5, "onboard_q_appreciate", "_appreciate", _validate_free_text),
    (6, "onboard_q_wish", "_wish", _validate_free_text),
]


def _extract_from_free_text(text: str) -> dict:
    """Best-effort extraction of structured data from free-text onboarding.

    Returns a dict of profile fields found. The LLM does the real extraction
    later via memory_ops; this catches the obvious structured bits.
    """
    result: dict = {}
    low = text.lower()

    # Relationship type
    for word in ("married", "dating"):
        if word in low:
            result["relationship_type"] = word
            break

    # Duration (look for "N years" or just a number in context)
    import re
    dur_match = re.search(r'(\d+)\s*(?:years?|yrs?|yr)', low)
    if dur_match:
        result["duration_years"] = int(dur_match.group(1))

    # Kids
    if any(w in low for w in ("kids", "children", "son", "daughter", "child")):
        if any(w in low for w in ("no kids", "no children", "don't have kids", "dont have kids")):
            result["has_kids"] = 0
        else:
            result["has_kids"] = 1

    # Age
    age_match = re.search(r'\b(\d{2})\s*(?:years?\s*old|yo|age)\b', low)
    if age_match:
        result["age_range"] = age_match.group(1)

    return result


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
        await save_onboarding_as_facts(db_path, user_id)
        return True, t(lang, "onboard_skip")

    current_step = profile["onboarding_step"]

    # Step 0: first response after pairing — try free-text extraction
    if current_step == 0:
        extracted = _extract_from_free_text(text)
        if extracted:
            # Save whatever we extracted
            await db_mod.update_user_profile(db_path, user_id, **extracted)

        # If the text is substantial (30+ chars), save as love_story_brief and complete
        if len(text.strip()) >= 30:
            await db_mod.update_user_profile(
                db_path, user_id,
                onboarding_step=6,
                onboarding_done=1,
                love_story_brief=text.strip()[:500],
            )
            # Also save the free text as a core fact
            fid = await db_mod.add_fact(db_path, user_id, text.strip()[:500], "private", fact_type="stable")
            await db_mod.promote_to_core(db_path, fid)
            await save_onboarding_as_facts(db_path, user_id)
            return True, t(lang, "onboard_complete")

        # Short answer — try to validate as relationship_type, then advance to structured Qs
        rel = _validate_relationship_type(text)
        if rel:
            await db_mod.update_user_profile(db_path, user_id, onboarding_step=1, relationship_type=rel)
            nxt = await get_onboarding_question(db_path, user_id, lang)
            return True, nxt[0] if nxt else t(lang, "onboard_complete")

        # Can't parse — just save as brief and ask first structured question
        if text.strip():
            await db_mod.update_user_profile(db_path, user_id, onboarding_step=0, love_story_brief=text.strip()[:500])
        nxt = await get_onboarding_question(db_path, user_id, lang)
        return True, nxt[0] if nxt else t(lang, "onboard_complete")

    # Find the question for current_step + 1 (next unanswered)
    target_q = None
    for q_step, _key, field, validator in ONBOARDING_QUESTIONS:
        if q_step > current_step:
            target_q = (q_step, field, validator)
            break

    if target_q is None:
        # All questions answered — mark done
        await db_mod.update_user_profile(db_path, user_id, onboarding_done=1)
        await save_onboarding_as_facts(db_path, user_id)
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
        await save_onboarding_as_facts(db_path, user_id)
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


async def save_onboarding_as_facts(db_path: str, user_id: int) -> int:
    """Convert onboarding profile answers into core facts for the LLM.

    Called when onboarding completes. Returns the number of facts created.
    """
    profile = await db_mod.get_user_profile(db_path, user_id)
    if not profile:
        return 0

    user = await db_mod.get_user(db_path, user_id)
    partner_name = None
    if user and user.get("partner_id"):
        partner = await db_mod.get_user(db_path, user["partner_id"])
        partner_name = (partner or {}).get("name")

    facts: list[str] = []

    rel = profile.get("relationship_type")
    dur = profile.get("duration_years")
    if rel and dur:
        partner_part = f" to {partner_name}" if partner_name else ""
        facts.append(f"{rel.capitalize()}{partner_part}, {dur} years")
    elif rel:
        facts.append(f"Relationship type: {rel}")

    if profile.get("has_kids"):
        facts.append("Has kids")

    age = profile.get("age_range")
    if age:
        facts.append(f"Age: {age}")

    brief = profile.get("love_story_brief")
    if brief:
        facts.append(brief)

    count = 0
    for text in facts:
        fid = await db_mod.add_fact(db_path, user_id, text, "private", fact_type="stable")
        await db_mod.promote_to_core(db_path, fid)
        count += 1

    return count
