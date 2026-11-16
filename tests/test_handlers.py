"""Handler tests — transport-agnostic, using MockLLMClient."""

from __future__ import annotations

import pytest

from couples_bot.db import (
    create_pending,
    create_user,
    ensure_user,
    get_facts,
    get_pending,
    get_user,
    set_partner,
    update_pending_status,
)
from couples_bot.handlers import BotService, parse_tags

from tests.helpers import MockLLMClient


# ---------------------------------------------------------------------------
# Tag parsing
# ---------------------------------------------------------------------------

class TestParseTags:
    def test_no_tags(self):
        tags, clean = parse_tags("Just a plain message")
        assert tags == set()
        assert clean == "Just a plain message"

    def test_keep_tag(self):
        tags, clean = parse_tags("Remember this #keep")
        assert "#keep" in tags
        assert "#keep" not in clean
        assert "Remember this" in clean

    def test_send_tag(self):
        tags, clean = parse_tags("#send Tell them I'm sorry")
        assert "#send" in tags
        assert "Tell them I'm sorry" in clean.strip()

    def test_combined_tags(self):
        tags, clean = parse_tags("#send #now Please tell them")
        assert "#send" in tags
        assert "#now" in tags
        assert "Please tell them" in clean.strip()

    def test_deep_tag(self):
        tags, clean = parse_tags("Think about this #deep")
        assert "#deep" in tags


# ---------------------------------------------------------------------------
# /start and /join (pairing flow)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pairing_flow(service: BotService, test_db):
    """Full pairing: /start → code → /join → paired."""
    # User A starts
    responses = await service.handle_start(1, "Alice")
    assert len(responses) == 1
    text = responses[0].text
    assert "pairing code" in text.lower() or "code" in text.lower()

    # Extract code from the service's internal state
    assert len(service._pairing_codes) == 1
    code = list(service._pairing_codes.keys())[0]

    # User B joins
    responses = await service.handle_join(2, "Bob", code)
    assert len(responses) == 2  # one for joiner, one for partner

    # Verify pairing in DB
    a = await get_user(test_db, 1)
    b = await get_user(test_db, 2)
    assert a["partner_id"] == 2
    assert b["partner_id"] == 1

    # Code consumed
    assert len(service._pairing_codes) == 0


@pytest.mark.asyncio
async def test_join_invalid_code(service: BotService):
    responses = await service.handle_join(1, "Alice", "BADCODE")
    assert len(responses) == 1
    assert "invalid" in responses[0].text.lower() or "❌" in responses[0].text


@pytest.mark.asyncio
async def test_start_already_paired(service: BotService, test_db, paired_users):
    a_id, _ = paired_users
    responses = await service.handle_start(a_id, "Alice")
    assert any("already paired" in r.text.lower() for r in responses)


# ---------------------------------------------------------------------------
# Approval flow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_approval_flow(service: BotService, test_db, paired_users):
    """Create via /tell → approve → pending gets status 'approved'."""
    a_id, b_id = paired_users

    service.llm = MockLLMClient([
        {"reply": "I understand", "new_facts": [], "relay_draft": "Hey, can we talk?"}
    ])

    responses = await service.handle_tell(a_id, "I need to talk")
    assert len(responses) == 1
    assert responses[0].buttons is not None
    # Should have approval buttons
    labels = [b.text for row in responses[0].buttons for b in row]
    assert any("Approve" in l or "✅" in l for l in labels)

    # Get the pending_id from callback_data
    approve_btn = responses[0].buttons[0][0]
    cb_data = approve_btn.callback_data
    assert cb_data.startswith("ap:")

    # Approve
    responses = await service.handle_callback(a_id, cb_data)
    assert any("approved" in r.text.lower() or "✅" in r.text for r in responses)


@pytest.mark.asyncio
async def test_discard_flow(service: BotService, test_db, paired_users):
    a_id, b_id = paired_users

    service.llm = MockLLMClient([
        {"reply": "Got it", "new_facts": [], "relay_draft": "A draft"}
    ])

    responses = await service.handle_tell(a_id, "Nevermind")
    discard_btn = responses[0].buttons[0][2]  # 3rd button = Discard
    responses = await service.handle_callback(a_id, discard_btn.callback_data)
    assert any("discard" in r.text.lower() or "🗑️" in r.text for r in responses)


@pytest.mark.asyncio
async def test_edit_flow(service: BotService, test_db, paired_users):
    a_id, b_id = paired_users

    service.llm = MockLLMClient([
        {"reply": "Ok", "new_facts": [], "relay_draft": "Original draft"}
    ])

    responses = await service.handle_tell(a_id, "Need to say something")
    edit_btn = responses[0].buttons[0][1]  # 2nd button = Edit

    # Click edit
    responses = await service.handle_callback(a_id, edit_btn.callback_data)
    assert any("edited" in r.text.lower() or "send" in r.text.lower() for r in responses)

    # Send edited text
    responses = await service.handle_message(a_id, "Alice", "My edited message")
    assert len(responses) >= 1
    # Should show new buttons again
    assert responses[0].buttons is not None


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cooldown(service: BotService, test_db, paired_users):
    """Approved message should have send_after in the future."""
    a_id, b_id = paired_users

    service.llm = MockLLMClient([
        {"reply": "Ok", "new_facts": [], "relay_draft": "Draft"}
    ])

    responses = await service.handle_tell(a_id, "Tell them")
    approve_btn = responses[0].buttons[0][0]
    await service.handle_callback(a_id, approve_btn.callback_data)

    # Check the pending row
    from couples_bot.db import get_deliverable
    deliverable = await get_deliverable(test_db)
    # Should NOT be deliverable yet (cooldown = 5 min)
    assert len(deliverable) == 0


@pytest.mark.asyncio
async def test_now_tag_skips_cooldown(service: BotService, test_db, paired_users):
    """#now flag should set send_after to now (immediately deliverable)."""
    a_id, b_id = paired_users

    service.llm = MockLLMClient([
        {"reply": "Ok", "new_facts": [], "relay_draft": "Urgent draft"}
    ])

    responses = await service.handle_tell(a_id, "#now Tell them now")
    # The approve button should have apn: prefix
    approve_btn = responses[0].buttons[0][0]
    assert approve_btn.callback_data.startswith("apn:")

    await service.handle_callback(a_id, approve_btn.callback_data)

    from couples_bot.db import get_deliverable
    deliverable = await get_deliverable(test_db)
    assert len(deliverable) == 1


# ---------------------------------------------------------------------------
# Tag handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_keep_tag_saves_only(service: BotService, test_db, paired_users):
    """#keep should save facts but NOT create a relay draft."""
    a_id, _ = paired_users

    service.llm = MockLLMClient([{
        "reply": "Noted!",
        "new_facts": [{"text": "Loves pizza", "visibility": "private"}],
        "relay_draft": None,
    }])

    responses = await service.handle_message(a_id, "Alice", "#keep I love pizza")
    assert len(responses) == 1
    # Should NOT have relay buttons
    assert responses[0].buttons is None

    facts = await get_facts(test_db, a_id)
    texts = [f["text"] for f in facts]
    assert "Loves pizza" in texts


@pytest.mark.asyncio
async def test_send_tag_drafts_relay(service: BotService, test_db, paired_users):
    """#send should create a relay draft with approval buttons."""
    a_id, _ = paired_users

    service.llm = MockLLMClient([{
        "reply": "I'll help relay that",
        "new_facts": [],
        "relay_draft": "Gentle version",
    }])

    responses = await service.handle_message(a_id, "Alice", "#send I'm upset")
    assert len(responses) == 1
    assert responses[0].buttons is not None
    assert "Gentle version" in responses[0].text


@pytest.mark.asyncio
async def test_deep_tag_uses_heavy_model(service: BotService, test_db, paired_users):
    """#deep should route to the heavy model."""
    a_id, _ = paired_users
    mock = MockLLMClient([{
        "reply": "Deep thoughts",
        "new_facts": [],
        "relay_draft": None,
    }])
    service.llm = mock

    await service.handle_message(a_id, "Alice", "#deep What do you think?")
    assert len(mock.calls) == 1
    assert mock.calls[0]["model"] == "test-heavy-1"


@pytest.mark.asyncio
async def test_no_tag_asks_user(service: BotService, test_db, paired_users):
    """A plain message with a relay_draft should show choice buttons."""
    a_id, _ = paired_users

    service.llm = MockLLMClient([{
        "reply": "I hear you",
        "new_facts": [],
        "relay_draft": "A kind version",
    }])

    responses = await service.handle_message(a_id, "Alice", "I need to tell my partner something")
    assert len(responses) == 1
    # Should have save/relay buttons
    assert responses[0].buttons is not None
    labels = [b.text for row in responses[0].buttons for b in row]
    assert any("save" in l.lower() or "💾" in l for l in labels)
    assert any("relay" in l.lower() or "💌" in l for l in labels)


@pytest.mark.asyncio
async def test_no_tag_no_relay_draft(service: BotService, test_db, paired_users):
    """A plain message without relay_draft should just reply (no buttons)."""
    a_id, _ = paired_users

    service.llm = MockLLMClient([{
        "reply": "That's interesting!",
        "new_facts": [],
        "relay_draft": None,
    }])

    responses = await service.handle_message(a_id, "Alice", "Just chatting")
    assert len(responses) == 1
    assert responses[0].buttons is None
    assert "interesting" in responses[0].text.lower()


# ---------------------------------------------------------------------------
# /whatdoyouknow & /forget
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_whatdoyouknow(service: BotService, test_db, paired_users):
    a_id, _ = paired_users
    responses = await service.handle_whatdoyouknow(a_id)
    text = responses[0].text
    assert "Alice feels stressed" in text
    assert "morning walks" in text


@pytest.mark.asyncio
async def test_forget_by_id(service: BotService, test_db, paired_users):
    a_id, _ = paired_users
    facts = await get_facts(test_db, a_id)
    fact_id = facts[0]["id"]

    responses = await service.handle_forget(a_id, str(fact_id))
    assert any("deleted" in r.text.lower() or "🗑️" in r.text for r in responses)

    remaining = await get_facts(test_db, a_id)
    assert len(remaining) == len(facts) - 1


@pytest.mark.asyncio
async def test_forget_all_requires_confirm(service: BotService, test_db, paired_users):
    a_id, _ = paired_users

    # First call: confirmation prompt
    responses = await service.handle_forget(a_id, "all")
    assert any("confirm" in r.text.lower() or "⚠️" in r.text for r in responses)

    # Facts still exist
    facts = await get_facts(test_db, a_id)
    assert len(facts) > 0

    # Second call: actual deletion
    responses = await service.handle_forget(a_id, "all")
    assert any("deleted" in r.text.lower() or "🗑️" in r.text for r in responses)

    facts = await get_facts(test_db, a_id)
    assert len(facts) == 0


# ---------------------------------------------------------------------------
# /export
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_export(service: BotService, test_db, paired_users):
    a_id, _ = paired_users
    responses = await service.handle_export(a_id)
    assert len(responses) == 1
    assert responses[0].document is not None
    fname, content = responses[0].document
    assert fname == "my_facts.txt"
    assert "Alice feels stressed" in content


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_help(service: BotService, test_db, paired_users):
    a_id, _ = paired_users
    responses = await service.handle_help(a_id)
    text = responses[0].text
    assert "/start" in text
    assert "/tell" in text
    assert "not" in text.lower() and "therapy" in text.lower()


# ---------------------------------------------------------------------------
# /lang
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lang_creates_user_if_not_exists(service: BotService, test_db):
    """Calling /lang for a new user should create the user row first."""
    user_id = 9999
    # User does not exist yet
    user = await get_user(test_db, user_id)
    assert user is None

    responses = await service.handle_lang(user_id, "en")
    assert len(responses) == 1

    # User should now exist in DB
    user = await get_user(test_db, user_id)
    assert user is not None
