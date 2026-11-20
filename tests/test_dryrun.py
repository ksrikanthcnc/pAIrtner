"""Tests for /dryrun and /debug commands."""

import json
import pytest

from pairtner.handlers import BotService
from pairtner.storage.messages import get_recent_messages
from pairtner.storage.facts import get_active_facts


@pytest.mark.asyncio
async def test_dryrun_returns_reply(service: BotService, test_db):
    """Dryrun returns the bot reply without saving anything."""
    # Pair users first
    await service.handle_start(1, "Alice")
    code = list(service._pairing_codes.keys())[0]
    await service.handle_join(2, "Bob", code)

    resp = await service.handle_dryrun(1, "I had a tough day at work")
    assert len(resp) >= 1
    assert "dry run" in resp[0].text.lower() or "Dry run" in resp[0].text
    assert "nothing saved" in resp[0].text.lower()


@pytest.mark.asyncio
async def test_dryrun_no_db_writes(service: BotService, test_db):
    """Dryrun must not write messages or facts to DB."""
    await service.handle_start(1, "Alice")
    code = list(service._pairing_codes.keys())[0]
    await service.handle_join(2, "Bob", code)

    msgs_before = await get_recent_messages(test_db, 1)
    facts_before = await get_active_facts(test_db, 1)

    await service.handle_dryrun(1, "Bob forgot our anniversary again")

    msgs_after = await get_recent_messages(test_db, 1)
    facts_after = await get_active_facts(test_db, 1)

    # Pairing creates some messages; dryrun should add zero more
    assert len(msgs_after) == len(msgs_before), "dryrun should not log messages"
    assert len(facts_after) == len(facts_before), "dryrun should not save facts"


@pytest.mark.asyncio
async def test_debug_shows_internals(service: BotService, test_db):
    """Debug mode shows extracted facts and context info."""
    await service.handle_start(1, "Alice")
    code = list(service._pairing_codes.keys())[0]
    await service.handle_join(2, "Bob", code)

    resp = await service.handle_debug(1, "I want Bob to be more attentive")
    text = resp[0].text
    assert "debug mode" in text.lower() or "Debug mode" in text
    assert "Context used" in text


@pytest.mark.asyncio
async def test_dryrun_empty_message(service: BotService, test_db):
    """Dryrun with no message shows usage."""
    resp = await service.handle_dryrun(1, "")
    assert "Usage" in resp[0].text


@pytest.mark.asyncio
async def test_dispatch_routes_dryrun(service: BotService, test_db):
    """The dispatch table routes /dryrun and /debug."""
    resp = await service.dispatch(1, "Alice", "/dryrun hello")
    assert any("dry run" in r.text.lower() or "Usage" in r.text for r in resp)

    resp2 = await service.dispatch(1, "Alice", "/debug hello")
    assert any("debug" in r.text.lower() or "Usage" in r.text for r in resp2)
