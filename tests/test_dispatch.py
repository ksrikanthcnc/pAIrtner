"""BotService.dispatch — one text -> command router shared by CLI/WebSocket adapters."""
from __future__ import annotations

import re

import pytest

from couples_bot.handlers import BotService


@pytest.mark.asyncio
async def test_slash_start_runs_command_not_chat(service: BotService, mock_llm):
    """/start must reach handle_start (pairing code), never the LLM chat path."""
    out = await service.dispatch(5001, "Zed", "/start")
    assert out, "expected a reply"
    assert mock_llm.calls == []  # no LLM call: it is a command, not chat
    assert re.search(r"\b[A-Z0-9]{6}\b", out[0].text)


@pytest.mark.asyncio
async def test_unknown_command(service: BotService):
    out = await service.dispatch(5002, "Zed", "/nosuchthing")
    assert "Unknown command: /nosuchthing" in out[0].text


@pytest.mark.asyncio
async def test_bot_suffix_and_case_are_normalised(service: BotService):
    out = await service.dispatch(5003, "Zed", "/HELP@pAIrtner_bot")
    assert "Unknown command" not in out[0].text


@pytest.mark.asyncio
async def test_args_are_passed_through(service: BotService):
    """/join with a bad code reaches handle_join (not the unknown-command path)."""
    await service.dispatch(5004, "Zed", "/start")
    out = await service.dispatch(5004, "Zed", "/join ZZZZZZ")
    assert "Unknown command" not in out[0].text


@pytest.mark.asyncio
async def test_plain_text_goes_to_chat(service: BotService, mock_llm):
    await service.dispatch(5005, "Zed", "/start")
    out = await service.dispatch(5005, "Zed", "just saying hi")
    assert out  # handled by handle_message


@pytest.mark.asyncio
async def test_every_command_in_table_is_a_real_handler(service: BotService):
    for cmd, factory in service._command_table(1, "x", None).items():
        assert callable(factory), cmd
