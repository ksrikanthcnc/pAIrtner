"""Backup loop tests."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from couples_bot.backup import _list_backup_files, _rotate, _run_backup


@pytest.mark.asyncio
async def test_backup_creates_file(tmp_path):
    """backup creates a dated copy of the database."""
    db_path = str(tmp_path / "test.db")
    backup_dir = str(tmp_path / "backups")
    with open(db_path, "w") as f:
        f.write("test data")

    await _run_backup(db_path, backup_dir, daily_count=7, weekly_count=4)

    files = os.listdir(backup_dir)
    assert len(files) == 1
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert files[0] == f"couples_bot_{today}.db"

    # Verify content was copied
    with open(os.path.join(backup_dir, files[0])) as f:
        assert f.read() == "test data"


@pytest.mark.asyncio
async def test_backup_skips_if_exists(tmp_path):
    """backup skips if today's backup already exists."""
    db_path = str(tmp_path / "test.db")
    backup_dir = str(tmp_path / "backups")
    with open(db_path, "w") as f:
        f.write("original data")

    # First backup
    await _run_backup(db_path, backup_dir, daily_count=7, weekly_count=4)

    # Modify DB
    with open(db_path, "w") as f:
        f.write("modified data")

    # Second backup — should skip
    await _run_backup(db_path, backup_dir, daily_count=7, weekly_count=4)

    files = os.listdir(backup_dir)
    assert len(files) == 1

    # Content should be the original, not modified
    with open(os.path.join(backup_dir, files[0])) as f:
        assert f.read() == "original data"


@pytest.mark.asyncio
async def test_backup_creates_directory(tmp_path):
    """backup creates the backup directory if it doesn't exist."""
    db_path = str(tmp_path / "test.db")
    backup_dir = str(tmp_path / "nested" / "backups")
    with open(db_path, "w") as f:
        f.write("data")

    await _run_backup(db_path, backup_dir, daily_count=7, weekly_count=4)

    assert os.path.isdir(backup_dir)
    assert len(os.listdir(backup_dir)) == 1


@pytest.mark.asyncio
async def test_backup_skips_missing_db(tmp_path):
    """backup skips when the DB file does not exist."""
    db_path = str(tmp_path / "nonexistent.db")
    backup_dir = str(tmp_path / "backups")

    await _run_backup(db_path, backup_dir, daily_count=7, weekly_count=4)

    assert not os.path.exists(backup_dir)


def test_backup_rotation_daily(tmp_path):
    """rotation keeps only N daily backups."""
    backup_dir = str(tmp_path / "backups")
    os.makedirs(backup_dir)

    # Create 10 weekday (non-Sunday) backups: Mon-Sat pattern
    # 2025-01-06 is Monday, 2025-01-07 Tuesday, etc.
    dates = [
        "2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09", "2025-01-10",
        "2025-01-13", "2025-01-14", "2025-01-15", "2025-01-16", "2025-01-17",
    ]
    for d in dates:
        with open(os.path.join(backup_dir, f"couples_bot_{d}.db"), "w") as f:
            f.write("data")

    _rotate(backup_dir, daily_count=3, weekly_count=4)

    remaining = _list_backup_files(backup_dir)
    assert len(remaining) == 3
    # Should keep the 3 most recent
    remaining_dates = [dt.strftime("%Y-%m-%d") for _, dt in remaining]
    assert "2025-01-17" in remaining_dates
    assert "2025-01-16" in remaining_dates
    assert "2025-01-15" in remaining_dates


def test_backup_rotation_weekly(tmp_path):
    """rotation keeps only M weekly (Sunday) backups."""
    backup_dir = str(tmp_path / "backups")
    os.makedirs(backup_dir)

    # Sundays: 2025-01-05, 2025-01-12, 2025-01-19, 2025-01-26, 2025-02-02
    sundays = ["2025-01-05", "2025-01-12", "2025-01-19", "2025-01-26", "2025-02-02"]
    for d in sundays:
        with open(os.path.join(backup_dir, f"couples_bot_{d}.db"), "w") as f:
            f.write("data")

    _rotate(backup_dir, daily_count=7, weekly_count=2)

    remaining = _list_backup_files(backup_dir)
    assert len(remaining) == 2
    remaining_dates = [dt.strftime("%Y-%m-%d") for _, dt in remaining]
    assert "2025-02-02" in remaining_dates
    assert "2025-01-26" in remaining_dates


def test_list_backup_files_sorted(tmp_path):
    """_list_backup_files returns sorted results, ignoring non-backup files."""
    backup_dir = str(tmp_path)

    # Create some backup files out of order
    for name in ["couples_bot_2025-01-15.db", "couples_bot_2025-01-10.db",
                  "couples_bot_2025-01-20.db", "other_file.txt",
                  "couples_bot_bad-date.db"]:
        with open(os.path.join(backup_dir, name), "w") as f:
            f.write("data")

    files = _list_backup_files(backup_dir)
    assert len(files) == 3  # Only valid backup files
    dates = [dt.strftime("%Y-%m-%d") for _, dt in files]
    assert dates == ["2025-01-10", "2025-01-15", "2025-01-20"]
