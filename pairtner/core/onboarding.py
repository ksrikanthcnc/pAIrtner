"""Onboarding flow — single free-text prompt, LLM extracts structured data.

After pairing, the user gets ONE open-ended prompt. Their response (any length)
is sent to the LLM with a JSON extraction schema. The LLM returns structured
profile data which we save to the profile + as core facts. No rigid questionnaire.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pairtner import db as db_mod
from pairtner.i18n import t

logger = logging.getLogger(__name__)

# The system prompt for profile extraction
_EXTRACT_SYSTEM = """\
You are a data extraction assistant. The user just described themselves and their \
relationship. Extract structured data from their text and respond with ONLY a JSON \
object (no markdown, no explanation). Use null for anything not mentioned.

Required JSON shape:
{
  "relationship_type": "married" | "dating" | "other" | null,
  "duration_years": <integer or null>,
  "has_kids": true | false | null,
  "age_range": "<string like '30s' or '25-30' or null>",
  "appreciates": "<what they appreciate about partner, or null>",
  "wish": "<what they wish for the relationship, or null>",
  "summary": "<1-2 sentence summary of what they shared>"
}

Rules:
- Extract ONLY what's explicitly stated or clearly implied.
- For duration_years, convert phrases like "a decade" to 10, "few years" to 3.
- For has_kids, infer from mentions of "son", "daughter", "children", "toddler" etc.
- The summary should capture the essence in the user's own tone.
- If the text is very short or uninformative, still return the JSON with mostly nulls.
"""


def _extract_from_free_text(text: str) -> dict:
    """Regex fallback for when LLM extraction is unavailable.

    Returns a dict of profile fields found. Used as a safety net.
    """
    import re

    result: dict = {}
    low = text.lower()

    for word in ("married", "dating"):
        if word in low:
            result["relationship_type"] = word
            break

    dur_match = re.search(r'(\d+)\s*(?:years?|yrs?|yr)', low)
    if dur_match:
        result["duration_years"] = int(dur_match.group(1))

    if any(w in low for w in ("kids", "children", "son", "daughter", "child")):
        if any(w in low for w in ("no kids", "no children", "don't have kids", "dont have kids")):
            result["has_kids"] = 0
        else:
            result["has_kids"] = 1

    age_match = re.search(r'\b(\d{2})\s*(?:years?\s*old|yo|age)\b', low)
    if age_match:
        result["age_range"] = age_match.group(1)

    return result


def _parse_llm_extraction(raw: str) -> dict[str, Any] | None:
    """Parse LLM JSON output, tolerating markdown fences."""
    text = raw.strip()
    # Strip ```json ... ``` wrapper if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    return None


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
        return True
    return profile["onboarding_done"] == 0 and profile["onboarding_step"] == 0


async def get_onboarding_question(
    db_path: str, user_id: int, lang: str = "en"
) -> tuple[str, int] | None:
    """Return the onboarding prompt (always step 0 — single free-text prompt)."""
    profile = await db_mod.ensure_user_profile(db_path, user_id)
    if profile["onboarding_done"]:
        return None
    if profile["onboarding_step"] == 0:
        return t(lang, "onboard_welcome"), 0
    return None


async def process_onboarding_answer(
    db_path: str, user_id: int, text: str, lang: str = "en",
    llm: Any = None,
) -> tuple[bool, str | None]:
    """Process the user's free-text onboarding response.

    If *llm* is provided, uses LLM to extract structured JSON.
    Falls back to regex extraction if LLM is unavailable or fails.

    Returns ``(consumed, response_text_or_none)``.
    """
    profile = await db_mod.ensure_user_profile(db_path, user_id)
    if profile["onboarding_done"]:
        return False, None

    # /skip or any short/greeting message → complete onboarding, start chatting
    clean = text.strip()
    if not clean:
        return True, t(lang, "onboard_welcome")

    low = clean.lower().rstrip("!.?,")
    is_greeting = low in (
        "hi", "hey", "hello", "yo", "sup", "hii", "hiii",
        "hey there", "hi there", "hello there", "hola", "namaste",
        "ok", "okay", "sure", "yes", "yep", "yeah", "ya",
        "start", "go", "lets go", "let's go",
    )
    if low == "/skip" or is_greeting:
        await db_mod.update_user_profile(db_path, user_id, onboarding_done=1)
        await save_onboarding_as_facts(db_path, user_id)
        return True, t(lang, "onboard_complete")

    # Try LLM extraction first
    extracted: dict[str, Any] = {}
    if llm is not None:
        try:
            result = await llm.generate(clean, _EXTRACT_SYSTEM)
            reply = result.get("reply", "")
            parsed = _parse_llm_extraction(reply)
            if parsed:
                extracted = parsed
                logger.info("LLM extraction: %s", extracted)
        except Exception:
            logger.warning("LLM extraction failed, falling back to regex", exc_info=True)

    # Regex fallback if LLM didn't produce results
    if not extracted:
        extracted = _extract_from_free_text(clean)

    # Map LLM output to profile fields
    profile_updates: dict[str, Any] = {}

    rel = extracted.get("relationship_type")
    if rel and isinstance(rel, str):
        profile_updates["relationship_type"] = rel.lower()

    dur = extracted.get("duration_years")
    if dur is not None:
        try:
            profile_updates["duration_years"] = int(dur)
        except (ValueError, TypeError):
            pass

    kids = extracted.get("has_kids")
    if kids is not None:
        profile_updates["has_kids"] = 1 if kids else 0

    age = extracted.get("age_range")
    if age and isinstance(age, str):
        profile_updates["age_range"] = age

    # Build a love_story_brief from the summary or raw text
    summary = extracted.get("summary") or clean[:500]
    profile_updates["love_story_brief"] = summary[:500]

    # Mark onboarding as done
    profile_updates["onboarding_step"] = 6
    profile_updates["onboarding_done"] = 1

    await db_mod.update_user_profile(db_path, user_id, **profile_updates)

    # Save the raw text as a core fact
    fid = await db_mod.add_fact(db_path, user_id, clean[:500], "private", fact_type="stable")
    await db_mod.promote_to_core(db_path, fid)

    # Save extracted specifics as core facts
    await save_onboarding_as_facts(db_path, user_id)

    # Build a personalized response
    appreciates = extracted.get("appreciates")
    wish = extracted.get("wish")
    if appreciates or wish:
        parts = [t(lang, "onboard_complete")]
        if appreciates:
            parts.append(f"\n\n💕 I'll remember what you appreciate: {appreciates}")
        if wish:
            parts.append(f"\n🌟 And your wish: {wish}")
        return True, "".join(parts)

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

    count = 0
    for text in facts:
        fid = await db_mod.add_fact(db_path, user_id, text, "private", fact_type="stable")
        await db_mod.promote_to_core(db_path, fid)
        count += 1

    return count
