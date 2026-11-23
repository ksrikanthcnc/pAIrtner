"""Core-fact cap warning and bot_name config."""

from __future__ import annotations

import aiosqlite
import pytest
import pytest_asyncio

from pairtner.config import load_config
from pairtner.core import prompts
from pairtner.core.context_composer import ContextComposer
from pairtner.storage.schema_v2 import SCHEMA_V2_SQL, make_relationship_id

ALICE, BOB = 1, 2
REL = make_relationship_id(ALICE, BOB)


@pytest_asyncio.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "t.db")
    async with aiosqlite.connect(path) as db:
        await db.executescript(SCHEMA_V2_SQL)
        await db.execute("INSERT INTO users(tg_id, name) VALUES (1,'Alice'),(2,'Bob')")
        await db.execute(
            "INSERT INTO relationships(id, user_a_id, user_b_id) VALUES (?,?,?)",
            (REL, ALICE, BOB),
        )
        await db.commit()
    return path


async def add_core(path, n):
    async with aiosqlite.connect(path) as db:
        for i in range(n):
            await db.execute(
                "INSERT INTO facts(owner_id, relationship_id, text, visibility, kind) "
                "VALUES (?,?,?,?,?)",
                (ALICE, REL, f"pref {i}", "private", "core"),
            )
        await db.commit()


@pytest.mark.asyncio
async def test_more_than_20_core_facts_warns(db_path):
    await add_core(db_path, 21)
    ctx = await ContextComposer(db_path).build_context(ALICE, REL)
    assert ctx.core_cap_warning is not None
    assert "Alice" in ctx.core_cap_warning
    assert "21" in ctx.core_cap_warning


@pytest.mark.asyncio
async def test_20_or_fewer_core_facts_no_warning(db_path):
    await add_core(db_path, 20)
    ctx = await ContextComposer(db_path).build_context(ALICE, REL)
    assert ctx.core_cap_warning is None


def test_bot_name_default_and_yaml(tmp_path, monkeypatch):
    monkeypatch.delenv("BOT_NAME", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    empty = tmp_path / "e.yaml"
    empty.write_text("")
    assert load_config(yaml_path=str(empty)).bot_name == "Eko"
    y = tmp_path / "s.yaml"
    y.write_text("bot_name: Milo\n")
    assert load_config(yaml_path=str(y)).bot_name == "Milo"


def test_bot_name_env_override(tmp_path, monkeypatch):
    y = tmp_path / "s.yaml"
    y.write_text("bot_name: Milo\n")
    monkeypatch.setenv("BOT_NAME", "Zed")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    assert load_config(yaml_path=str(y)).bot_name == "Zed"


def test_bot_name_in_system_prompt():
    try:
        prompts.set_bot_name("Milo")
        assert "Your name is Milo" in prompts.build_system_prompt()
    finally:
        prompts.set_bot_name("Eko")
