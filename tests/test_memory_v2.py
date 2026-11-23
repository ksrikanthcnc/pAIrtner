"""Tests for the v2 memory engine (visibility, nudges, settings, core strictness)."""

from __future__ import annotations

import aiosqlite
import pytest
import pytest_asyncio

from pairtner import db as db_mod
from pairtner.core.memory import (
    ALLOWED_SETTINGS,
    MemoryOp,
    SettingsOp,
    apply_memory_ops,
    parse_memory_ops,
)
from pairtner.storage.schema_v2 import SCHEMA_V2_SQL, make_relationship_id

ALICE, BOB = 1, 2
REL = make_relationship_id(ALICE, BOB)


@pytest_asyncio.fixture
async def v2_db(tmp_path) -> str:
    path = str(tmp_path / "v2.db")
    async with aiosqlite.connect(path) as db:
        await db.executescript(SCHEMA_V2_SQL)
        await db.execute("INSERT INTO users (tg_id, name) VALUES (?, 'Alice'), (?, 'Bob')", (ALICE, BOB))
        await db.execute(
            "INSERT INTO relationships (id, user_a_id, user_b_id) VALUES (?, ?, ?)", (REL, ALICE, BOB)
        )
        await db.commit()
    return path


async def _fact(path: str, fact_id: int) -> dict:
    rows = await db_mod._fetch_all(path, "SELECT * FROM facts WHERE id = ?", (fact_id,))
    return rows[0]


async def _apply(path, ops, **kw):
    return await apply_memory_ops(path, ALICE, ops, "hello", existing_facts=[], **kw)


@pytest.mark.asyncio
async def test_visibility_stored(v2_db):
    ops = parse_memory_ops(
        [
            {"op": "add", "text": "Secret worry about money", "visibility": "private"},
            {"op": "add", "text": "Loves surprise dinners", "visibility": "shared"},
            {"op": "add", "text": "Had a long day", "visibility": "bogus"},
        ],
        set(), v2=True,
    )
    res = await _apply(v2_db, ops)
    vis = [(await _fact(v2_db, o.memory_id))["visibility"] for o in res.outcomes]
    assert vis == ["private", "shared", "shared"]


@pytest.mark.asyncio
async def test_nudge_fields_stored(v2_db):
    ops = parse_memory_ops(
        [{
            "op": "add", "text": "Feels ignored lately",
            "nudge_type": "attention", "nudge_target": "partner",
            "nudge_suggestion": "Offer extra attention and presence.",
        }],
        set(), v2=True, relationship_id=REL,
    )
    res = await _apply(v2_db, ops)
    row = await _fact(v2_db, res.outcomes[0].memory_id)
    assert row["nudge_type"] == "attention"
    assert row["nudge_suggestion"] == "Offer extra attention and presence."
    assert row["nudge_target_id"] == BOB
    assert row["nudge_status"] == "pending"
    assert row["nudge_cooldown_h"] == 24
    assert row["relationship_id"] == REL


@pytest.mark.asyncio
async def test_core_strictness(v2_db):
    ops = parse_memory_ops(
        [
            {"op": "add", "kind": "core", "text": "Feels stressed about the Friday deadline"},
            {"op": "add", "kind": "core", "text": "Birthday is March 15, 1992"},
        ],
        set(), v2=True,
    )
    res = await _apply(v2_db, ops)
    assert res.outcomes[0].outcome == "downgraded"
    assert (await _fact(v2_db, res.outcomes[0].memory_id))["kind"] == "working"
    assert res.outcomes[1].outcome == "applied"
    assert (await _fact(v2_db, res.outcomes[1].memory_id))["kind"] == "core"


@pytest.mark.asyncio
async def test_settings_ops_applied(v2_db):
    res = await _apply(v2_db, [], settings_ops=[
        SettingsOp("camouflage_frequency", "low"),
        SettingsOp("nudge_enabled", "false"),
    ])
    assert [(s.key, s.value) for s in res.settings_ops] == [
        ("camouflage_frequency", "low"), ("nudge_enabled", "false"),
    ]
    rows = await db_mod._fetch_all(
        v2_db, "SELECT key, value FROM user_meta WHERE user_id = ?", (ALICE,)
    )
    assert {r["key"]: r["value"] for r in rows} == {
        "setting:camouflage_frequency": "low", "setting:nudge_enabled": "false",
    }


@pytest.mark.asyncio
async def test_settings_ops_rejected(v2_db):
    assert "admin_mode" not in ALLOWED_SETTINGS
    res = await _apply(v2_db, [], settings_ops=[
        SettingsOp("admin_mode", "true"),
        SettingsOp("voice", "pirate"),
    ])
    assert res.settings_ops == []
    assert len(res.rejected_settings) == 2
    rows = await db_mod._fetch_all(v2_db, "SELECT * FROM user_meta", ())
    assert rows == []


@pytest.mark.asyncio
async def test_cancel_nudge(v2_db):
    # Alice's fact nudges Bob; Bob addresses it, so Bob's cancel applies.
    fid = await db_mod.add_fact(
        v2_db, ALICE, "Wants more attention", "shared",
        nudge_type="attention", nudge_suggestion="Be attentive.",
        nudge_target_id=BOB, nudge_status="pending",
    )
    stranger = await apply_memory_ops(
        v2_db, 99, [MemoryOp(op="cancel_nudge", id=fid)], "x", existing_facts=[],
    )
    assert stranger.cancelled_nudges == []
    assert (await _fact(v2_db, fid))["nudge_status"] == "pending"

    ops = parse_memory_ops([{"op": "cancel_nudge", "id": fid, "why": "talked it through"}], set(), v2=True)
    res = await apply_memory_ops(v2_db, BOB, ops, "we talked", existing_facts=[])
    assert res.cancelled_nudges == [fid]
    row = await _fact(v2_db, fid)
    assert row["nudge_status"] == "cancelled"
    assert row["nudge_cancelled_at"]
    assert row["nudge_cancel_reason"] == "talked it through"


@pytest.mark.asyncio
async def test_msg_source_stored(v2_db):
    ops = parse_memory_ops(
        [
            {"op": "add", "text": "Bot noted: prefers tea", "msg_source": "bot"},
            {"op": "add", "text": "Likes hiking trips"},
        ],
        set(), v2=True,
    )
    res = await _apply(v2_db, ops)
    assert (await _fact(v2_db, res.outcomes[0].memory_id))["msg_source"] == "bot"
    assert (await _fact(v2_db, res.outcomes[1].memory_id))["msg_source"] == "user"
