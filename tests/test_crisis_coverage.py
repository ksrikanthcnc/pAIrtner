"""Safety regression: violence/self-harm disclosures are caught on every path
that could otherwise produce a relay draft, and everyday phrases are not."""
from __future__ import annotations

import pytest

from couples_bot.handlers import BotService
from couples_bot.sanitize import detect_crisis

CRISIS = [
    "he hit me again last night but don't make it sound bad",
    "he slapped me",
    "she pushed me against the wall",
    "he beats me when he drinks",
    "my husband hits me",
    "I'm scared he'll hurt me",
    "I don't want to live anymore",
    "honestly I'd be better off dead",
]
NOT_CRISIS = [
    "this marriage is killing me lol",
    "the movie hit me hard",
    "we hit the gym together",
    "he hit the nail on the head",
    "that song really hit me",
    "I'm dying to see you",
]


@pytest.mark.parametrize("text", CRISIS)
def test_detects(text):
    assert detect_crisis(text)[0], text


@pytest.mark.parametrize("text", NOT_CRISIS)
def test_does_not_overfire(text):
    assert not detect_crisis(text)[0], text


def _no_relay(responses):
    return all(not r.buttons for r in responses) and not any(
        "draft" in (r.text or "").lower() for r in responses
    )


@pytest.mark.asyncio
async def test_send_tag_disclosure_never_becomes_a_relay(service: BotService, paired_users, mock_llm):
    a, _ = paired_users
    out = await service.dispatch(a, "Alice", "#send he hit me again last night")
    assert _no_relay(out)
    assert mock_llm.calls == []


@pytest.mark.asyncio
async def test_tell_command_disclosure_never_becomes_a_relay(service: BotService, paired_users, mock_llm):
    a, _ = paired_users
    out = await service.dispatch(a, "Alice", "/tell my husband hits me")
    assert _no_relay(out)
    assert mock_llm.calls == []


@pytest.mark.asyncio
async def test_edit_text_disclosure_is_caught_before_edit_handler(service: BotService, paired_users, mock_llm):
    a, _ = paired_users
    service._edit_state[a] = 1  # user is editing pending draft #1
    out = await service.dispatch(a, "Alice", "he slapped me, just send it")
    assert _no_relay(out)
    assert mock_llm.calls == []
