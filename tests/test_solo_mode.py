"""Solo/personal mode: schema, config, dispatch, /mode, prompt, nudges."""
from __future__ import annotations

import dataclasses
import sqlite3

import pytest

from pairtner import db as db_mod
from pairtner.config import Config
from pairtner.core.prompts import SOLO_PERSONA, apply_solo_persona
from pairtner.features import nudges
from pairtner.handlers import BotService
from pairtner.storage.migrate_v2 import migrate_to_v2
from tests.helpers import MockLLMClient


def _svc(service: BotService, **cfg) -> BotService:
    service.config = dataclasses.replace(service.config, **cfg)
    return service


# -- schema / migration -------------------------------------------------------

@pytest.mark.asyncio
async def test_mode_column_defaults_to_couple(test_db):
    await db_mod.create_user(test_db, 1, "a")
    assert (await db_mod.get_user(test_db, 1))["mode"] == "couple"
    assert await db_mod.get_user_mode(test_db, 1) == "couple"


@pytest.mark.asyncio
async def test_set_user_mode_and_validation(test_db):
    await db_mod.create_user(test_db, 1)
    await db_mod.set_user_mode(test_db, 1, "solo")
    assert await db_mod.get_user_mode(test_db, 1) == "solo"
    with pytest.raises(ValueError):
        await db_mod.set_user_mode(test_db, 1, "group")
    with pytest.raises(ValueError):
        await db_mod.create_user(test_db, 2, mode="bogus")


@pytest.mark.asyncio
async def test_unknown_user_mode_is_couple(test_db):
    assert await db_mod.get_user_mode(test_db, 999) == "couple"


@pytest.mark.asyncio
async def test_migration_adds_mode_column_idempotently(tmp_path):
    """Base schema now includes mode column; migration is a no-op."""
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    from pairtner.storage.schema import SCHEMA_SQL
    conn.executescript(SCHEMA_SQL)
    assert "mode" in [r[1] for r in conn.execute("PRAGMA table_info(users)")]
    conn.execute("INSERT INTO users (tg_id, name) VALUES (1, 'x')")
    conn.commit()
    conn.close()
    await migrate_to_v2(path)
    await migrate_to_v2(path)
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT mode FROM users").fetchone()[0] == "couple"
    conn.close()


@pytest.mark.asyncio
async def test_mode_check_constraint(test_db):
    await db_mod.create_user(test_db, 1)
    conn = sqlite3.connect(test_db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE users SET mode='bad' WHERE tg_id=1")
    conn.close()


# -- config -------------------------------------------------------------------

def test_solo_mode_default_off(config):
    assert config.solo_mode is False


def test_solo_mode_env(monkeypatch):
    from pairtner.config import load_config
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("SOLO_MODE", "true")
    assert load_config().solo_mode is True
    monkeypatch.setenv("SOLO_MODE", "false")
    assert load_config().solo_mode is False


# -- service ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_new_user_default_mode_from_config(service):
    _svc(service, solo_mode=True)
    out = await service.handle_start(10, "Sam")
    assert "journal" in out[0].text.lower()
    assert await db_mod.get_user_mode(service.db_path, 10) == "solo"
    # no pairing code issued
    assert not service._pairing_codes


@pytest.mark.asyncio
async def test_existing_user_keeps_mode_when_env_changes(service):
    await db_mod.create_user(service.db_path, 11)
    _svc(service, solo_mode=True)
    await service.handle_message(11, "x", "hello")
    assert await db_mod.get_user_mode(service.db_path, 11) == "couple"


@pytest.mark.asyncio
async def test_mode_command_switches(service):
    await db_mod.create_user(service.db_path, 1)
    r = await service.dispatch(1, "a", "/mode solo")
    assert "solo" in r[0].text
    assert await db_mod.get_user_mode(service.db_path, 1) == "solo"
    r = await service.dispatch(1, "a", "/mode couple")
    assert await db_mod.get_user_mode(service.db_path, 1) == "couple"
    # legacy relay style still works and does not touch account mode
    await service.dispatch(1, "a", "/mode voice")
    assert await db_mod.get_user_mode(service.db_path, 1) == "couple"
    r = await service.dispatch(1, "a", "/mode")
    assert "Account mode" in r[0].text


@pytest.mark.asyncio
async def test_solo_prompt_and_no_relay(service):
    await db_mod.create_user(service.db_path, 1)
    await db_mod.set_user_mode(service.db_path, 1, "solo")
    service.llm = MockLLMClient([{
        "reply": "Tell me more", "new_facts": [],
        "relay_draft": "should be ignored", "partner_themes": ["x"],
    }])
    out = await service.handle_message(1, "a", "rough day")
    assert out[0].text.startswith("Tell me more")
    assert out[0].buttons is None  # no relay choice buttons
    assert service.llm.calls[0]["system_prompt"].startswith(SOLO_PERSONA)
    assert not service._relay_choices


@pytest.mark.asyncio
async def test_couple_prompt_has_no_solo_persona(service):
    await db_mod.create_user(service.db_path, 1)
    service.llm = MockLLMClient()
    await service.handle_message(1, "a", "hi")
    assert SOLO_PERSONA not in service.llm.calls[0]["system_prompt"]


@pytest.mark.asyncio
async def test_solo_skips_partner_themes_extras(service, monkeypatch):
    seen = {}

    async def fake(db, uid, result, cfg):
        seen.update(result)

    monkeypatch.setattr("pairtner.handlers.tags.process_llm_extras", fake)
    await db_mod.create_user(service.db_path, 1)
    await db_mod.set_user_mode(service.db_path, 1, "solo")
    service.llm = MockLLMClient([{
        "reply": "ok", "new_facts": [], "relay_draft": None,
        "partner_themes": ["t"],
    }])
    await service.handle_message(1, "a", "hi")
    assert seen["partner_themes"] == []


@pytest.mark.asyncio
async def test_solo_blocks_partner_commands(service):
    await db_mod.create_user(service.db_path, 1)
    await db_mod.set_user_mode(service.db_path, 1, "solo")
    for text in ("/join ABC123", "#send hello"):
        out = await service.dispatch(1, "a", text)
        assert "solo mode" in out[0].text, text


@pytest.mark.asyncio
async def test_solo_skips_nudges(test_db, config):
    await db_mod.create_user(test_db, 1)
    assert await nudges._may_nudge(test_db, 1, config) is True
    await db_mod.set_user_mode(test_db, 1, "solo")
    assert await nudges._may_nudge(test_db, 1, config) is False


def test_apply_solo_persona_prepends():
    out = apply_solo_persona("BASE")
    assert out.startswith(SOLO_PERSONA) and out.endswith("BASE")
    assert "journal and coach" in out
