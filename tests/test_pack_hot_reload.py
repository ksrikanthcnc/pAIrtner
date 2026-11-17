"""Edits to a pack take effect on the next message, with no restart."""

from __future__ import annotations

import logging

import pytest
import yaml

from pairtner import pack
from pairtner.handlers import BotService
from tests.helpers import MockLLMClient, copy_default_pack


@pytest.fixture
def pack_dir(tmp_path):
    root = copy_default_pack(tmp_path)
    pack.activate(root)
    return root


async def _system_prompt(service: BotService, user_id: int, text: str) -> str:
    mock = MockLLMClient([{"reply": "ok", "new_facts": [], "relay_draft": None}])
    service.llm = mock
    await service.handle_message(user_id, "Alice", text)
    assert len(mock.calls) == 1
    return mock.calls[0]["system_prompt"]


async def test_persona_edit_applies_to_next_message(service, paired_users, pack_dir):
    a_id, _ = paired_users
    first = await _system_prompt(service, a_id, "hello")
    (pack_dir / "persona.md").write_text("You are HotReloadBot, version two.")
    second = await _system_prompt(service, a_id, "hello again")
    assert "HotReloadBot" not in first
    assert second.startswith("You are HotReloadBot, version two.")


async def test_contract_edit_applies_to_next_message(service, paired_users, pack_dir):
    a_id, _ = paired_users
    before = await _system_prompt(service, a_id, "hello")
    contract = (pack_dir / "contract.md").read_text()
    (pack_dir / "contract.md").write_text(contract + "\nContract marker: CM-42")
    after = await _system_prompt(service, a_id, "hello again")
    assert "CM-42" not in before
    assert "Contract marker: CM-42" in after


async def test_strings_edit_applies_to_next_message(service, paired_users, pack_dir):
    a_id, _ = paired_users
    # "Current message" label is part of the user content, so check that side.
    mock = MockLLMClient([{"reply": "ok", "new_facts": [], "relay_draft": None}] * 2)
    service.llm = mock
    await service.handle_message(a_id, "Alice", "hello")
    strings = yaml.safe_load((pack_dir / "strings.yaml").read_text(encoding="utf-8"))
    strings["label_current_message"] = "Latest words from the user"
    (pack_dir / "strings.yaml").write_text(
        yaml.safe_dump(strings, allow_unicode=True), encoding="utf-8",
    )
    await service.handle_message(a_id, "Alice", "hello again")
    assert "## Current message\nhello" in mock.calls[0]["user_content"]
    assert "## Latest words from the user\nhello again" in mock.calls[1]["user_content"]


async def test_broken_edit_keeps_last_good_prompt_and_logs(
    service, paired_users, pack_dir, caplog,
):
    a_id, _ = paired_users
    (pack_dir / "persona.md").write_text("You are GoodBot.")
    assert "GoodBot" in await _system_prompt(service, a_id, "one")

    (pack_dir / "persona.md").write_text("")  # half-saved edit
    with caplog.at_level(logging.ERROR, logger="pairtner.pack"):
        assert "GoodBot" in await _system_prompt(service, a_id, "two")
        assert "GoodBot" in await _system_prompt(service, a_id, "three")
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "persona.md" in errors[0].getMessage()


async def test_fixing_the_edit_recovers(service, paired_users, pack_dir):
    a_id, _ = paired_users
    (pack_dir / "persona.md").write_text("")
    await _system_prompt(service, a_id, "one")
    (pack_dir / "persona.md").write_text("You are RecoveredBot.")
    assert "RecoveredBot" in await _system_prompt(service, a_id, "two")
