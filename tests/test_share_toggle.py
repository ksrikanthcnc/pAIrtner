"""Tests for the fact share toggle (fv:<id>) and /whatdoyouknow buttons."""
from __future__ import annotations

import aiosqlite
import pytest

from pairtner.db import (
    add_fact,
    archive_fact,
    get_active_facts,
    get_facts,
    set_fact_visibility,
)

SECRET = "SHARETOGGLE-SENTINEL-3307"


async def _fact(db_path, fact_id):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)) as cur:
            return dict(await cur.fetchone())


async def test_owner_toggles_both_ways(service, test_db, paired_users):
    a, _ = paired_users
    fid = await add_fact(test_db, a, SECRET, "private")
    resp = await service.handle_callback(a, f"fv:{fid}")
    assert f"#{fid}" in resp[0].text and "shared" in resp[0].text.lower()
    row = await _fact(test_db, fid)
    assert row["visibility"] == "shared" and row["shared_at"]
    resp = await service.handle_callback(a, f"fv:{fid}")
    assert "private" in resp[0].text.lower()
    row = await _fact(test_db, fid)
    assert row["visibility"] == "private" and row["shared_at"] is None


async def test_partner_cannot_toggle(service, test_db, paired_users):
    a, b = paired_users
    fid = await add_fact(test_db, a, SECRET, "private")
    resp = await service.handle_callback(b, f"fv:{fid}")
    assert resp[0].text == (await service.handle_callback(b, "fv:999999"))[0].text
    assert (await _fact(test_db, fid))["visibility"] == "private"
    assert await set_fact_visibility(test_db, fid, b, "shared") is False
    assert (await _fact(test_db, fid))["visibility"] == "private"


async def test_unknown_archived_and_bad_ids(service, test_db, paired_users):
    a, _ = paired_users
    unknown = await service.handle_callback(a, "fv:424242")
    junk = await service.handle_callback(a, "fv:abc")
    fid = await add_fact(test_db, a, SECRET, "private")
    await archive_fact(test_db, fid)
    archived = await service.handle_callback(a, f"fv:{fid}")
    assert unknown[0].text == junk[0].text == archived[0].text
    assert (await _fact(test_db, fid))["visibility"] == "private"
    assert await set_fact_visibility(test_db, fid, a, "shared") is False


async def test_invalid_visibility_value_rejected(test_db, paired_users):
    a, _ = paired_users
    fid = await add_fact(test_db, a, SECRET, "private")
    with pytest.raises(ValueError):
        await set_fact_visibility(test_db, fid, a, "public")




async def test_llm_labelled_shared_fact_stays_private(service, mock_llm, test_db, paired_users):
    a, _ = paired_users
    mock_llm.responses.append({
        "reply": "ok",
        "new_facts": [{"text": "MODELSHARED-SENTINEL-1", "visibility": "shared"}],
        "relay_draft": None,
    })
    await service.handle_message(a, "Alice", "I like long train journeys a lot")
    stored = [f for f in await get_facts(test_db, a) if f["text"] == "MODELSHARED-SENTINEL-1"]
    assert stored and all(f["visibility"] == "private" for f in stored)
    assert all(f["shared_at"] is None for f in stored)




