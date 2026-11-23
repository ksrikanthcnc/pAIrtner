"""Tests for fact lifecycle: aging, re-activation, and partner-informed questions."""
import pytest
import aiosqlite

from pairtner.storage.schema import init_db
from pairtner.storage.migrate_v2 import migrate_to_v2
from pairtner.core.memory_api import MemoryAPI
from pairtner.features.nudge_engine import (
    generate_partner_informed_questions,
    get_mixed_questions,
    get_camouflage_questions,
    load_question_bank,
)


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    await init_db(path)
    await migrate_to_v2(path)
    return path


@pytest.fixture
async def seeded_db(db_path):
    """DB with two users and some facts."""
    from pairtner import db as db_mod
    await db_mod.create_user(db_path, 1, "Alice", "en")
    await db_mod.create_user(db_path, 2, "Bob", "en")
    await db_mod.set_partner(db_path, 1, 2)

    async with aiosqlite.connect(db_path) as db:
        # Alice's facts
        await db.execute(
            "INSERT INTO facts (owner_id, text, kind, status, visibility, "
            "created_at, updated_at) VALUES "
            "(1, 'Loves Italian food', 'working', 'active', 'shared', "
            "datetime('now', '-10 days'), datetime('now', '-10 days'))"
        )
        await db.execute(
            "INSERT INTO facts (owner_id, text, kind, status, visibility, "
            "created_at, updated_at, last_cited_at) VALUES "
            "(1, 'Had a fight about dishes', 'working', 'outdated', 'shared', "
            "datetime('now', '-40 days'), datetime('now', '-40 days'), "
            "datetime('now', '-35 days'))"
        )
        await db.execute(
            "INSERT INTO facts (owner_id, text, kind, status, visibility, "
            "created_at, updated_at) VALUES "
            "(1, 'Married for 8 years', 'core', 'active', 'shared', "
            "datetime('now', '-100 days'), datetime('now', '-100 days'))"
        )
        # Bob's shared facts (visible to Alice's nudges)
        await db.execute(
            "INSERT INTO facts (owner_id, text, kind, status, visibility, "
            "created_at, updated_at) VALUES "
            "(2, 'Stressed about work deadline', 'working', 'active', 'shared', "
            "datetime('now', '-2 days'), datetime('now', '-2 days'))"
        )
        await db.execute(
            "INSERT INTO facts (owner_id, text, kind, status, visibility, "
            "created_at, updated_at) VALUES "
            "(2, 'Wants to plan a date night', 'working', 'active', 'shared', "
            "datetime('now', '-1 day'), datetime('now', '-1 day'))"
        )
        await db.commit()
    return db_path


class TestLifecycleAging:
    async def test_aging_does_not_outdate_working_facts(self, seeded_db):
        """Working facts should NOT be auto-outdated. Summaries handle implicit memory."""
        api = MemoryAPI(seeded_db)
        result = await api.run_aging()
        assert result.marked_outdated == 0

    async def test_core_facts_never_touched_by_aging(self, seeded_db):
        """Core facts should never be aged, regardless of citation."""
        api = MemoryAPI(seeded_db)
        await api.run_aging()

        async with aiosqlite.connect(seeded_db) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT status FROM facts WHERE text LIKE '%Married%'"
            )
            core = dict(await cur.fetchone())
            assert core["status"] == "active"

    async def test_citations_tracked(self, seeded_db):
        """Citation count and last_cited_at should be updated."""
        api = MemoryAPI(seeded_db)

        async with aiosqlite.connect(seeded_db) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id FROM facts WHERE text LIKE '%Italian%'"
            )
            fact_id = (await cur.fetchone())["id"]

        await api._record_citations([fact_id])

        async with aiosqlite.connect(seeded_db) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT citation_count, last_cited_at FROM facts WHERE id = ?",
                (fact_id,),
            )
            updated = dict(await cur.fetchone())
            assert updated["citation_count"] >= 1
            assert updated["last_cited_at"] is not None


class TestCamouflageQuestions:
    def test_load_question_bank(self):
        bank = load_question_bank()
        assert len(bank) >= 5  # at least 5 categories
        for cat, questions in bank.items():
            assert len(questions) >= 3

    async def test_get_camouflage_questions(self, seeded_db):
        questions = await get_camouflage_questions(seeded_db, 1, count=3)
        assert len(questions) == 3
        for q in questions:
            assert q.endswith("?")

    async def test_question_count_scales_with_activity(self, seeded_db):
        """Quiet users get fewer questions."""
        from pairtner.features.nudge_engine import questions_for_activity
        import random as stdlib_random
        rng = stdlib_random.Random(42)
        assert questions_for_activity(2, rng) == 1  # quiet
        assert questions_for_activity(15, rng) >= 2  # normal
        assert questions_for_activity(50, rng) >= 3  # active


class TestPartnerInformedQuestions:
    async def test_generate_with_mock_llm(self, seeded_db):
        """Partner-informed questions use partner's shared facts."""
        async def mock_llm(system: str, user: str) -> str:
            # Verify the prompt contains partner facts
            assert "Stressed about work" in user
            assert "date night" in user
            return (
                "How's work been treating you lately?\n"
                "Have you and your partner done anything fun recently?\n"
                "What's been on your mind this week?"
            )

        questions = await generate_partner_informed_questions(
            seeded_db, 1, 2, mock_llm, count=3,
        )
        assert len(questions) == 3
        assert "work" in questions[0].lower()

    async def test_no_partner_facts_returns_empty(self, db_path):
        """If partner has no shared facts, return empty list."""
        from pairtner import db as db_mod
        await db_mod.create_user(db_path, 1, "Alice", "en")
        await db_mod.create_user(db_path, 2, "Bob", "en")

        async def mock_llm(system, user):
            raise AssertionError("should not be called")

        questions = await generate_partner_informed_questions(
            db_path, 1, 2, mock_llm, count=3,
        )
        assert questions == []

    async def test_llm_failure_returns_empty(self, seeded_db):
        """LLM failures gracefully return empty list."""
        async def failing_llm(system, user):
            raise RuntimeError("LLM unavailable")

        questions = await generate_partner_informed_questions(
            seeded_db, 1, 2, failing_llm, count=3,
        )
        assert questions == []

    async def test_mixed_questions_includes_both(self, seeded_db):
        """get_mixed_questions should include both informed and generic."""
        async def mock_llm(system, user):
            return "How's work been?\nPlanning anything fun this weekend?"

        mixed = await get_mixed_questions(
            seeded_db, 1, partner_id=2, llm_fn=mock_llm,
            total=5, rng=__import__("random").Random(42),
        )
        assert len(mixed) == 5
        # At least some should be from the generic bank (end with ?)
        assert any(q.endswith("?") for q in mixed)

    async def test_mixed_questions_without_partner(self, seeded_db):
        """Without partner_id, all questions are generic."""
        mixed = await get_mixed_questions(
            seeded_db, 1, total=3,
            rng=__import__("random").Random(42),
        )
        assert len(mixed) == 3
