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
    """The dispatch table routes /dryrun, /debug, and /model."""
    resp = await service.dispatch(1, "Alice", "/dryrun hello")
    assert any("dry run" in r.text.lower() or "Usage" in r.text for r in resp)

    resp2 = await service.dispatch(1, "Alice", "/debug hello")
    assert any("debug" in r.text.lower() or "Usage" in r.text for r in resp2)

    resp3 = await service.dispatch(1, "Alice", "/model test-model hello")
    assert len(resp3) >= 1


@pytest.mark.asyncio
async def test_combined_flags(service: BotService, test_db):
    """/debug /dryrun message works (combined flags)."""
    await service.handle_start(1, "Alice")
    code = list(service._pairing_codes.keys())[0]
    await service.handle_join(2, "Bob", code)

    # /dryrun with /debug flag embedded
    resp = await service.handle_dryrun(1, "/debug how are we doing?")
    text = resp[0].text
    # Should show debug output since /debug flag was parsed
    assert "debug" in text.lower() or "Debug" in text


@pytest.mark.asyncio
async def test_model_override_in_dryrun(service: BotService, test_db):
    """/debug /model gemma3:4b message uses the specified model."""
    await service.handle_start(1, "Alice")
    code = list(service._pairing_codes.keys())[0]
    await service.handle_join(2, "Bob", code)

    resp = await service.handle_debug(1, "/model test-model hello")
    text = resp[0].text
    assert "test-model" in text


@pytest.mark.asyncio
async def test_parse_message_flags():
    """Flag parser extracts /dryrun, /debug, /model correctly."""
    from pairtner.handlers.tags import TagsMixin

    flags, model, msg = TagsMixin._parse_message_flags("/debug /model gemma3:4b how are we?")
    assert "debug" in flags
    assert model == "gemma3:4b"
    assert msg == "how are we?"

    flags2, model2, msg2 = TagsMixin._parse_message_flags("/dryrun /debug test message")
    assert "dryrun" in flags2
    assert "debug" in flags2
    assert model2 is None
    assert msg2 == "test message"

    flags3, model3, msg3 = TagsMixin._parse_message_flags("just a normal message")
    assert flags3 == set()
    assert model3 is None
    assert msg3 == "just a normal message"


@pytest.mark.asyncio
async def test_model_command_usage(service: BotService, test_db):
    """/model with no args shows current model; a bare name sets it."""
    resp = await service.handle_model(1, "")
    assert "/model reset" in resp[0].text

    resp2 = await service.handle_model(1, "gemma3:4b")
    assert "gemma3:4b" in resp2[0].text
