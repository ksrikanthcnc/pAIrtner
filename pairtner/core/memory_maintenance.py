"""Memory maintenance — background loop for aging, outdating, consolidation.

Runs hourly (configurable). Lightweight — skips if nothing to do.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from pairtner.core.memory_api import MemoryAPI
from pairtner.storage.helpers import _now_iso, _fetch_all, _fetch_one

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
            v2 = await _has_v2_schema(db_path)
            for uid in user_ids:
                try:
                    if v2:
                        await _run_v2_maintenance(db_path, llm, uid)
                    else:
                        await _maybe_consolidate(db_path, llm, uid)
                except Exception:
                    logger.exception("Maintenance failed for user %s", uid)

            # 3. Nudge expiry on v1 DBs that have nudge columns
            if not v2 and await _has_nudge_columns(db_path):
                from pairtner.features.nudge_engine import expire_stale_nudges

                await expire_stale_nudges(db_path, _ttl_config())

        except Exception:
            logger.exception("Memory maintenance loop error")


SETTINGS_PATH = Path(__file__).resolve().parents[2] / "settings.yaml"
DEFAULT_MAX_ACTIVE_NUDGES = 5


async def _has_v2_schema(db_path: str) -> bool:
    row = await _fetch_one(
        db_path,
        "SELECT name FROM sqlite_master WHERE type='table' AND name='relationships'",
        (),
    )
    return row is not None


async def _has_nudge_columns(db_path: str) -> bool:
    rows = await _fetch_all(db_path, "PRAGMA table_info(facts)", ())
    return any(r["name"] == "nudge_status" for r in rows)


def _ttl_config() -> dict[str, int]:
    from pairtner.features.nudge_engine import load_ttl_config

    return load_ttl_config(SETTINGS_PATH)


def _max_active_nudges() -> int:
    if SETTINGS_PATH.exists():
        import yaml

        data = yaml.safe_load(SETTINGS_PATH.read_text()) or {}
        val = (data.get("nudges") or {}).get("max_active")
        if val is not None:
            return int(val)
    return DEFAULT_MAX_ACTIVE_NUDGES


async def _run_v2_maintenance(db_path: str, llm: Any, user_id: int) -> None:
    """v2 maintenance: periodic + couple summaries, nudge cleanup, orphan rescue."""
    import aiosqlite

    from pairtner.core.summary import (
        ACTIVE_INTERVALS,
        create_couple_summary,
        run_periodic_summaries,
    )
    from pairtner.features.nudge_engine import enforce_max_active, expire_stale_nudges

    rels = await _fetch_all(
        db_path,
        "SELECT id FROM relationships WHERE status = 'active' "
        "AND (user_a_id = ? OR user_b_id = ?)",
        (user_id, user_id),
    )
    rel_ids = [r["id"] for r in rels]

    for rid in rel_ids:
        try:
            created = await run_periodic_summaries(db_path, llm, user_id, rid)
            if created:
                logger.info("v2 summaries: user %s rel %s created %s", user_id, rid, created)
        except Exception:
            # LLM failure (quota, rate limit, network) -- facts stay as working.
            # They'll be picked up on the next maintenance cycle. Never lost.
            logger.warning(
                "v2 summaries failed for user %s rel %s -- will retry next cycle",
                user_id, rid, exc_info=True,
            )

        # Couple summaries where both partners have a summary for the same period
        try:
            marks = ",".join("?" for _ in ACTIVE_INTERVALS)
            pairs = await _fetch_all(
                db_path,
                f"SELECT kind, json_extract(metadata,'$.period') AS period FROM facts "
                f"WHERE relationship_id = ? AND kind IN ({marks}) AND owner_id IS NOT NULL "
                f"AND archived_at IS NULL AND json_extract(metadata,'$.period') IS NOT NULL "
                f"GROUP BY kind, period HAVING COUNT(DISTINCT owner_id) >= 2",
                (rid, *ACTIVE_INTERVALS),
            )
            for p in pairs:
                await create_couple_summary(db_path, llm, rid, p["kind"], p["period"])
        except Exception:
            logger.warning("Couple summaries failed for rel %s", rid, exc_info=True)

    # Nudge cleanup (doesn't need LLM, always safe)
    await expire_stale_nudges(db_path, _ttl_config())
    max_active = _max_active_nudges()
    for rid in rel_ids:
        await enforce_max_active(db_path, rid, max_active)

    # Check for orphaned working facts older than 30 days (safety net).
    # These should have been summarized but weren't (e.g. repeated LLM failures).
    orphan_count = 0
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM facts WHERE owner_id = ? AND kind = 'working' "
            "AND archived_at IS NULL AND status = 'active' "
            "AND created_at < datetime('now', '-30 days')",
            (user_id,),
        ) as cur:
            orphan_count = (await cur.fetchone())[0]
        await db.execute(
            "INSERT OR REPLACE INTO user_meta (user_id, key, value, updated_at) "
            "VALUES (?, 'last_v2_maintenance', ?, CURRENT_TIMESTAMP)",
            (user_id, _now_iso()),
        )
        if orphan_count > 0:
            await db.execute(
                "INSERT OR REPLACE INTO user_meta (user_id, key, value, updated_at) "
                "VALUES (?, 'orphan_facts_count', ?, CURRENT_TIMESTAMP)",
                (user_id, str(orphan_count)),
            )
            logger.warning(
                "user %s has %d working facts older than 30 days (unsummarized orphans)",
                user_id, orphan_count,
            )
        await db.commit()


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
