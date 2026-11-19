"""Memory maintenance — background loop for aging, outdating, consolidation.

Runs hourly (configurable). Lightweight — skips if nothing to do.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from pairtner.core.memory_api import MemoryAPI
from pairtner.storage.helpers import _now_iso, _fetch_all

logger = logging.getLogger(__name__)


async def memory_maintenance_loop(
    db_path: str,
    llm: Any,
    *,
    interval: int = 3600,
) -> None:
    """Background loop: run memory maintenance once per interval.

    - Ages expired/transient facts
    - Auto-marks stale working facts as outdated
    - Runs consolidation (weekly) for eligible users
    """
    api = MemoryAPI(db_path)
    logger.info("Memory maintenance loop started (interval=%ds)", interval)

    while True:
        await asyncio.sleep(interval)
        try:
            # 1. Run aging (all users)
            result = await api.run_aging()
            if result.marked_outdated > 0:
                logger.info(
                    "Memory aging: marked %d facts as outdated",
                    result.marked_outdated,
                )

            # 2. Consolidation (per-user, weekly)
            user_ids = await _get_active_user_ids(db_path)
            for uid in user_ids:
                await _maybe_consolidate(db_path, llm, uid)

        except Exception:
            logger.exception("Memory maintenance loop error")


async def _get_active_user_ids(db_path: str) -> list[int]:
    """Get user IDs with at least one active fact."""
    rows = await _fetch_all(
        db_path,
        "SELECT DISTINCT owner_id FROM facts WHERE archived_at IS NULL",
        (),
    )
    return [r["owner_id"] for r in rows]


async def _maybe_consolidate(
    db_path: str, llm: Any, user_id: int
) -> None:
    """Run weekly consolidation for each missed calendar week.

    Fixed-window strategy: if the bot was down for 3 weeks, run 3 separate
    weekly passes (week 1, week 2, week 3) — NOT one big pass.  Each pass
    processes only facts resolved within that 7-day window.
    """
    from pairtner.storage.helpers import _fetch_one
    from datetime import timedelta

    row = await _fetch_one(
        db_path,
        "SELECT value FROM user_meta WHERE user_id = ? AND key = 'last_consolidation'",
        (user_id,),
    )

    now = datetime.now(timezone.utc)

    if row and row.get("value"):
        try:
            last = datetime.fromisoformat(row["value"])
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            last = now - timedelta(days=7)  # Malformed — process last week
    else:
        last = now - timedelta(days=7)  # First run — process last week

    days_since = (now - last).days
    if days_since < 7:
        logger.debug(
            "Weekly consolidation: user %s — %dd since last run, skipping (next in %dd)",
            user_id, days_since, 7 - days_since,
        )
        return

    # How many weekly windows to process
    weeks_missed = days_since // 7
    logger.info(
        "Weekly consolidation: user %s — %dd since last run, processing %d weekly window(s)",
        user_id, days_since, weeks_missed,
    )

    try:
        from pairtner.core.consolidation import run_consolidation
        import aiosqlite

        total_episodes = 0
        for week_num in range(weeks_missed):
            # Each window: [last + week_num*7d, last + (week_num+1)*7d)
            window_start = last + timedelta(days=week_num * 7)
            window_end = last + timedelta(days=(week_num + 1) * 7)

            episodes = await run_consolidation(
                db_path, llm, user_id,
                window_start=window_start.isoformat(),
                window_end=window_end.isoformat(),
            )
            total_episodes += len(episodes) if episodes else 0

        if total_episodes:
            logger.info(
                "Consolidation: user %s — created %d episodes across %d weekly windows",
                user_id, total_episodes, weeks_missed,
            )

        # Record the run — advance to the last completed window boundary
        completed_up_to = last + timedelta(days=weeks_missed * 7)
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO user_meta (user_id, key, value, updated_at) "
                "VALUES (?, 'last_consolidation', ?, CURRENT_TIMESTAMP)",
                (user_id, completed_up_to.isoformat()),
            )
            await db.commit()

        # Run cascading consolidation monthly
        await _maybe_cascade(db_path, llm, user_id)

    except Exception:
        logger.exception("Consolidation failed for user %s", user_id)


async def _maybe_cascade(
    db_path: str, llm: Any, user_id: int
) -> None:
    """Run cascading consolidation (monthly/quarterly) if 30+ days since last."""
    from pairtner.storage.helpers import _fetch_one

    row = await _fetch_one(
        db_path,
        "SELECT value FROM user_meta WHERE user_id = ? AND key = 'last_cascade'",
        (user_id,),
    )

    if row and row.get("value"):
        try:
            last = datetime.fromisoformat(row["value"])
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            days_since = (datetime.now(timezone.utc) - last).days
            if days_since < 30:
                logger.debug(
                    "Monthly cascade: user %s — %dd since last run, skipping (next in %dd)",
                    user_id, days_since, 30 - days_since,
                )
                return  # Too soon
            logger.info(
                "Monthly cascade: user %s — %dd since last run, running now",
                user_id, days_since,
            )
        except (ValueError, TypeError):
            logger.warning(
                "Monthly cascade: user %s — malformed last_cascade timestamp, running anyway",
                user_id,
            )

    try:
        from pairtner.core.consolidation import run_cascading_consolidation

        result = await run_cascading_consolidation(db_path, llm, user_id)
        monthly = result.get("monthly", [])
        quarterly = result.get("quarterly", [])
        if monthly or quarterly:
            logger.info(
                "Cascading: user %s — %d monthly, %d quarterly episodes",
                user_id, len(monthly), len(quarterly),
            )

        import aiosqlite

        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO user_meta (user_id, key, value, updated_at) "
                "VALUES (?, 'last_cascade', ?, CURRENT_TIMESTAMP)",
                (user_id, _now_iso()),
            )
            await db.commit()
    except Exception:
        logger.exception("Cascading consolidation failed for user %s", user_id)
