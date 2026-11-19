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
    """Run consolidation if enough time has passed since the last run."""
    from pairtner.storage.helpers import _fetch_one

    row = await _fetch_one(
        db_path,
        "SELECT value FROM user_meta WHERE user_id = ? AND key = 'last_consolidation'",
        (user_id,),
    )

    if row and row.get("value"):
        try:
            last = datetime.fromisoformat(row["value"])
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            days_since = (datetime.now(timezone.utc) - last).days
            if days_since < 7:
                return  # Too soon
        except (ValueError, TypeError):
            pass  # Malformed timestamp — run anyway

    try:
        from pairtner.core.consolidation import run_consolidation

        episodes = await run_consolidation(db_path, llm, user_id)
        if episodes:
            logger.info(
                "Consolidation: user %s — created %d episodes",
                user_id,
                len(episodes),
            )

        # Record the run
        import aiosqlite

        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO user_meta (user_id, key, value, updated_at) "
                "VALUES (?, 'last_consolidation', ?, CURRENT_TIMESTAMP)",
                (user_id, _now_iso()),
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
                return  # Too soon
        except (ValueError, TypeError):
            pass

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
