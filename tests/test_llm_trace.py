"""Per-call pack tracing: TracingLLM log line, llm_calls and pack_snapshots rows."""
from __future__ import annotations

import hashlib
import logging

import aiosqlite
import pytest

from pairtner import pack, prompts
from pairtner.llm import FALLBACK_REPLY, TracingLLM
from tests.helpers import MockLLMClient, copy_default_pack


async def _rows(db_path: str, sql: str) -> list[tuple]:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(sql) as cur:
            return await cur.fetchall()


def _build() -> tuple[str, str]:
    ctx = prompts.UserContext(user_name="Alice", partner_name="Bob")
    return prompts.build_chat_prompt(ctx, "hi")


class _UsageClient(MockLLMClient):
    async def check_usage_warning(self, model: str):
        return f"warn {model}"


async def test_one_call_one_row_and_one_snapshot(test_db, caplog):
    pack.activate("packs/default")
    llm = TracingLLM(MockLLMClient(), test_db)
    system, user = _build()
    with caplog.at_level(logging.INFO, logger="pairtner.trace"):
        await llm.generate(user, system)
        await llm.generate(user, system)
    snap = pack.current().snapshot()
    calls = await _rows(test_db, "SELECT task, pack_hash, prompt_sha256 FROM llm_calls")
    assert len(calls) == 2
    assert calls[0][0] == "chat"
    assert calls[0][1] == snap["pack_hash"]
    assert calls[0][2] == hashlib.sha256((system + "\x00" + user).encode()).hexdigest()
    snaps = await _rows(test_db, "SELECT pack_hash, name, version, files_json FROM pack_snapshots")
    assert len(snaps) == 1
    assert snaps[0][0] == snap["pack_hash"]
    for rel, sha in snap["files"].items():
        assert f'"{rel}": "{sha}"' in snaps[0][3]
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("llm_call"))
    assert f"pack={snap['name']}@{snap['version']}" in line
    assert f"pack_hash={snap['pack_hash'][:12]}" in line
    assert "task=chat" in line


async def test_hot_reload_gives_new_hash_and_row(test_db, tmp_path):
    root = copy_default_pack(tmp_path)
    pack.activate(root)
    llm = TracingLLM(MockLLMClient(), test_db)
    system, user = _build()
    await llm.generate(user, system)
    persona = root / "persona.md"
    persona.write_text(persona.read_text(encoding="utf-8") + "\nExtra line.\n", encoding="utf-8")
    system, user = _build()
    await llm.generate(user, system)
    calls = await _rows(test_db, "SELECT pack_hash FROM llm_calls ORDER BY id")
    assert calls[0][0] != calls[1][0]
    assert len(await _rows(test_db, "SELECT 1 FROM pack_snapshots")) == 2


async def test_trace_uses_hash_active_at_assembly(test_db, tmp_path):
    root = copy_default_pack(tmp_path)
    pack.activate(root)
    llm = TracingLLM(MockLLMClient(), test_db)
    system, user = _build()
    built_hash = pack.current().snapshot()["pack_hash"]
    persona = root / "persona.md"
    persona.write_text(persona.read_text(encoding="utf-8") + "\nChanged.\n", encoding="utf-8")
    assert pack.current().snapshot()["pack_hash"] != built_hash  # reload happened
    await llm.generate(user, system)
    calls = await _rows(test_db, "SELECT pack_hash FROM llm_calls")
    assert calls == [(built_hash,)]


async def test_unassembled_call_is_traced_as_unknown(test_db):
    pack.activate("packs/default")
    pack.last_trace.set(None)
    llm = TracingLLM(MockLLMClient(), test_db)
    await llm.generate("u", "s")
    assert (await _rows(test_db, "SELECT task FROM llm_calls")) == [("unknown",)]


async def test_db_failure_does_not_break_generate(tmp_path, caplog):
    pack.activate("packs/default")
    inner = MockLLMClient()
    llm = TracingLLM(inner, str(tmp_path / "no-such-dir" / "x.db"))
    with caplog.at_level(logging.WARNING, logger="pairtner.llm"):
        result = await llm.generate("u", "s")
    assert result["reply"]
    assert len(inner.calls) == 1
    assert any("trace" in r.getMessage() for r in caplog.records)


async def test_attribute_delegation(test_db):
    llm = TracingLLM(_UsageClient(), test_db)
    assert hasattr(llm, "check_usage_warning")
    assert await llm.check_usage_warning("m") == "warn m"
    assert not hasattr(llm, "no_such_attribute")


async def test_on_call_receives_prompt_and_trace(test_db):
    pack.activate("packs/default")
    seen: list[dict] = []
    llm = TracingLLM(MockLLMClient(), test_db, on_call=seen.append)
    system, user = _build()
    result = await llm.generate(user, system)
    assert len(seen) == 1
    assert seen[0]["system"] == system
    assert seen[0]["user_content"] == user
    assert seen[0]["result"] == result
    assert seen[0]["pack"]["pack_hash"] == pack.current().snapshot()["pack_hash"]


def test_fallback_reply_text():
    assert FALLBACK_REPLY == (
        "I'm having trouble connecting right now. Please try again in a moment."
    )


def test_pack_loaded_logged_on_activate_and_reload(tmp_path, caplog):
    root = copy_default_pack(tmp_path)
    with caplog.at_level(logging.INFO, logger="pairtner.trace"):
        pack.activate(root)
        persona = root / "persona.md"
        persona.write_text(persona.read_text(encoding="utf-8") + "\nMore.\n", encoding="utf-8")
        pack.current()
    loaded = [r.getMessage() for r in caplog.records if r.getMessage().startswith("pack_loaded")]
    assert len(loaded) == 2
    assert "persona.md" in loaded[0]
    assert loaded[0] != loaded[1]
