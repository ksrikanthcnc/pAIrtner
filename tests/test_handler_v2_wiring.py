"""Wiring tests: ContextComposer (v2) in the plain-message handler."""

from __future__ import annotations

import aiosqlite
import pytest
import pytest_asyncio

from pairtner.config import Config
from pairtner.core.context_composer import ComposedContext
from pairtner.core.prompts import build_chat_prompt_v2
from pairtner.db import ensure_user, init_db, set_partner
from pairtner.handlers import BotService
from pairtner.storage.migrate_v2 import migrate_to_v2
from pairtner.storage.schema_v2 import make_relationship_id
from tests.helpers import MockLLMClient

ALICE, BOB, CARA = 1, 2, 3
REL = make_relationship_id(ALICE, BOB)


def _service(db_path: str, llm: MockLLMClient) -> BotService:
    cfg = Config(
        telegram_token="t", gemini_api_key="k", model_default="m",
        model_heavy=["h"], cooldown_minutes=5, db_path=db_path, heavy_daily_cap=10,
    )
    return BotService(config=cfg, db_path=db_path, llm=llm)


async def _add(path, owner, text, visibility, **kw):
    cols = {"owner_id": owner, "relationship_id": REL, "text": text,
            "visibility": visibility, "kind": "working", **kw}
    async with aiosqlite.connect(path) as db:
        await db.execute(
            f"INSERT INTO facts({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            tuple(cols.values()),
        )
        await db.commit()


@pytest_asyncio.fixture
async def v2_db(tmp_path) -> str:
    path = str(tmp_path / "v2.db")
    await init_db(path)
    for uid, name in ((ALICE, "Alice"), (BOB, "Bob"), (CARA, "Cara")):
        await ensure_user(path, uid, name)
    await set_partner(path, ALICE, BOB)
    await migrate_to_v2(path)
    return path


@pytest_asyncio.fixture
async def v1_db(tmp_path) -> str:
    path = str(tmp_path / "v1.db")
    await init_db(path)
    for uid, name in ((ALICE, "Alice"), (BOB, "Bob"), (CARA, "Cara")):
        await ensure_user(path, uid, name)
    await set_partner(path, ALICE, BOB)
    return path


async def test_paired_v2_user_gets_composer_path(v2_db):
    svc = _service(v2_db, MockLLMClient())
    res = await svc._build_ctx_v2(BOB, "hello")
    assert res is not None
    composed, rel = res
    assert isinstance(composed, ComposedContext)
    assert rel == REL


async def test_unpaired_user_falls_back_to_v1(v2_db):
    llm = MockLLMClient()
    svc = _service(v2_db, llm)
    assert await svc._build_ctx_v2(CARA, "hi") is None
    await svc._handle_plain(CARA, "hi there")
    assert llm.calls  # v1 path still works end-to-end


async def test_v1_schema_falls_back_even_if_paired(v1_db, monkeypatch):
    async def _no_v2(_path):
        return False

    monkeypatch.setattr("pairtner.handlers.service._has_v2_columns", _no_v2)
    llm = MockLLMClient()
    svc = _service(v1_db, llm)
    assert await svc._build_ctx_v2(ALICE, "hi") is None
    await svc._handle_plain(ALICE, "hi there")
    assert llm.calls


async def test_partner_private_facts_never_in_prompt(v2_db):
    await _add(v2_db, ALICE, "ALICE_PRIVATE_SECRET_XYZ", "private")
    await _add(v2_db, ALICE, "Alice loves surprise picnics", "nudge_eligible")
    await _add(v2_db, BOB, "Bob private worry about work", "private")
    llm = MockLLMClient()
    svc = _service(v2_db, llm)
    await svc._handle_plain(BOB, "tell me about Alice secret picnics")
    prompt = llm.calls[0]["user_content"]
    assert "ALICE_PRIVATE_SECRET_XYZ" not in prompt
    assert "Alice loves surprise picnics" in prompt
    assert "Bob private worry about work" in prompt


async def test_nudge_suggestion_in_prompt_and_delivered(v2_db):
    await _add(
        v2_db, ALICE, "Alice feels ignored when Bob is on his phone", "private",
        nudge_target_id=BOB, nudge_suggestion="Put the phone away at dinner",
        nudge_type="behavior", nudge_status="pending",
    )
    llm = MockLLMClient()
    svc = _service(v2_db, llm)
    await svc._handle_plain(BOB, "how was dinner")
    prompt = llm.calls[0]["user_content"]
    assert "Put the phone away at dinner" in prompt
    assert "feels ignored" not in prompt
    async with aiosqlite.connect(v2_db) as db:
        async with db.execute(
            "SELECT nudge_status FROM facts WHERE nudge_suggestion IS NOT NULL"
        ) as cur:
            assert (await cur.fetchone())[0] == "delivered"


async def test_memory_ops_v2_set_visibility_and_nudge(v2_db):
    llm = MockLLMClient([{
        "reply": "ok", "relay_draft": None, "new_facts": [],
        "memory_ops": [{
            "op": "add", "kind": "working", "text": "Bob wants more date nights",
            "visibility": "nudge_eligible", "nudge_type": "behavior",
            "nudge_suggestion": "Plan a date night this week",
            "nudge_target": "partner",
        }],
    }])
    svc = _service(v2_db, llm)
    await svc._handle_plain(BOB, "I wish we had more date nights")
    async with aiosqlite.connect(v2_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM facts WHERE text = 'Bob wants more date nights'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row["visibility"] == "nudge_eligible"
    assert row["relationship_id"] == REL
    assert row["nudge_target_id"] == ALICE
    assert row["nudge_status"] == "pending"
    assert row["nudge_suggestion"] == "Plan a date night this week"


async def test_build_chat_prompt_v2_sections():
    c = ComposedContext(
        core_facts=["Core A"], working_facts=["Work B"], fts_matches=["Match C"],
        partner_facts=["Partner D"], nudge_suggestions=["Nudge E"],
        summaries=["(daily) Sum F"], proactive=["Upcoming (today): G"],
        recent_messages=[("user", "earlier msg", "")],
    )
    system, content = build_chat_prompt_v2(c, "current text")
    assert system
    for s in ("Core A", "Work B", "Match C", "Partner D", "Nudge E",
              "Sum F", "Upcoming (today): G", "earlier msg", "current text"):
        assert s in content
