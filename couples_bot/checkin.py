"""Weekly check-in — reflection questions and blame-free summary."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from couples_bot import db as db_mod
from couples_bot.i18n import t
from couples_bot.llm import LLMClient
from couples_bot.prompts import build_checkin_summary_prompt

logger = logging.getLogger(__name__)

SendFunc = Callable[[int, str], Awaitable[None]]

_DAY_MAP = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_DEFAULT_QUESTIONS = [
    "What went well this week?",
    "What could we do differently?",
    "What are you grateful for about us?",
]


async def checkin_loop(
    db_path: str,
    llm: LLMClient,
    send_func: SendFunc,
    checkin_day: str = "sunday",
    checkin_time: str = "18:00",
    checkin_tz: str = "Asia/Kolkata",
    checkin_questions: list[str] | None = None,
    poll_interval: float = 60.0,
) -> None:
    """Periodically check if it's check-in time, then send questions.

    Runs forever as a background ``asyncio.Task``.
    """
    questions = checkin_questions or _DEFAULT_QUESTIONS

    while True:
        try:
            await _check_and_send(
                db_path, llm, send_func,
                checkin_day, checkin_time, checkin_tz, questions,
            )
        except Exception:
            logger.exception("Error in checkin_loop")
        await asyncio.sleep(poll_interval)


async def _check_and_send(
    db_path: str,
    llm: LLMClient,
    send_func: SendFunc,
    checkin_day: str,
    checkin_time: str,
    checkin_tz: str,
    questions: list[str],
) -> None:
    """If the current day/time matches, send check-in questions."""
    try:
        tz = ZoneInfo(checkin_tz)
    except KeyError:
        logger.error("Invalid timezone %r — skipping check-in", checkin_tz)
        return

    now = datetime.now(tz)
    target_weekday = _DAY_MAP.get(checkin_day.lower())
    if target_weekday is None:
        logger.error("Invalid checkin_day %r", checkin_day)
        return

    target_hour, target_minute = (int(p) for p in checkin_time.split(":"))

    if now.weekday() != target_weekday:
        return
    if now.hour != target_hour or now.minute != target_minute:
        return

    couples = await db_mod.get_all_couples(db_path)
    for user_a, user_b in couples:
        couple_id = db_mod.make_couple_id(user_a, user_b)

        # Don't re-send if there's already an active check-in this week
        active = await db_mod.get_active_checkin(db_path, couple_id)
        if active is not None:
            continue

        week_start = now.strftime("%Y-%m-%d")
        await db_mod.create_checkin(
            db_path, couple_id, week_start, user_a, user_b,
        )

        # Send questions to both partners
        for uid in (user_a, user_b):
            user = await db_mod.get_user(db_path, uid)
            lang = (user or {}).get("lang") or "en"
            header = t(lang, "checkin_questions_header")
            items = "\n".join(
                t(lang, "checkin_question_item", index=i + 1, question=q)
                for i, q in enumerate(questions)
            )
            await send_func(uid, f"{header}\n{items}")

        logger.info("Weekly check-in questions sent for couple %s", couple_id)


async def process_checkin_answer(
    db_path: str,
    llm: LLMClient,
    send_func: SendFunc,
    user_id: int,
    text: str,
) -> bool:
    """Store a check-in answer and generate summary if both partners answered.

    Returns ``True`` if the message was consumed as a check-in answer,
    ``False`` if there's no active check-in for this user.
    """
    user = await db_mod.get_user(db_path, user_id)
    if not user or not user.get("partner_id"):
        return False

    partner_id = user["partner_id"]
    couple_id = db_mod.make_couple_id(user_id, partner_id)
    checkin = await db_mod.get_active_checkin(db_path, couple_id)
    if checkin is None:
        return False

    # Determine which partner slot this user fills
    if user_id == checkin["partner_a_id"]:
        if checkin.get("partner_a_answers"):
            return False  # Already answered
        await db_mod.update_checkin_answers(
            db_path, checkin["id"], "a", text,
        )
    elif user_id == checkin["partner_b_id"]:
        if checkin.get("partner_b_answers"):
            return False  # Already answered
        await db_mod.update_checkin_answers(
            db_path, checkin["id"], "b", text,
        )
    else:
        return False

    # Notify user their answer was recorded
    lang = (user or {}).get("lang") or "en"

    # Re-read to check if both answered
    checkin = await db_mod.get_active_checkin(db_path, couple_id)
    if checkin is None:
        return True

    if checkin.get("partner_a_answers") and checkin.get("partner_b_answers"):
        # Both answered — generate summary
        await _generate_summary(
            db_path, llm, send_func, checkin, user_id, partner_id,
        )
    else:
        # Notify: waiting for partner
        await send_func(user_id, t(lang, "checkin_waiting_partner"))

    return True


async def _generate_summary(
    db_path: str,
    llm: LLMClient,
    send_func: SendFunc,
    checkin: dict,
    user_a: int,
    user_b: int,
) -> None:
    """Generate and send the blame-free summary once both partners answered."""
    a_answers = checkin["partner_a_answers"]
    b_answers = checkin["partner_b_answers"]

    # Get shared context for richer summary
    shared_facts = await db_mod.get_shared_facts_for_pair(db_path, user_a, user_b)
    shared_texts = [f["text"] for f in shared_facts]

    # Build goals section for the check-in (FEAT-004)
    couple_id = db_mod.make_couple_id(user_a, user_b)
    goals_parts: list[str] = []
    for uid in (user_a, user_b):
        personal = await db_mod.get_active_goals(db_path, uid)
        if personal:
            items = "\n".join(f"- {g['text']}" for g in personal)
            goals_parts.append(f"### Personal\n{items}")
    shared_goals = await db_mod.get_shared_goals(db_path, couple_id)
    if shared_goals:
        items = "\n".join(f"- {g['text']}" for g in shared_goals)
        goals_parts.append(f"### Shared\n{items}")
    goals_text = (
        "## Active Goals\n" + "\n".join(goals_parts) + "\n"
    ) if goals_parts else None

    system_prompt, user_content = build_checkin_summary_prompt(
        a_answers, b_answers, shared_texts, goals=goals_text,
    )
    response = await llm.generate(user_content, system_prompt)
    summary = response.get("reply", "")

    if not summary:
        return

    # Mark complete with summary
    await db_mod.mark_checkin_complete(db_path, checkin["id"], summary)

    # Send to both
    for uid in (checkin["partner_a_id"], checkin["partner_b_id"]):
        u = await db_mod.get_user(db_path, uid)
        lang = (u or {}).get("lang") or "en"
        header = t(lang, "checkin_summary_header")
        await send_func(uid, f"{header}\n{summary}")

    logger.info("Check-in summary sent for couple %s", checkin["couple_id"])
