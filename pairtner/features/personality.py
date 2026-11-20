"""Conversational personality questionnaire.

Casual questions asked one at a time, spread over many conversations, that
tune interaction mode, conflict style, reply length and check-in frequency.
State lives in ``user_meta`` under the ``personality:`` prefix.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from pairtner import db as db_mod
from pairtner.core.memory import SETTINGS_META_PREFIX, validate_setting
from pairtner.storage import facts as facts_mod
from pairtner.storage.helpers import _fetch_one

logger = logging.getLogger(__name__)

MIN_MESSAGES = 5          # user must have sent more than this many messages
MIN_GAP_MESSAGES = 3      # messages since last question must exceed this

ANSWERED_KEY = "personality:answered"
PENDING_KEY = "personality:pending"
LAST_ASKED_KEY = "personality:last_asked_count"
UNCLEAR = "unclear"


# key = user_meta key; options map the label the LLM returns to the stored value.
PERSONALITY_QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "q1",
        "text": "When something's bugging you, do you usually want help figuring it out, or just someone to listen?",
        "key": "default_interaction_mode",
        "options": {"help": "solution", "listen": "vent"},
        "purpose": "help -> solution, listen -> vent",
        "confirmation": {
            "solution": "Got it, I'll lean toward helping you work things out.",
            "vent": "Got it, I'll listen first and hold the advice.",
        },
    },
    {
        "id": "q2",
        "text": "If your partner said something that upset you, would you rather talk it out right away or cool off first?",
        "key": "conflict_style",
        "options": {"talk": "direct", "cool_off": "cool_off"},
        "purpose": "talk now -> direct, cool off -> cool_off",
        "confirmation": {
            "direct": "Noted, you like to talk things through right away.",
            "cool_off": "Noted, you like some space before talking it through.",
        },
    },
    {
        "id": "q3",
        "text": "Do you prefer texts that are short and to the point, or longer and more detailed?",
        "key": "response_length",
        "options": {"short": "short", "long": "long"},
        "purpose": "short -> short, detailed -> long",
        "confirmation": {
            "short": "Short and sweet it is.",
            "long": "Okay, I'll give you more detail.",
        },
    },
    {
        "id": "q4",
        "text": "When you share something personal, do you want validation first or advice?",
        "key": "default_interaction_mode",
        "options": {"validation": "vent", "advice": "solution"},
        "purpose": "validation -> vent, advice -> solution (reinforces q1)",
        "confirmation": {
            "vent": "Validation first, understood.",
            "solution": "Advice it is, I'll get practical.",
        },
    },
    {
        "id": "q5",
        "text": "How do you feel about surprise check-ins from me? Love them, or prefer I wait till you reach out?",
        "key": "camouflage_frequency",
        "options": {"love": "normal", "wait": "low"},
        "purpose": "love -> normal, wait -> low",
        "confirmation": {
            "normal": "Great, I'll check in now and then.",
            "low": "No problem, I'll wait for you to reach out.",
        },
    },
]
_BY_ID = {q["id"]: q for q in PERSONALITY_QUESTIONS}

# Keys that also drive live behaviour via the validated settings whitelist.
_SETTING_KEYS = {
    "default_interaction_mode": "interaction_mode",
    "response_length": "response_length",
    "camouflage_frequency": "camouflage_frequency",
}


async def _get_meta(db_path: str, user_id: int, key: str) -> str | None:
    row = await _fetch_one(
        db_path,
        "SELECT value FROM user_meta WHERE user_id = ? AND key = ?",
        (user_id, key),
    )
    return row["value"] if row else None


async def _get_answered(db_path: str, user_id: int) -> list[str]:
    raw = await _get_meta(db_path, user_id, ANSWERED_KEY)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Corrupt %s for user %s", ANSWERED_KEY, user_id)
        return []
    return [q for q in data if q in _BY_ID]


def _next_question(answered: list[str]) -> dict[str, Any] | None:
    for q in PERSONALITY_QUESTIONS:
        if q["id"] not in answered:
            return q
    return None


async def get_pending_question_id(db_path: str, user_id: int) -> str | None:
    pid = await _get_meta(db_path, user_id, PENDING_KEY)
    return pid if pid in _BY_ID else None


async def get_next_personality_question(db_path: str, user_id: int) -> str | None:
    """Return the next unanswered question text (and mark it pending), or None."""
    q = _next_question(await _get_answered(db_path, user_id))
    if q is None:
        return None
    await facts_mod.set_user_meta(db_path, user_id, PENDING_KEY, q["id"])
    await facts_mod.set_user_meta(
        db_path, user_id, LAST_ASKED_KEY,
        str(await db_mod.get_message_count(db_path, user_id)),
    )
    return q["text"]


async def should_ask_personality(db_path: str, user_id: int) -> bool:
    """True if questions remain, the user is established, and the gap has passed."""
    if _next_question(await _get_answered(db_path, user_id)) is None:
        return False
    if await get_pending_question_id(db_path, user_id):
        return False
    profile = await db_mod.get_user_profile(db_path, user_id)
    if not profile or not profile.get("onboarding_done"):
        return False
    count = profile.get("message_count") or 0
    if count <= MIN_MESSAGES:
        return False
    last = await _get_meta(db_path, user_id, LAST_ASKED_KEY)
    if last is not None:
        try:
            if count - int(last) <= MIN_GAP_MESSAGES:
                return False
        except ValueError:
            pass
    return True


def _classify_prompt(q: dict[str, Any]) -> str:
    labels = ", ".join(q["options"]) + f", {UNCLEAR}"
    return (
        "You classify a user's answer to a casual preference question.\n"
        f"Question: {q['text']}\n"
        f"Reply with exactly one word from: {labels}.\n"
        f"Use '{UNCLEAR}' if the message does not actually answer the question."
    )


def _parse_label(reply: str, q: dict[str, Any]) -> str | None:
    word = (reply or "").strip().lower().strip(" .!\"'`")
    return word if word in q["options"] else None


async def process_personality_answer(
    db_path: str, user_id: int, answer: str, llm: Any,
) -> dict[str, str]:
    """Classify *answer* for the pending question and store the result.

    Returns ``{key: value, "confirmation": str}``, or ``{}`` when there is no
    pending question or the message was not a usable answer (pending is
    cleared so normal chat continues).
    """
    qid = await get_pending_question_id(db_path, user_id)
    if qid is None:
        return {}
    q = _BY_ID[qid]
    await facts_mod.set_user_meta(db_path, user_id, PENDING_KEY, "")

    result = await llm.generate(answer, _classify_prompt(q))
    label = _parse_label(result.get("reply", ""), q)
    if label is None:
        return {}
    value = q["options"][label]
    key = q["key"]

    # Q4 reinforces Q1: disagreement means the user wants it both ways.
    if qid == "q4":
        prior = await _get_meta(db_path, user_id, key)
        if prior and prior != value:
            value = "adaptive"
    await facts_mod.set_user_meta(db_path, user_id, key, value)

    setting = _SETTING_KEYS.get(key)
    if setting:
        valid = validate_setting(setting, value)
        if valid is not None:
            await facts_mod.set_user_meta(
                db_path, user_id, SETTINGS_META_PREFIX + valid.key, valid.value,
            )

    answered = await _get_answered(db_path, user_id)
    answered.append(qid)
    await facts_mod.set_user_meta(db_path, user_id, ANSWERED_KEY, json.dumps(answered))

    confirmation = q["confirmation"].get(
        value, "Got it, I'll keep that in mind."
    )
    return {key: value, "confirmation": confirmation}
