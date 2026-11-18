"""Automated daily backup of the SQLite database with rotation."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def backup_loop(
    db_path: str,
    backup_dir: str,
    daily_count: int = 7,
    weekly_count: int = 4,
    interval_seconds: float = 3600.0,
) -> None:
    """Periodically back up the database file with daily and weekly rotation.

    Runs forever as a background ``asyncio.Task``.

    - Copies *db_path* to ``<backup_dir>/pairtner_YYYY-MM-DD.db``.
    - Skips if today's backup already exists.
    - Keeps the most recent *daily_count* daily backups.
    - Keeps the most recent *weekly_count* Sunday backups.
    """
    while True:
        try:
            await _run_backup(db_path, backup_dir, daily_count, weekly_count)
        except Exception:
            logger.exception("Error in backup_loop")
        await asyncio.sleep(interval_seconds)


async def _run_backup(
    db_path: str,
    backup_dir: str,
    daily_count: int,
    weekly_count: int,
) -> None:
    """Execute a single backup cycle (create + rotate)."""
    if not os.path.isfile(db_path):
        logger.warning("DB file %r does not exist — skipping backup", db_path)
        return

    os.makedirs(backup_dir, exist_ok=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dest = os.path.join(backup_dir, f"pairtner_{today}.db")

    if os.path.isfile(dest):
        logger.debug("Backup for %s already exists — skipping", today)
        return

    # Run the blocking copy in a thread to avoid blocking the event loop
    await asyncio.to_thread(shutil.copy2, db_path, dest)
    logger.info("Backup created: %s", dest)

    # Rotate old backups
    await asyncio.to_thread(_rotate, backup_dir, daily_count, weekly_count)


def _rotate(backup_dir: str, daily_count: int, weekly_count: int) -> None:
    """Delete old backups beyond the configured retention limits."""
    all_files = _list_backup_files(backup_dir)
    if not all_files:
        return

    # Split into weekly (Sunday) and daily (everything else)
    weekly: list[tuple[str, datetime]] = []
    daily: list[tuple[str, datetime]] = []

    for fname, dt in all_files:
        if dt.weekday() == 6:  # Sunday
            weekly.append((fname, dt))
        else:
            daily.append((fname, dt))

    _prune(backup_dir, daily, daily_count)
    _prune(backup_dir, weekly, weekly_count)


def _list_backup_files(backup_dir: str) -> list[tuple[str, datetime]]:
    """Return ``(filename, date)`` pairs for all backup files, sorted by date."""
    result: list[tuple[str, datetime]] = []
    for fname in os.listdir(backup_dir):
        if fname.startswith("pairtner_") and fname.endswith(".db"):
            date_str = fname[len("pairtner_"):-len(".db")]
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
                result.append((fname, dt))
            except ValueError:
                continue
    result.sort(key=lambda x: x[1])
    return result


def _prune(
    backup_dir: str,
    files: list[tuple[str, datetime]],
    keep: int,
) -> None:
    """Delete the oldest files beyond *keep* count."""
    if len(files) <= keep:
        return
    # Sort oldest first, remove the excess
    files.sort(key=lambda x: x[1])
    to_remove = files[: len(files) - keep]
    for fname, _ in to_remove:
        path = os.path.join(backup_dir, fname)
        try:
            os.remove(path)
            logger.info("Rotated old backup: %s", path)
        except OSError:
            logger.warning("Failed to remove old backup: %s", path)
