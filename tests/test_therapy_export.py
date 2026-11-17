"""Tests for therapy export (FEAT-004 batch4)."""

from __future__ import annotations

import pytest
import pytest_asyncio

from pairtner import db as db_mod
from pairtner.config import Config
from pairtner.db import (
    add_fact,
    add_timeline_event,
    add_user_goal,
    add_shared_goal,
    create_user,
    init_db,
    record_pattern,
    set_partner,
)
from pairtner.handlers import BotService
from pairtner.therapy_export import generate_therapy_report

from tests.helpers import MockLLMClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def te_db(tmp_path) -> str:
    db_path = str(tmp_path / "te_test.db")
    await init_db(db_path)
    return db_path


@pytest_asyncio.fixture
async def te_paired(te_db) -> tuple[str, int, int]:
    """Create paired users with sample data for therapy export."""
    a, b = 6001, 6002
    await create_user(te_db, a, "Arjun")
    await create_user(te_db, b, "Sneha")
    await set_partner(te_db, a, b)

    # Facts for user A (private + shared)
    await add_fact(te_db, a, "Arjun feels stressed at work", "private")
    await add_fact(te_db, a, "We enjoy hiking together", "shared")

    # Facts for user B (private — should NOT appear in A's report)
    await add_fact(te_db, b, "Sneha worries about money privately", "private")
    await add_fact(te_db, b, "We both love cooking", "shared")

    # Timeline events
    cid = db_mod.make_couple_id(a, b)
    await add_timeline_event(te_db, cid, "paired", "Arjun and Sneha paired")
    await add_timeline_event(te_db, cid, "goal", "Set fitness goal")
    await add_timeline_event(te_db, cid, "relay", "First relay sent")

    # Patterns for user A
    await record_pattern(te_db, a, "tends to avoid conflict")
    await record_pattern(te_db, a, "uses 'I feel' statements")

    # Goals
    await add_user_goal(te_db, a, "Be more present")
    await add_shared_goal(te_db, cid, a, "Weekly date night")

    return te_db, a, b


@pytest_asyncio.fixture
async def te_service(te_db) -> BotService:
    cfg = Config(
        telegram_token="test", gemini_api_key="test-key", db_path=te_db,
    )
    return BotService(config=cfg, db_path=te_db, llm=MockLLMClient())


# ---------------------------------------------------------------------------
# generate_therapy_report tests
# ---------------------------------------------------------------------------

class TestTherapyReportGeneration:
    async def test_report_generated_with_data(self, te_paired):
        db, a, b = te_paired
        mock_llm = MockLLMClient([
            {"reply": "## Relationship Timeline\nArjun and Sneha paired recently.\n"
                      "## Communication Patterns\nArjun tends to avoid conflict.\n"
                      "## Growth Areas\nMore open communication would help.",
             "new_facts": []},
        ])

        report = await generate_therapy_report(db, mock_llm, a)
        assert report is not None
        assert "Therapy Report" in report
        assert "Arjun" in report

    async def test_report_returns_none_for_unpaired_user(self, te_db):
        await create_user(te_db, 6099, "Solo")
        mock_llm = MockLLMClient()
        report = await generate_therapy_report(te_db, mock_llm, 6099)
        assert report is None

    async def test_report_returns_none_when_no_data(self, te_db):
        await create_user(te_db, 6003, "Empty")
        await create_user(te_db, 6004, "Also Empty")
        await set_partner(te_db, 6003, 6004)

        mock_llm = MockLLMClient()
        report = await generate_therapy_report(te_db, mock_llm, 6003)
        assert report is None

    async def test_report_privacy_no_partner_private_facts(self, te_paired):
        """The report should NEVER contain the partner's private facts."""
        db, a, b = te_paired

        # The LLM is a mock, so we check the prompt sent to it
        mock_llm = MockLLMClient([
            {"reply": "Test report content", "new_facts": []},
        ])

        await generate_therapy_report(db, mock_llm, a)

        # Check the LLM was called with user A's context
        assert len(mock_llm.calls) == 1
        prompt_content = mock_llm.calls[0]["user_content"]

        # Arjun's private fact should be in the prompt
        assert "Arjun feels stressed at work" in prompt_content

        # Sneha's PRIVATE fact must NOT appear
        assert "Sneha worries about money privately" not in prompt_content

        # Shared facts from BOTH should appear
        assert "We enjoy hiking together" in prompt_content
        assert "We both love cooking" in prompt_content

    async def test_report_includes_patterns(self, te_paired):
        db, a, b = te_paired
        mock_llm = MockLLMClient([
            {"reply": "Report with patterns", "new_facts": []},
        ])

        await generate_therapy_report(db, mock_llm, a)

        prompt_content = mock_llm.calls[0]["user_content"]
        assert "tends to avoid conflict" in prompt_content
        assert "I feel" in prompt_content

    async def test_report_includes_goals(self, te_paired):
        db, a, b = te_paired
        mock_llm = MockLLMClient([
            {"reply": "Report with goals", "new_facts": []},
        ])

        await generate_therapy_report(db, mock_llm, a)

        prompt_content = mock_llm.calls[0]["user_content"]
        assert "Be more present" in prompt_content
        assert "Weekly date night" in prompt_content

    async def test_report_includes_timeline(self, te_paired):
        db, a, b = te_paired
        mock_llm = MockLLMClient([
            {"reply": "Report with timeline", "new_facts": []},
        ])

        await generate_therapy_report(db, mock_llm, a)

        prompt_content = mock_llm.calls[0]["user_content"]
        assert "paired" in prompt_content.lower()
        assert "relay" in prompt_content.lower()

    async def test_report_has_header_and_footer(self, te_paired):
        db, a, b = te_paired
        mock_llm = MockLLMClient([
            {"reply": "Main report content", "new_facts": []},
        ])

        report = await generate_therapy_report(db, mock_llm, a)
        assert report is not None
        assert "Therapy Report" in report
        assert "Arjun" in report
        assert "Partner's private data is never included" in report


# ---------------------------------------------------------------------------
# /export therapy command tests
# ---------------------------------------------------------------------------

class TestExportTherapyCommand:
    async def test_export_therapy_generates_document(self, te_db, te_service):
        db = te_db
        await create_user(db, 7001, "Raj")
        await create_user(db, 7002, "Nita")
        await set_partner(db, 7001, 7002)

        # Add some data so report isn't empty
        cid = db_mod.make_couple_id(7001, 7002)
        await add_timeline_event(db, cid, "paired", "Paired")
        await add_fact(db, 7001, "Raj likes mornings", "private")

        svc = te_service
        svc.llm = MockLLMClient([
            {"reply": "Therapy report for Raj", "new_facts": []},
        ])

        resp = await svc.handle_export(7001, "therapy")
        assert len(resp) == 1
        assert resp[0].document is not None
        fname, content = resp[0].document
        assert fname.startswith("therapy_report_")
        assert fname.endswith(".txt")
        assert "Therapy Report" in content

    async def test_export_therapy_empty(self, te_db, te_service):
        db = te_db
        await create_user(db, 7003, "Empty")
        await create_user(db, 7004, "User")
        await set_partner(db, 7003, 7004)

        svc = te_service
        resp = await svc.handle_export(7003, "therapy")
        assert len(resp) == 1
        assert "enough data" in resp[0].text.lower() or "empty" in resp[0].text.lower()

    async def test_export_therapy_not_paired(self, te_db, te_service):
        db = te_db
        await create_user(db, 7005, "Solo")

        svc = te_service
        resp = await svc.handle_export(7005, "therapy")
        assert len(resp) == 1
        assert "pair" in resp[0].text.lower()

    async def test_export_without_arg_still_works(self, te_db, te_service):
        """Regression: /export without arg should still export facts."""
        db = te_db
        await create_user(db, 7006, "Facts")
        await add_fact(db, 7006, "Test fact", "private")

        svc = te_service
        resp = await svc.handle_export(7006, None)
        assert len(resp) == 1
        assert resp[0].document is not None
        fname, content = resp[0].document
        assert fname == "my_facts.txt"
        assert "Test fact" in content

    async def test_export_facts_empty(self, te_db, te_service):
        """Regression: /export with no facts returns empty message."""
        db = te_db
        await create_user(db, 7007, "NoFacts")

        svc = te_service
        resp = await svc.handle_export(7007, None)
        assert len(resp) == 1
        assert resp[0].document is None
