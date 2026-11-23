"""Proactive engagement — nudges, appreciation prompts, and ratio tracking.

Background task that periodically sends context-aware conversation
starters and appreciation prompts based on the Gottman 5:1 ratio.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from pairtner import db as db_mod
from pairtner import pack
from pairtner.i18n import t
from pairtner.llm import LLMClient, get_reply_text
from pairtner.push import ensure_push_table, send_push
from pairtner.core.prompts import load_task_prompt

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


def _in_quiet_window(now: datetime, quiet_start: str, quiet_end: str) -> bool:
    """True if *now* falls inside the user's quiet window [start, end).

    Unlike :func:`_is_quiet_hours` (legacy config semantics: start/end is the
    ACTIVE window), here start/end is the quiet window itself and may wrap
    midnight (22:00 -> 08:00).
    """
    try:
        sh, sm = (int(p) for p in quiet_start.split(":"))
        eh, em = (int(p) for p in quiet_end.split(":"))
    except (ValueError, AttributeError):
        return False
    start, end = sh * 60 + sm, eh * 60 + em
    cur = now.hour * 60 + now.minute
    if start == end:
        return False
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end


async def _user_nudge_setting(db_path: str, uid: int, key: str) -> str | None:
    from pairtner.core.memory import SETTINGS_META_PREFIX
    from pairtner.storage import settings as settings_mod

    return await settings_mod.get_user_preference(
        db_path, uid, SETTINGS_META_PREFIX + key, default=None,
    )


async def _user_is_quiet(db_path: str, uid: int, now: datetime, config: Any) -> bool:
    """Per-user quiet hours; user settings first, config defaults as fallback."""
    start = await _user_nudge_setting(db_path, uid, "nudge_quiet_start")
    end = await _user_nudge_setting(db_path, uid, "nudge_quiet_end")
    if start and end:
        return _in_quiet_window(now, start, end)
    return _is_quiet_hours(
        now,
        getattr(config, "nudges_quiet_start", "09:00"),
        getattr(config, "nudges_quiet_end", "21:00"),
    )


_FREQUENCY_HOURS = {"daily": 24.0, "weekly": 24.0 * 7, "rare": 24.0 * 21}


async def generate_nudge(
    db_path: str,
    llm: LLMClient,
    user_id: int,
    partner_id: int,
    lang: str = "en",
) -> str:
    """Generate a context-aware nudge for *user_id*.

    Uses recent shared facts of any sentiment; falls back to generic starters.
    """
    facts = await db_mod.get_recent_shared_facts(
        db_path, user_id, partner_id, limit=5,
    )

    if not facts:
        starters = _GENERIC_STARTERS_TE if lang == "te" else _GENERIC_STARTERS_EN
        return random.choice(starters)

    sample = facts[:5]
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
    reply = get_reply_text(result)
    # The LLM may return the nudge text in various formats due to JSON mode.
    # Strip any JSON artifacts.
    if reply and reply.strip().startswith("["):
        try:
            arr = json.loads(reply)
            if isinstance(arr, list) and arr:
                reply = str(arr[0])
        except (ValueError, TypeError):
            pass
    reply = reply.strip().strip('"').strip("'")
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
    reply = get_reply_text(result)
    if not reply:
        reply = t(lang, "nudge_appreciation", partner_name=partner_name)
    return reply


# Per-user nudge state: {uid: {"last": datetime (UTC), "ignored": int}}.
# In-memory; the DB (facts.nudge_delivered_at) seeds "last" after a restart.
_nudge_state: dict[int, dict[str, Any]] = {}
_MAX_BACKOFF_MULT = 8
_COOLDOWN_SLACK_S = 60  # tolerate poll-loop jitter


def _parse_ts(ts: Any) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _last_nudge_at(db_path: str, uid: int) -> datetime | None:
    """Most recent nudge delivery time for *uid* (memory, then DB)."""
    mem = _nudge_state.get(uid, {}).get("last")
    if mem:
        return mem
    import aiosqlite

    try:
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute(
                "SELECT MAX(nudge_delivered_at) FROM facts "
                "WHERE nudge_target_id = ?", (uid,),
            )
            row = await cur.fetchone()
    except aiosqlite.OperationalError:
        return None  # v1 schema without nudge columns
    return _parse_ts(row[0]) if row else None


async def _user_replied_since(db_path: str, uid: int, since: datetime) -> bool:
    import aiosqlite

    try:
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute(
                "SELECT 1 FROM messages WHERE user_id = ? AND role = 'user' "
                "AND created_at > ? LIMIT 1",
                (uid, since.strftime("%Y-%m-%d %H:%M:%S")),
            )
            return await cur.fetchone() is not None
    except aiosqlite.OperationalError:
        return False


async def _may_nudge(
    db_path: str, uid: int, config: Any, now: datetime | None = None,
) -> bool:
    """Cooldown + backoff gate. Updates the ignored counter as a side effect.

    Solo users never receive partner nudges or appreciation prompts.
    """
    if await db_mod.get_user_mode(db_path, uid) == "solo":
        return False
    if now is not None and await _user_is_quiet(db_path, uid, now, config):
        return False
    interval_h = float(getattr(config, "nudges_interval_hours", 24) or 0)
    freq = await _user_nudge_setting(db_path, uid, "nudge_frequency")
    if freq == "off":
        return False
    if freq == "weekly" and now is not None:
        day = str(getattr(config, "checkin_day", "sunday") or "sunday").lower()
        if now.strftime("%A").lower() != day:
            return False
    if now is not None and (
        await _user_nudge_setting(db_path, uid, "nudge_skip_weekends") == "true"
    ) and now.weekday() >= 5:
        return False
    if freq in _FREQUENCY_HOURS:
        interval_h = _FREQUENCY_HOURS[freq]
    last = await _last_nudge_at(db_path, uid)
    if last is None:
        return True
    state = _nudge_state.setdefault(uid, {"last": last, "ignored": 0})
    state.setdefault("last", last)
    ignored = int(state.get("ignored", 0))
    mult = min(2 ** ignored, _MAX_BACKOFF_MULT)
    wait_s = interval_h * 3600 * mult - _COOLDOWN_SLACK_S
    return (datetime.now(timezone.utc) - last).total_seconds() >= wait_s


_CONFLICT_COOLDOWN_S = 3600  # 60 min after a conflict-tagged fact


async def _recent_conflict(db_path: str, uid: int, partner_id: int) -> bool:
    """True if a conflict-tagged fact was created in the last 60 min."""
    import aiosqlite

    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=_CONFLICT_COOLDOWN_S)
              ).strftime("%Y-%m-%d %H:%M:%S")
    try:
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute(
                "SELECT 1 FROM facts WHERE (owner_id = ? OR owner_id = ?) "
                "AND tag LIKE '%conflict%' AND created_at > ? LIMIT 1",
                (uid, partner_id, cutoff),
            )
            return await cur.fetchone() is not None
    except Exception:
        return False


async def _last_user_message_at(db_path: str, uid: int) -> datetime | None:
    """When the user last sent a message."""
    import aiosqlite

    try:
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute(
                "SELECT MAX(created_at) FROM messages "
                "WHERE user_id = ? AND role = 'user'", (uid,),
            )
            row = await cur.fetchone()
            return _parse_ts(row[0]) if row and row[0] else None
    except Exception:
        return None


async def _record_nudge(db_path: str, uid: int) -> None:
    """Record a delivery; bump ignored count if user never replied to the last."""
    now = datetime.now(timezone.utc)
    state = _nudge_state.setdefault(uid, {"last": None, "ignored": 0})
    prev = state.get("last")
    if prev is not None:
        if await _user_replied_since(db_path, uid, prev):
            state["ignored"] = 0
        else:
            state["ignored"] = int(state.get("ignored", 0)) + 1
    state["last"] = now
    # Persist to DB so restarts don't trigger bursts
    try:
        import aiosqlite

        async with aiosqlite.connect(db_path) as db:
            # Mark the most recent pending nudge for this target as delivered
            await db.execute(
                "UPDATE facts SET nudge_delivered_at = ?, nudge_status = 'delivered' "
                "WHERE id = ("
                "  SELECT id FROM facts "
                "  WHERE nudge_target_id = ? AND nudge_status = 'pending' "
                "  ORDER BY created_at DESC LIMIT 1"
                ")",
                (now.strftime("%Y-%m-%d %H:%M:%S"), uid),
            )
            await db.commit()
    except Exception:
        logger.debug("Could not persist nudge delivery time for %s", uid)


async def _deliver(
    db_path: str, send_func: SendFunc, uid: int, text: str, vapid_key: str,
) -> None:
    await send_func(uid, text)
    if not vapid_key:
        return
    try:
        await send_push(
            db_path, uid, "Pairtner", text, vapid_private_key=vapid_key,
        )
    except Exception:
        logger.exception("Push delivery failed for user %s", uid)


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
    try:
        await ensure_push_table(db_path)
    except Exception:
        logger.exception("Could not ensure push table")
    # Preload nudge state from DB to avoid burst on restart
    try:
        await _preload_nudge_state(db_path)
    except Exception:
        logger.exception("Could not preload nudge state")
    # Wait before first cycle to avoid burst on restart/redeploy
    await asyncio.sleep(min(poll_interval, 300))  # at least 5 min delay
    while True:
        try:
            await _nudge_cycle(db_path, llm, send_func, config)
        except Exception:
            logger.exception("Error in nudge_loop")
        await asyncio.sleep(poll_interval)


async def _preload_nudge_state(db_path: str) -> None:
    """Seed in-memory nudge state from DB so restarts don't trigger bursts."""
    import aiosqlite

    try:
        async with aiosqlite.connect(db_path) as db:
            rows = await db.execute_fetchall(
                "SELECT nudge_target_id, MAX(nudge_delivered_at) "
                "FROM facts WHERE nudge_delivered_at IS NOT NULL "
                "GROUP BY nudge_target_id",
            )
            for uid, ts in rows:
                if uid and ts:
                    dt = _parse_ts(ts)
                    if dt:
                        _nudge_state[uid] = {"last": dt, "ignored": 0}
                        logger.info("Preloaded nudge state for user %s: last=%s", uid, dt)
    except Exception:
        logger.warning("Could not preload nudge state (v1 schema?)")


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
    vapid_key = os.environ.get("VAPID_PRIVATE_KEY", "")

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
                    if not await _may_nudge(db_path, uid, config, now):
                        continue
                    user = await db_mod.get_user(db_path, uid)
                    lang = (user or {}).get("lang") or "en"
                    partner = await db_mod.get_user(db_path, pid)
                    pname = (partner or {}).get("name") or pack.current().s(
                        "partner_fallback",
                    )
                    prompt = await generate_appreciation_prompt(
                        db_path, llm, uid, pname, lang=lang,
                    )
                    await _deliver(
                        db_path, send_func, uid, f"💛 {prompt}", vapid_key,
                    )
                    await _record_nudge(db_path, uid)
                continue  # Skip regular nudge when appreciation is triggered

        # Regular nudge
        if nudges_enabled:
            for uid, pid in [(user_a, user_b), (user_b, user_a)]:
                user = await db_mod.get_user(db_path, uid)
                lang = (user or {}).get("lang") or "en"
                if (user or {}).get("paused"):
                    continue
                if not await _may_nudge(db_path, uid, config, now):
                    continue
                # Skip nudge if there's a recent conflict (60 min cooldown)
                if await _recent_conflict(db_path, uid, pid):
                    logger.debug("Skipping nudge for %s — recent conflict", uid)
                    continue
                nudge = await generate_nudge(
                    db_path, llm, uid, pid, lang=lang,
                )
                await _deliver(
                    db_path, send_func, uid, f"💬 {nudge}", vapid_key,
                )
                await _record_nudge(db_path, uid)

    logger.info("Nudge cycle completed for %d couples", len(couples))
