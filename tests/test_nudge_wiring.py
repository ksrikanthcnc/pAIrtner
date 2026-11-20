"""Tests for nudge injection, delivery tracking, camouflage and settings wiring."""

from __future__ import annotations

import random

import aiosqlite
import pytest
import pytest_asyncio

from pairtner import db as db_mod
from pairtner.features.nudge_injector import (
    inject_nudge_context,
    mark_nudges_delivered,
    maybe_inject_camouflage,
)
from pairtner.features.settings_applier import apply_settings_ops
from pairtner.storage.schema_v2 import SCHEMA_V2_SQL, make_relationship_id

ALICE, BOB = 1, 2
REL = make_relationship_id(ALICE, BOB)
SECRET = "Alice secretly fears being abandoned"
SUGGESTION = "Offer extra reassurance this week."


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


async def _add_nudge(path: str) -> int:
    return await db_mod.add_fact(
        path, ALICE, SECRET, "nudge_eligible",
        nudge_type="attention", nudge_suggestion=SUGGESTION,
        nudge_target_id=BOB, nudge_status="pending", nudge_cooldown_h=0,
    )


async def _status(path: str, fid: int) -> str:
    rows = await db_mod._fetch_all(path, "SELECT nudge_status FROM facts WHERE id = ?", (fid,))
    return rows[0]["nudge_status"]


@pytest.mark.asyncio
async def test_nudge_injected_into_prompt(v2_db):
    fid = await _add_nudge(v2_db)
    prompt, ids = await inject_nudge_context(v2_db, BOB, REL, "BASE")
    assert prompt.startswith("BASE")
    assert SUGGESTION in prompt
    assert ids == [fid]


@pytest.mark.asyncio
async def test_no_nudges_leaves_prompt_unchanged(v2_db):
    prompt, ids = await inject_nudge_context(v2_db, BOB, REL, "BASE")
    assert (prompt, ids) == ("BASE", [])


@pytest.mark.asyncio
async def test_injection_never_leaks_source_fact(v2_db):
    await _add_nudge(v2_db)
    prompt, _ = await inject_nudge_context(v2_db, BOB, REL, "BASE")
    assert SECRET not in prompt
    assert "abandoned" not in prompt


@pytest.mark.asyncio
async def test_nudges_target_only_recipient(v2_db):
    await _add_nudge(v2_db)
    prompt, ids = await inject_nudge_context(v2_db, ALICE, REL, "BASE")
    assert ids == [] and SUGGESTION not in prompt


@pytest.mark.asyncio
async def test_mark_delivered(v2_db):
    fid = await _add_nudge(v2_db)
    _, ids = await inject_nudge_context(v2_db, BOB, REL, "BASE")
    await mark_nudges_delivered(v2_db, ids)
    assert await _status(v2_db, fid) == "delivered"
    _, again = await inject_nudge_context(v2_db, BOB, REL, "BASE")
    assert again == []


@pytest.mark.asyncio
async def test_nudge_disabled_setting(v2_db):
    await _add_nudge(v2_db)
    await apply_settings_ops(v2_db, BOB, [{"key": "nudge_enabled", "value": "false"}])
    prompt, ids = await inject_nudge_context(v2_db, BOB, REL, "BASE")
    assert (prompt, ids) == ("BASE", [])


class _Rng(random.Random):
    def __init__(self, value: float) -> None:
        super().__init__(0)
        self._v = value

    def random(self) -> float:  # type: ignore[override]
        return self._v


@pytest.mark.asyncio
async def test_camouflage_injected_when_roll_low(v2_db):
    out = await maybe_inject_camouflage(v2_db, BOB, "BASE", REL, rng=_Rng(0.0))
    assert out.startswith("BASE") and len(out) > len("BASE")


@pytest.mark.asyncio
async def test_camouflage_skipped_when_roll_high(v2_db):
    out = await maybe_inject_camouflage(v2_db, BOB, "BASE", REL, rng=_Rng(0.99))
    assert out == "BASE"


@pytest.mark.asyncio
async def test_camouflage_frequency_off_and_high(v2_db):
    await apply_settings_ops(v2_db, BOB, [{"key": "camouflage_frequency", "value": "off"}])
    assert await maybe_inject_camouflage(v2_db, BOB, "BASE", REL, rng=_Rng(0.0)) == "BASE"
    await apply_settings_ops(v2_db, BOB, [{"key": "camouflage_frequency", "value": "high"}])
    assert await maybe_inject_camouflage(v2_db, BOB, "BASE", REL, rng=_Rng(0.4)) != "BASE"
    await apply_settings_ops(v2_db, BOB, [{"key": "camouflage_frequency", "value": "low"}])
    assert await maybe_inject_camouflage(v2_db, BOB, "BASE", REL, rng=_Rng(0.4)) == "BASE"


@pytest.mark.asyncio
async def test_settings_applied_and_rejected(v2_db):
    res = await apply_settings_ops(v2_db, BOB, [
        {"key": "voice", "value": "warm"},
        {"key": "response_length", "value": "short"},
        {"key": "voice", "value": "pirate"},
        {"key": "admin", "value": "true"},
    ])
    assert [r["status"] for r in res] == ["applied", "applied", "rejected", "rejected"]
    rows = await db_mod._fetch_all(
        v2_db, "SELECT key, value FROM user_meta WHERE user_id = ?", (BOB,),
    )
    assert {r["key"]: r["value"] for r in rows} == {
        "setting:voice": "warm", "setting:response_length": "short",
    }


@pytest.mark.asyncio
async def test_service_confirms_settings(service, test_db, paired_users):
    uid, _ = paired_users
    count = await service._apply_memory_ops_from_result(
        uid, {"reply": "ok", "settings_ops": [{"key": "voice", "value": "warm"}]}, "be warmer",
    )
    assert count == 0
    assert "voice" in service._last_settings_confirmation
    rows = await db_mod._fetch_all(
        test_db, "SELECT value FROM user_meta WHERE user_id = ? AND key = 'setting:voice'", (uid,),
    )
    assert rows[0]["value"] == "warm"
