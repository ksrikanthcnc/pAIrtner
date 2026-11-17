"""Proactive engagement — nudges, appreciation prompts, and ratio tracking.

Background task that periodically sends context-aware conversation
starters and appreciation prompts based on the Gottman 5:1 ratio.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from couples_bot import db as db_mod
from couples_bot import pack
from couples_bot.i18n import t
from couples_bot.llm import LLMClient
from couples_bot.prompts import load_task_prompt

logger = logging.getLogger(__name__)

SendFunc = Callable[[int, str], Awaitable[None]]

# Generic starters used when no shared facts are available.
_GENERIC_STARTERS_EN = [
    "How's your day going? Anything on your mind you'd like to share?",
    "Take a moment — what's something good that happened today?",
    "Hey there! Just checking in. How are you feeling right now?",
]
_GENERIC_STARTERS_TE = [
    "మీ రోజు ఎలా ఉంది? ఏదైనా చెప్పాలనుకుంటున్నారా?",
    "ఒక్క క్షణం ఆగండి — ఈ రోజు మంచిగా ఏం జరిగింది?",
    "హేయ్! ఎలా ఉన్నారు? ఇప్పుడు ఎలా feel అవుతున్నారు?",
]


def _is_quiet_hours(
    now: datetime, quiet_start: str, quiet_end: str,
) -> bool:
    """Return True if *now* is within the quiet window (no nudges).

    Quiet window means nudges are NOT sent during these hours.
    If quiet_start=09:00 and quiet_end=21:00, nudges are sent
    between 09:00-21:00 (active hours), and quiet outside that.
    """
    try:
        start_h, start_m = (int(p) for p in quiet_start.split(":"))
        end_h, end_m = (int(p) for p in quiet_end.split(":"))
    except (ValueError, AttributeError):
        return False

    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m
    now_minutes = now.hour * 60 + now.minute

    if start_minutes <= end_minutes:
        # Normal range: active between start and end
        return not (start_minutes <= now_minutes < end_minutes)
    else:
        # Wrapped range (e.g. 21:00 to 09:00)
        return not (now_minutes >= start_minutes or now_minutes < end_minutes)


async def generate_nudge(
    db_path: str,
    llm: LLMClient,
    user_id: int,
    partner_id: int,
    lang: str = "en",
) -> str:
    """Generate a context-aware nudge for *user_id*.

    Uses shared positive facts when available; falls back to generic starters.
    """
    positive_facts = await db_mod.get_positive_shared_facts(
        db_path, user_id, partner_id,
    )

    if not positive_facts:
        starters = _GENERIC_STARTERS_TE if lang == "te" else _GENERIC_STARTERS_EN
        return random.choice(starters)

    # Pick up to 3 random positive facts for context
    sample = random.sample(positive_facts, min(3, len(positive_facts)))
    facts_text = "\n".join(f"- {f['text']}" for f in sample)

    p = pack.current()
    pack.set_trace(p, "nudge")
    task_prompt = load_task_prompt("nudge") or ""
    partner = await db_mod.get_user(db_path, partner_id)
    partner_name = (partner or {}).get("name") or p.s("partner_fallback")

    system_prompt = (
        f"{task_prompt}\n\n"
        f"{p.s('nudge_partner', partner_name=partner_name)}\n"
        f"{p.s('nudge_language', lang=lang)}"
    )
    user_content = (
        f"## {p.s('nudge_facts_heading')}\n{facts_text}\n\n"
        f"{p.s('nudge_request')}"
    )

    result = await llm.generate(user_content, system_prompt)
    reply = result.get("reply", "")
    return reply if reply else random.choice(
        _GENERIC_STARTERS_TE if lang == "te" else _GENERIC_STARTERS_EN
    )


def check_appreciation_ratio(
    positive: int, negative: int, target_ratio: float = 5.0,
) -> bool:
    """Return True if the ratio is below the target (needs attention).

    If there are no negative interactions, the ratio is considered healthy.
    """
    if negative == 0:
        return False
    ratio = positive / negative
    return ratio < target_ratio


async def generate_appreciation_prompt(
    db_path: str,
    llm: LLMClient,
    user_id: int,
    partner_name: str,
    lang: str = "en",
) -> str:
    """Generate an appreciation nudge when the ratio is low."""
    p = pack.current()
    pack.set_trace(p, "appreciation")
    task_prompt = load_task_prompt("appreciation") or ""

    system_prompt = (
        f"{task_prompt}\n\n"
        f"{p.s('nudge_partner', partner_name=partner_name)}\n"
        f"{p.s('nudge_language', lang=lang)}"
    )
    user_content = p.s("appreciation_request")

    result = await llm.generate(user_content, system_prompt)
    reply = result.get("reply", "")
    if not reply:
        reply = t(lang, "nudge_appreciation", partner_name=partner_name)
    return reply


async def nudge_loop(
    db_path: str,
    llm: LLMClient,
    send_func: SendFunc,
    config: Any,
    poll_interval: float = 3600.0,
) -> None:
    """Background task: periodically send nudges and appreciation prompts.

    Runs forever as an ``asyncio.Task``.
    """
    while True:
        try:
            await _nudge_cycle(db_path, llm, send_func, config)
        except Exception:
            logger.exception("Error in nudge_loop")
        await asyncio.sleep(poll_interval)


async def _nudge_cycle(
    db_path: str,
    llm: LLMClient,
    send_func: SendFunc,
    config: Any,
) -> None:
    """Single cycle of the nudge loop — check all couples."""
    nudges_enabled = getattr(config, "nudges_enabled", True)
    appreciation_enabled = getattr(config, "appreciation_enabled", True)

    if not nudges_enabled and not appreciation_enabled:
        return

    tz_name = getattr(config, "checkin_timezone", "Asia/Kolkata")
    try:
        tz = ZoneInfo(tz_name)
    except KeyError:
        tz = ZoneInfo("UTC")

    now = datetime.now(tz)

    quiet_start = getattr(config, "nudges_quiet_start", "09:00")
    quiet_end = getattr(config, "nudges_quiet_end", "21:00")

    if _is_quiet_hours(now, quiet_start, quiet_end):
        return

    couples = await db_mod.get_all_couples(db_path)
    target_ratio = getattr(config, "appreciation_target_ratio", 5.0)
    check_days = getattr(config, "appreciation_check_days", 7)

    for user_a, user_b in couples:
        couple_id = db_mod.make_couple_id(user_a, user_b)

        # Check appreciation ratio and send prompts if needed
        if appreciation_enabled:
            pos, neg = await db_mod.get_interaction_ratio(
                db_path, couple_id, days=check_days,
            )
            if check_appreciation_ratio(pos, neg, target_ratio):
                for uid, pid in [(user_a, user_b), (user_b, user_a)]:
                    user = await db_mod.get_user(db_path, uid)
                    lang = (user or {}).get("lang") or "en"
                    partner = await db_mod.get_user(db_path, pid)
                    pname = (partner or {}).get("name") or pack.current().s(
                        "partner_fallback",
                    )
                    prompt = await generate_appreciation_prompt(
                        db_path, llm, uid, pname, lang=lang,
                    )
                    await send_func(uid, f"💛 {prompt}")
                continue  # Skip regular nudge when appreciation is triggered

        # Regular nudge
        if nudges_enabled:
            for uid, pid in [(user_a, user_b), (user_b, user_a)]:
                user = await db_mod.get_user(db_path, uid)
                lang = (user or {}).get("lang") or "en"
                if (user or {}).get("paused"):
                    continue
                nudge = await generate_nudge(
                    db_path, llm, uid, pid, lang=lang,
                )
                await send_func(uid, f"💬 {nudge}")

    logger.info("Nudge cycle completed for %d couples", len(couples))
