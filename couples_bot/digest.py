"""Daily digest — evening summary sent to both partners."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from couples_bot import db as db_mod
from couples_bot.conflict_detector import check_conflict_resolutions
from couples_bot.i18n import t
from couples_bot.llm import LLMClient
from couples_bot.prompts import build_digest_prompt

logger = logging.getLogger(__name__)

# Preserve reference to fromisoformat — tests may mock the datetime class
_parse_isoformat = datetime.fromisoformat

SendFunc = Callable[[int, str], Awaitable[None]]


async def digest_loop(
    db_path: str,
    llm: LLMClient,
    send_func: SendFunc,
    digest_time: str = "20:00",
    digest_tz: str = "Asia/Kolkata",
    poll_interval: float = 60.0,
) -> None:
    """Periodically check if it's digest time, then send daily summaries.

    Runs forever as a background ``asyncio.Task``.
    """
    while True:
        try:
            await _check_and_send(db_path, llm, send_func, digest_time, digest_tz)
        except Exception:
            logger.exception("Error in digest_loop")
        await asyncio.sleep(poll_interval)


async def _check_and_send(
    db_path: str,
    llm: LLMClient,
    send_func: SendFunc,
    digest_time: str,
    digest_tz: str,
) -> None:
    """If the current time matches digest_time, send digests for all couples."""
    try:
        tz = ZoneInfo(digest_tz)
    except KeyError:
        logger.error("Invalid timezone %r — skipping digest check", digest_tz)
        return

    now = datetime.now(tz)
    target_hour, target_minute = (int(p) for p in digest_time.split(":"))

    # Only fire during the matching minute
    if now.hour != target_hour or now.minute != target_minute:
        return

    couples = await db_mod.get_all_couples(db_path)
    if not couples:
        return

    for user_a, user_b in couples:
        couple_id = db_mod.make_couple_id(user_a, user_b)

        # Check if digest already sent today (compare in local tz)
        last_digest = await db_mod.get_couple_config(db_path, couple_id, "last_digest")
        today_str = now.strftime("%Y-%m-%d")
        if last_digest:
            try:
                last_dt = _parse_isoformat(last_digest)
                last_local = last_dt.astimezone(tz)
                if last_local.strftime("%Y-%m-%d") == today_str:
                    continue
            except (ValueError, TypeError):
                pass  # malformed — proceed as if no digest sent

        await _send_digest_for_couple(
            db_path, llm, send_func, user_a, user_b, couple_id, last_digest
        )


async def _send_digest_for_couple(
    db_path: str,
    llm: LLMClient,
    send_func: SendFunc,
    user_a: int,
    user_b: int,
    couple_id: str,
    last_digest: str | None,
) -> None:
    """Generate and send the daily digest for one couple."""
    since = last_digest or "1970-01-01T00:00:00"

    # Gather shared facts added since last digest
    shared_facts = await db_mod.get_shared_facts_for_pair(db_path, user_a, user_b)
    recent_shared = [
        f["text"] for f in shared_facts
        if f.get("created_at", "") >= since
    ]

    # Gather sent relays since last digest
    relays_a = await db_mod.get_sent_relays_since(db_path, user_a, since)
    relays_b = await db_mod.get_sent_relays_since(db_path, user_b, since)
    relay_texts = [
        r.get("edited_draft") or r["draft"]
        for r in relays_a + relays_b
    ]

    if not recent_shared and not relay_texts:
        logger.debug("No activity for couple %s — skipping digest", couple_id)
        return

    # Build prompt and call LLM
    system_prompt, user_content = build_digest_prompt(recent_shared, relay_texts)
    response = await llm.generate(user_content, system_prompt)
    summary = response.get("reply", "")

    if not summary:
        return

    # Send to both partners
    for uid in (user_a, user_b):
        user = await db_mod.get_user(db_path, uid)
        lang = (user or {}).get("lang") or "en"
        header = t(lang, "digest_header")
        await send_func(uid, f"{header}\n{summary}")

    # Record the digest timestamp
    now_iso = datetime.now(timezone.utc).isoformat()
    await db_mod.set_couple_config(db_path, couple_id, "last_digest", now_iso)

    # Periodic conflict resolution check (FEAT-002)
    resolved = await check_conflict_resolutions(db_path, couple_id)
    if resolved:
        logger.info(
            "Auto-resolved %d conflict(s) for couple %s during digest",
            resolved, couple_id,
        )

    logger.info("Daily digest sent for couple %s", couple_id)
