"""Tests for /debug (no args) and /warnings."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from pairtner.handlers import BotService


def _ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _insert(db_path, owner, text, *, kind="working", status="active",
            created=None, citations=0, source_ids=None, metadata=None):
    con = sqlite3.connect(db_path)
    cur = con.execute(
        "INSERT INTO facts (owner_id, text, kind, status, created_at, citation_count, "
        "source_ids, metadata) VALUES (?,?,?,?,?,?,?,?)",
        (owner, text, kind, status, created or _ago(0), citations,
         json.dumps(source_ids) if source_ids is not None else None,
         json.dumps(metadata) if metadata is not None else None),
    )
    con.commit()
    fid = cur.lastrowid
    con.close()
    return fid


@pytest_asyncio.fixture
async def user(service: BotService, test_db) -> int:
    await service.handle_start(1, "Alice")
    return 1


@pytest.mark.asyncio
async def test_debug_no_args_reports_memory_state(service, test_db, user):
    _insert(test_db, user, "likes tea", kind="core", created=_ago(100))
    _insert(test_db, user, "went hiking", kind="working")
    _insert(test_db, user, "old thing", kind="working", status="outdated")
    _insert(test_db, user, "week summary", kind="weekly", source_ids=[1])

    text = (await service.handle_debug(user, ""))[0].text
    assert "Memory debug" in text
    assert "core: 1" in text and "working: 2" in text
    assert "outdated: 1" in text and "active: 2" in text
    assert "FTS5 index: present, in sync" in text
    assert "weekly: 1" in text
    assert "Pack: default v" in text
    assert "LLM: " in text and "test-default" in text
    assert _ago(100)[:10] in text


@pytest.mark.asyncio
async def test_debug_with_args_still_runs_dryrun(service, test_db, user):
    text = (await service.handle_debug(user, "hello there"))[0].text
    assert "Memory debug" not in text


@pytest.mark.asyncio
async def test_dispatch_routes_debug_and_warnings(service, test_db, user):
    assert "Memory debug" in (await service.dispatch(user, "Alice", "/debug"))[0].text
    assert (await service.dispatch(user, "Alice", "/warnings"))[0].text


@pytest.mark.asyncio
async def test_warnings_clean(service, test_db, user):
    text = (await service.handle_warnings(user))[0].text
    assert "No memory quality warnings" in text


@pytest.mark.asyncio
async def test_warnings_detects_all_issue_types(service, test_db, user):
    _insert(test_db, user, "Bob loves cooking dinner together", kind="working")
    _insert(test_db, user, "Bob hates cooking dinner together", kind="working")
    _insert(test_db, user, "We should plan a weekend trip to the mountains", kind="working")
    _insert(test_db, user, "We should plan a weekend trip to the mountains!", kind="working")
    stale = _insert(test_db, user, "ancient unrelated trivia", created=_ago(90))
    _insert(test_db, user, "cited old thing", created=_ago(90), citations=3)
    orphan = _insert(test_db, user, "orphan weekly", kind="weekly", source_ids=[9999])

    text = (await service.handle_warnings(user))[0].text
    assert "Contradicting facts: 1" in text
    assert "Near-duplicate facts: 1 pair(s)" in text
    assert f"#{stale}" in text and "Stale working facts" in text
    assert "cited old thing" not in text
    assert f"#{orphan}" in text
    month = _ago(90)[:7]
    assert f"{month} (" in text  # month with facts, no summary


@pytest.mark.asyncio
async def test_monthly_summary_clears_missing_summary_warning(service, test_db, user):
    month = _ago(90)[:7]
    f = _insert(test_db, user, "something from long ago", created=_ago(90), citations=1)
    _insert(test_db, user, "month sum", kind="monthly", source_ids=[f],
            metadata={"period": month})
    text = (await service.handle_warnings(user))[0].text
    assert "no summary" not in text
