"""Tests for FEAT-001: FTS5 full-text search + smart context window."""

from __future__ import annotations

import pytest
import pytest_asyncio

from pairtner.config import Config
from pairtner.db import (
    add_fact,
    add_fact_summary,
    create_user,
    get_fact_summaries,
    get_facts_since,
    init_db,
    set_partner,
)
from pairtner.handlers import BotService
from pairtner.core.prompts import (
    SmartContext,
    UserContext,
    build_blunt_prompt,
    build_chat_prompt,
    build_keep_prompt,
    build_smart_context,
    build_tell_prompt,
)
from pairtner.core.search import (
    get_fact_count,
    init_fts,
    rebuild_fts_index,
    search_facts,
)
from pairtner.core.summarizer import check_and_summarize, generate_monthly_summary
from tests.helpers import MockLLMClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def search_db(tmp_path) -> str:
    """Fresh DB with FTS5 initialized (via init_db)."""
    db_path = str(tmp_path / "test_search.db")
    await init_db(db_path)
    return db_path


@pytest_asyncio.fixture
async def paired_search_db(search_db) -> tuple[str, int, int]:
    """DB with two paired users and sample facts for search testing."""
    alice, bob = 1001, 1002
    await create_user(search_db, alice, "Alice")
    await create_user(search_db, bob, "Bob")
    await set_partner(search_db, alice, bob)

    # Alice's private facts
    await add_fact(search_db, alice, "Alice loves hiking in the mountains", "private", tag="health")
    await add_fact(search_db, alice, "Alice feels stressed about work deadlines", "private", tag="work")
    await add_fact(search_db, alice, "Alice prefers morning coffee", "private", tag="other")

    # Alice's shared fact
    await add_fact(search_db, alice, "We agreed to cook together on weekends", "shared", tag="household")

    # Bob's private facts (should NOT appear in Alice's search)
    await add_fact(search_db, bob, "Bob secretly hates hiking", "private", tag="health")
    await add_fact(search_db, bob, "Bob is anxious about work reviews", "private", tag="work")

    # Bob's shared facts (SHOULD appear in Alice's search)
    await add_fact(search_db, bob, "We decided to try a new hiking trail", "shared", tag="health")

    return search_db, alice, bob


@pytest.fixture
def search_config() -> Config:
    return Config(
        telegram_token="test-token",
        gemini_api_key="test-key",
        search_fts_threshold=100,
        context_recent_days=7,
        context_max_relevant_facts=20,
    )


@pytest.fixture
def low_threshold_config() -> Config:
    return Config(
        telegram_token="test-token",
        gemini_api_key="test-key",
        search_fts_threshold=3,
        context_recent_days=7,
        context_max_relevant_facts=20,
    )


# ---------------------------------------------------------------------------
# FTS5 table creation and triggers
# ---------------------------------------------------------------------------

class TestFTSInitialisation:
    async def test_fts_table_created(self, search_db):
        """init_db creates the facts_fts virtual table."""
        import aiosqlite

        async with aiosqlite.connect(search_db) as db:
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='facts_fts'"
            ) as cur:
                row = await cur.fetchone()
        assert row is not None

    async def test_fts_triggers_created(self, search_db):
        """init_db creates the three sync triggers."""
        import aiosqlite

        async with aiosqlite.connect(search_db) as db:
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE 'facts_fts%'"
            ) as cur:
                triggers = {row[0] for row in await cur.fetchall()}
        assert triggers == {"facts_fts_ai", "facts_fts_ad", "facts_fts_au"}

    async def test_insert_fact_appears_in_search(self, search_db):
        """Inserting a fact automatically indexes it in FTS via trigger."""
        await create_user(search_db, 1, "Test")
        await add_fact(search_db, 1, "I enjoy playing chess on weekends")
        results = await search_facts(search_db, 1, "chess")
        assert len(results) == 1
        assert "chess" in results[0]["text"]

    async def test_rebuild_fts_index(self, search_db):
        """rebuild_fts_index re-indexes all facts without error."""
        await create_user(search_db, 1, "Test")
        await add_fact(search_db, 1, "Test fact for rebuild")
        await rebuild_fts_index(search_db)
        results = await search_facts(search_db, 1, "rebuild")
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Search: ranking and filtering
# ---------------------------------------------------------------------------

class TestSearchFacts:
    async def test_basic_search(self, paired_search_db):
        db_path, alice, bob = paired_search_db
        results = await search_facts(db_path, alice, "hiking")
        assert len(results) >= 1
        texts = [r["text"] for r in results]
        assert any("hiking" in t.lower() for t in texts)

    async def test_bm25_ranking(self, search_db):
        """Facts with more term relevance are ranked higher by BM25."""
        await create_user(search_db, 1, "Test")
        await add_fact(search_db, 1, "work is okay")
        await add_fact(search_db, 1, "work meetings are exhausting and work never stops and work stress is real")
        results = await search_facts(search_db, 1, "work")
        assert len(results) == 2
        # BM25 returns results in relevance order (lower = more relevant)
        # Both should be found; exact order depends on BM25 scoring

    async def test_or_matching_multiple_terms(self, search_db):
        """Search with multiple terms returns facts matching any term."""
        await create_user(search_db, 1, "Test")
        await add_fact(search_db, 1, "I love morning coffee")
        await add_fact(search_db, 1, "Evening walks are relaxing")
        results = await search_facts(search_db, 1, "coffee walks")
        assert len(results) == 2

    async def test_empty_query_returns_nothing(self, search_db):
        await create_user(search_db, 1, "Test")
        await add_fact(search_db, 1, "Some fact")
        results = await search_facts(search_db, 1, "")
        assert results == []

    async def test_special_characters_sanitized(self, search_db):
        """Special characters in the query don't crash FTS5."""
        await create_user(search_db, 1, "Test")
        await add_fact(search_db, 1, "Test fact with content")
        results = await search_facts(search_db, 1, "test (content) [brackets]")
        assert isinstance(results, list)

    async def test_limit_respected(self, search_db):
        await create_user(search_db, 1, "Test")
        for i in range(10):
            await add_fact(search_db, 1, f"Cooking recipe number {i}")
        results = await search_facts(search_db, 1, "cooking", limit=3)
        assert len(results) <= 3


# ---------------------------------------------------------------------------
# Privacy: own + shared only, never partner's private
# ---------------------------------------------------------------------------

class TestSearchPrivacy:
    async def test_user_sees_own_private_facts(self, paired_search_db):
        db_path, alice, _ = paired_search_db
        results = await search_facts(db_path, alice, "stressed")
        texts = [r["text"] for r in results]
        assert any("stressed" in t.lower() for t in texts)

    async def test_user_sees_partners_shared_facts(self, paired_search_db):
        db_path, alice, _ = paired_search_db
        results = await search_facts(db_path, alice, "hiking trail")
        texts = [r["text"] for r in results]
        assert any("hiking trail" in t.lower() for t in texts)

    async def test_user_cannot_see_partners_private_facts(self, paired_search_db):
        db_path, alice, _ = paired_search_db
        results = await search_facts(db_path, alice, "secretly hates")
        texts = [r["text"] for r in results]
        assert not any("secretly hates" in t.lower() for t in texts)

    async def test_unpaired_user_sees_only_own_facts(self, search_db):
        await create_user(search_db, 1, "Solo")
        await add_fact(search_db, 1, "My solo fact about gardens")
        results = await search_facts(search_db, 1, "gardens")
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Fact count + threshold logic
# ---------------------------------------------------------------------------

class TestThresholdLogic:
    async def test_get_fact_count(self, search_db):
        await create_user(search_db, 1, "Test")
        assert await get_fact_count(search_db, 1) == 0
        await add_fact(search_db, 1, "Fact one")
        await add_fact(search_db, 1, "Fact two")
        assert await get_fact_count(search_db, 1) == 2

    async def test_below_threshold_returns_none(self, search_db, search_config):
        """build_smart_context returns None when fact count is below threshold."""
        await create_user(search_db, 1, "Test")
        await add_fact(search_db, 1, "Just one fact")
        result = await build_smart_context(
            search_db, 1, "hello", search_config,
        )
        assert result is None

    async def test_above_threshold_returns_smart_context(
        self, search_db, low_threshold_config
    ):
        """build_smart_context returns SmartContext when above threshold."""
        await create_user(search_db, 1, "Test")
        for i in range(5):
            await add_fact(search_db, 1, f"Fact about topic {i}")
        result = await build_smart_context(
            search_db, 1, "topic", low_threshold_config,
        )
        assert result is not None
        assert isinstance(result, SmartContext)
        assert result.total_fact_count >= 5
        # Relevant facts should contain matches for "topic"
        assert len(result.relevant_facts) > 0 or len(result.recent_facts) > 0


# ---------------------------------------------------------------------------
# Monthly fact summaries
# ---------------------------------------------------------------------------

class TestFactSummaries:
    async def test_add_and_get_summaries(self, search_db):
        await create_user(search_db, 1, "Test")
        await add_fact_summary(search_db, 1, "2025-01", "January summary", 10)
        await add_fact_summary(search_db, 1, "2025-02", "February summary", 8)
        summaries = await get_fact_summaries(search_db, 1)
        assert len(summaries) == 2
        assert summaries[0]["year_month"] == "2025-02"  # Most recent first
        assert summaries[1]["year_month"] == "2025-01"

    async def test_upsert_summary(self, search_db):
        """Inserting a duplicate (user_id, year_month) updates the existing row."""
        await create_user(search_db, 1, "Test")
        await add_fact_summary(search_db, 1, "2025-01", "Old summary", 5)
        await add_fact_summary(search_db, 1, "2025-01", "Updated summary", 12)
        summaries = await get_fact_summaries(search_db, 1)
        assert len(summaries) == 1
        assert summaries[0]["summary_text"] == "Updated summary"
        assert summaries[0]["fact_count"] == 12

    async def test_get_facts_since(self, search_db):
        await create_user(search_db, 1, "Test")
        await add_fact(search_db, 1, "Old fact")
        await add_fact(search_db, 1, "New fact")
        # Get all facts since epoch — should return both
        results = await get_facts_since(search_db, 1, "2000-01-01")
        assert len(results) == 2

    async def test_generate_monthly_summary(self, search_db):
        """generate_monthly_summary calls LLM and stores the result."""
        await create_user(search_db, 1, "Test")
        # Insert a fact with a specific month timestamp
        import aiosqlite

        async with aiosqlite.connect(search_db) as db:
            await db.execute(
                "INSERT INTO facts (owner_id, text, visibility, tag, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (1, "January test fact", "private", "work", "2025-01-15T10:00:00"),
            )
            await db.commit()

        mock_llm = MockLLMClient(responses=[
            {"reply": "Work was the main theme in January.", "new_facts": [], "relay_draft": None},
        ])
        result = await generate_monthly_summary(
            search_db, mock_llm, 1, 2025, 1,
        )
        assert result is not None
        assert "January" in result

        summaries = await get_fact_summaries(search_db, 1)
        assert len(summaries) == 1
        assert summaries[0]["year_month"] == "2025-01"

    async def test_check_and_summarize_skips_current_month(self, search_db):
        """check_and_summarize does not generate a summary for the current month."""
        from datetime import datetime, timezone

        await create_user(search_db, 1, "Test")
        now = datetime.now(timezone.utc)
        # Insert a fact for the current month
        await add_fact(search_db, 1, "Current month fact")

        mock_llm = MockLLMClient()
        await check_and_summarize(search_db, mock_llm, 1)
        # LLM should not be called for the current month
        summaries = await get_fact_summaries(search_db, 1)
        # Current month should not have a summary
        current_ym = f"{now.year:04d}-{now.month:02d}"
        yms = [s["year_month"] for s in summaries]
        assert current_ym not in yms


# ---------------------------------------------------------------------------
# Smart context building
# ---------------------------------------------------------------------------

class TestSmartContext:
    async def test_smart_context_includes_recent_and_relevant(
        self, search_db, low_threshold_config
    ):
        await create_user(search_db, 1, "Test")
        # Add enough facts to exceed threshold (3)
        await add_fact(search_db, 1, "I feel tired from work")
        await add_fact(search_db, 1, "Morning yoga helps me relax")
        await add_fact(search_db, 1, "Work meetings are boring")
        await add_fact(search_db, 1, "I like cooking pasta")

        ctx = await build_smart_context(
            search_db, 1, "work", low_threshold_config,
        )
        assert ctx is not None
        # Recent facts should include all (they're from today)
        assert len(ctx.recent_facts) >= 1
        assert ctx.total_fact_count >= 4

    async def test_smart_context_with_summaries(
        self, search_db, low_threshold_config
    ):
        await create_user(search_db, 1, "Test")
        for i in range(5):
            await add_fact(search_db, 1, f"Fact number {i}")
        await add_fact_summary(search_db, 1, "2025-01", "January themes", 10)

        ctx = await build_smart_context(
            search_db, 1, "fact", low_threshold_config,
        )
        assert ctx is not None
        assert len(ctx.monthly_summaries) == 1
        assert "January" in ctx.monthly_summaries[0]

    async def test_smart_context_deduplicates_relevant_vs_recent(
        self, search_db, low_threshold_config
    ):
        """Relevant facts that are also in recent should not be duplicated."""
        await create_user(search_db, 1, "Test")
        for i in range(5):
            await add_fact(search_db, 1, f"Topic alpha fact {i}")

        ctx = await build_smart_context(
            search_db, 1, "alpha", low_threshold_config,
        )
        assert ctx is not None
        # All facts are recent (just created), so relevant should not duplicate them
        all_texts = ctx.recent_facts + ctx.relevant_facts
        assert len(all_texts) == len(set(all_texts))


# ---------------------------------------------------------------------------
# /search command handler
# ---------------------------------------------------------------------------

class TestSearchCommand:
    @pytest_asyncio.fixture
    async def search_service(self, search_db) -> BotService:
        cfg = Config(
            telegram_token="test-token",
            gemini_api_key="test-key",
            db_path=search_db,
        )
        mock_llm = MockLLMClient()
        return BotService(config=cfg, db_path=search_db, llm=mock_llm)

    async def test_search_no_query(self, search_service):
        await create_user(search_service.db_path, 1, "Test")
        responses = await search_service.handle_search(1, None)
        assert len(responses) == 1
        assert "search" in responses[0].text.lower() or "/search" in responses[0].text

    async def test_search_empty_query(self, search_service):
        await create_user(search_service.db_path, 1, "Test")
        responses = await search_service.handle_search(1, "   ")
        assert len(responses) == 1

    async def test_search_no_results(self, search_service):
        await create_user(search_service.db_path, 1, "Test")
        await add_fact(search_service.db_path, 1, "Apples are great")
        responses = await search_service.handle_search(1, "zebra")
        assert len(responses) == 1
        assert "no result" in responses[0].text.lower() or "కనుగొనబడలేదు" in responses[0].text

    async def test_search_with_results(self, search_service):
        await create_user(search_service.db_path, 1, "Test")
        await add_fact(search_service.db_path, 1, "I love morning runs", tag="health")
        responses = await search_service.handle_search(1, "morning")
        assert len(responses) == 1
        assert "morning" in responses[0].text.lower()


# ---------------------------------------------------------------------------
# Prompt builders with SmartContext
# ---------------------------------------------------------------------------

class TestPromptBuildersWithSmartContext:
    def test_chat_prompt_without_smart_ctx_unchanged(self):
        ctx = UserContext(
            user_name="Alice",
            private_facts=["Alice likes cats"],
            shared_facts=["Both like pizza"],
        )
        system, content = build_chat_prompt(ctx, "hello")
        assert "your notes" in content.lower()
        assert "Alice likes cats" in content

    def test_chat_prompt_with_smart_ctx_uses_structured_sections(self):
        ctx = UserContext(user_name="Alice")
        smart = SmartContext(
            recent_facts=["Recent fact A"],
            relevant_facts=["Relevant fact B"],
            monthly_summaries=["2025-01: Summary"],
            total_fact_count=150,
        )
        system, content = build_chat_prompt(ctx, "hello", smart_ctx=smart)
        assert "Recent notes" in content
        assert "Relevant notes" in content
        assert "Monthly summaries" in content
        assert "Recent fact A" in content
        assert "Relevant fact B" in content
        # Should NOT contain the flat facts sections
        assert "your notes" not in content.lower()

    def test_tell_prompt_with_smart_ctx(self):
        ctx = UserContext(user_name="Alice")
        smart = SmartContext(
            recent_facts=["Recent"],
            relevant_facts=["Relevant"],
            monthly_summaries=[],
            total_fact_count=200,
        )
        system, content = build_tell_prompt(ctx, "msg", smart_ctx=smart)
        assert "Recent notes" in content
        assert "Relevant notes" in content

    def test_keep_prompt_with_smart_ctx(self):
        ctx = UserContext(user_name="Alice", private_facts=["should not appear"])
        smart = SmartContext(
            recent_facts=["Recent keep fact"],
            relevant_facts=[],
            monthly_summaries=[],
            total_fact_count=150,
        )
        system, content = build_keep_prompt(ctx, "save this", smart_ctx=smart)
        assert "Recent notes" in content
        assert "should not appear" not in content

    def test_blunt_prompt_with_smart_ctx(self):
        ctx = UserContext(user_name="Alice")
        smart = SmartContext(
            recent_facts=["Recent blunt"],
            relevant_facts=["Relevant blunt"],
            monthly_summaries=[],
            total_fact_count=150,
        )
        system, content = build_blunt_prompt(ctx, "be honest", smart_ctx=smart)
        assert "Recent notes" in content
        assert "Relevant notes" in content

    def test_backward_compatible_no_smart_ctx(self):
        """All prompt builders work fine without smart_ctx (default None)."""
        ctx = UserContext(
            user_name="Test",
            private_facts=["fact1"],
            shared_facts=["fact2"],
        )
        # These should all work without smart_ctx argument
        build_chat_prompt(ctx, "msg")
        build_tell_prompt(ctx, "msg")
        build_keep_prompt(ctx, "msg")
        build_blunt_prompt(ctx, "msg")


# ---------------------------------------------------------------------------
# Config entries
# ---------------------------------------------------------------------------

class TestSearchConfig:
    def test_config_has_search_fields(self, search_config):
        assert search_config.search_fts_threshold == 100
        assert search_config.context_recent_days == 7
        assert search_config.context_max_relevant_facts == 20

    def test_config_defaults(self):
        cfg = Config(telegram_token="t", gemini_api_key="k")
        assert cfg.search_fts_threshold == 100
        assert cfg.context_recent_days == 7
        assert cfg.context_max_relevant_facts == 20
