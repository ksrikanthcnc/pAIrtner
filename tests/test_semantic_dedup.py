"""Tests for semantic dedup and contradiction detection."""
import pytest
from pairtner.core.memory import (
    is_near_duplicate,
    detect_contradiction,
    find_semantic_duplicates,
    parse_memory_ops,
    VALID_KINDS,
)


class TestFactKinds:
    def test_valid_kinds(self):
        assert "working" in VALID_KINDS
        assert "core" in VALID_KINDS

    def test_unknown_kind_defaults_to_working(self):
        ops = parse_memory_ops(
            [{"op": "add", "text": "some fact", "kind": "bogus"}],
            set(), v2=True,
        )
        assert len(ops) == 1
        assert ops[0].kind == "working"


class TestContradictionDetection:
    def test_opposite_sentiment_same_subject(self):
        assert detect_contradiction(
            "Loves cooking Italian food",
            "Hates cooking Italian food",
        )

    def test_no_contradiction_different_subject(self):
        assert not detect_contradiction(
            "Loves cooking Italian food",
            "Hates doing laundry every weekend",
        )

    def test_no_contradiction_same_sentiment(self):
        assert not detect_contradiction(
            "Loves cooking Italian food",
            "Enjoys cooking Italian food",
        )

    def test_no_contradiction_unrelated(self):
        assert not detect_contradiction(
            "Works at a tech company downtown",
            "Has two kids in primary school",
        )

    def test_contradiction_negative_to_positive(self):
        assert detect_contradiction(
            "Feels happy about the new promotion",
            "Feels frustrated about the new promotion",
        )

    def test_short_texts_no_overlap(self):
        """Short unrelated texts should not trigger."""
        assert not detect_contradiction("Sad", "Happy")


class TestSemanticDedup:
    @pytest.fixture
    def db_path(self, tmp_path):
        return str(tmp_path / "test.db")

    @pytest.mark.asyncio
    async def test_empty_db_returns_empty(self, db_path):
        import aiosqlite
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "CREATE TABLE facts "
                "(id INTEGER PRIMARY KEY, owner_id INTEGER, text TEXT, "
                "kind TEXT DEFAULT 'working', status TEXT DEFAULT 'active', "
                "archived_at TEXT)"
            )
            await db.commit()
        result = await find_semantic_duplicates(db_path, "some text", 1)
        assert result == []

    @pytest.mark.asyncio
    async def test_finds_similar_fact(self, db_path):
        import aiosqlite
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "CREATE TABLE facts "
                "(id INTEGER PRIMARY KEY, owner_id INTEGER, text TEXT, "
                "kind TEXT DEFAULT 'working', status TEXT DEFAULT 'active', "
                "archived_at TEXT)"
            )
            await db.execute(
                "INSERT INTO facts (owner_id, text) "
                "VALUES (1, 'Feels stressed about work deadlines')"
            )
            await db.commit()
        result = await find_semantic_duplicates(
            db_path, "Feeling stressed about work deadline pressure", 1,
            threshold=0.5,
        )
        assert len(result) >= 1
        assert result[0]["similarity"] >= 0.5

    @pytest.mark.asyncio
    async def test_ignores_other_users_facts(self, db_path):
        import aiosqlite
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "CREATE TABLE facts "
                "(id INTEGER PRIMARY KEY, owner_id INTEGER, text TEXT, "
                "kind TEXT DEFAULT 'working', status TEXT DEFAULT 'active', "
                "archived_at TEXT)"
            )
            await db.execute(
                "INSERT INTO facts (owner_id, text) "
                "VALUES (2, 'Feels stressed about work deadlines')"
            )
            await db.commit()
        result = await find_semantic_duplicates(
            db_path, "Feels stressed about work deadlines", 1,
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_ignores_archived_facts(self, db_path):
        import aiosqlite
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "CREATE TABLE facts "
                "(id INTEGER PRIMARY KEY, owner_id INTEGER, text TEXT, "
                "kind TEXT DEFAULT 'working', status TEXT DEFAULT 'active', "
                "archived_at TEXT)"
            )
            await db.execute(
                "INSERT INTO facts (owner_id, text, archived_at) "
                "VALUES (1, 'Feels stressed about work', '2026-01-01')"
            )
            await db.commit()
        result = await find_semantic_duplicates(
            db_path, "Feels stressed about work", 1,
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_stopwords_only_returns_empty(self, db_path):
        import aiosqlite
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "CREATE TABLE facts "
                "(id INTEGER PRIMARY KEY, owner_id INTEGER, text TEXT, "
                "kind TEXT DEFAULT 'working', status TEXT DEFAULT 'active', "
                "archived_at TEXT)"
            )
            await db.commit()
        # All stopwords — no keywords to search
        result = await find_semantic_duplicates(db_path, "is the it", 1)
        assert result == []
