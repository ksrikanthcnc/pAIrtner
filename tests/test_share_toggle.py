"""Tests for the fact share toggle (fv:<id>) and /whatdoyouknow buttons."""
from __future__ import annotations

import aiosqlite
import pytest

from couples_bot.db import (
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


async def test_shared_fact_reaches_partner_prompt_and_mediate_then_disappears(
    service, mock_llm, test_db, paired_users
):
    a, b = paired_users
    fid = await add_fact(test_db, a, SECRET, "private")
    await service.handle_message(b, "Bob", "hello there")
    assert SECRET not in mock_llm.calls[-1]["user_content"]

    await service.handle_callback(a, f"fv:{fid}")
    await service.handle_message(b, "Bob", "hello again")
    assert SECRET in mock_llm.calls[-1]["user_content"]
    await service.handle_mediate(b)
    assert SECRET in mock_llm.calls[-1]["user_content"]

    await service.handle_callback(a, f"fv:{fid}")
    await service.handle_message(b, "Bob", "and once more")
    assert SECRET not in mock_llm.calls[-1]["user_content"]
    n = len(mock_llm.calls)
    await service.handle_mediate(b)
    # Only the fixture's other shared fact may remain; the toggled one is gone.
    assert len(mock_llm.calls) == n + 1
    assert SECRET not in mock_llm.calls[-1]["user_content"]


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


async def test_buttons_on_whatdoyouknow(service, test_db, paired_users):
    a, _ = paired_users
    resp = await service.handle_whatdoyouknow(a)
    buttons = [b for row in resp[0].buttons for b in row]
    ids = {f["id"] for f in await get_active_facts(test_db, a)}
    assert {b.callback_data for b in buttons} == {f"fv:{i}" for i in ids}
    assert all(len(row) <= 2 for row in resp[0].buttons)
    labels = {b.callback_data: b.text for b in buttons}
    for f in await get_active_facts(test_db, a):
        assert f"#{f['id']}" in labels[f"fv:{f['id']}"]
        if f["visibility"] == "shared":
            assert "private" in labels[f"fv:{f['id']}"].lower()
        else:
            assert "share" in labels[f"fv:{f['id']}"].lower()


async def test_buttons_capped_at_30_most_recent(service, test_db, paired_users):
    a, _ = paired_users
    existing = len(await get_active_facts(test_db, a))
    for i in range(40):
        await add_fact(test_db, a, f"bulk fact number {i} xyz", "private")
    resp = await service.handle_whatdoyouknow(a)
    buttons = [b for row in resp[0].buttons for b in row]
    assert len(buttons) == 30
    newest = sorted((f["id"] for f in await get_active_facts(test_db, a)), reverse=True)[:30]
    assert {b.callback_data for b in buttons} == {f"fv:{i}" for i in newest}
    assert f"{existing + 40 - 30} more" in resp[0].text
