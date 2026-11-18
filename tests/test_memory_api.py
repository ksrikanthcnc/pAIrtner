"""Tests for the Memory API (pairtner.core.memory_api)."""

from __future__ import annotations

import math
import pytest
from datetime import datetime, timedelta, timezone

from pairtner.core.memory_api import (
    MemoryAPI,
    Fact,
    FactList,
    MemoryStats,
    RecencyReport,
    Provenance,
    ContextResult,
    compute_relevance_score,
    _age_in_days,
    _row_to_fact,
)
from pairtner.storage.helpers import _now_iso, _to_iso


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db(tmp_path):
    """Fresh DB with a test user."""
    from pairtner.db import init_db, create_user

    path = str(tmp_path / "test.db")
    await init_db(path)
    await create_user(path, 1, "Alice")
    return path


@pytest.fixture
def api(db):
    return MemoryAPI(db)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


class TestScoring:
    def test_core_always_1(self):
        row = {"kind": "core", "status": "active", "citation_count": 0, "created_at": "2020-01-01T00:00:00"}
        assert compute_relevance_score(row) == 1.0

    def test_core_score_unaffected_by_age(self):
        old = {"kind": "core", "status": "active", "citation_count": 0, "created_at": "2020-01-01T00:00:00"}
        new = {"kind": "core", "status": "active", "citation_count": 0, "created_at": _now_iso()}
        assert compute_relevance_score(old) == compute_relevance_score(new) == 1.0

    def test_working_decays_with_age(self):
        recent = {"kind": "working", "status": "active", "citation_count": 0, "created_at": _now_iso()}
        old_dt = datetime.now(timezone.utc) - timedelta(days=42)
        old = {"kind": "working", "status": "active", "citation_count": 0, "created_at": _to_iso(old_dt)}
        assert compute_relevance_score(recent) > compute_relevance_score(old)

    def test_citation_boost(self):
        base = {"kind": "working", "status": "active", "citation_count": 0, "created_at": _now_iso()}
        cited = {"kind": "working", "status": "active", "citation_count": 5, "created_at": _now_iso()}
        assert compute_relevance_score(cited) > compute_relevance_score(base)

    def test_citation_boost_caps_at_1_5(self):
        row = {"kind": "working", "status": "active", "citation_count": 100, "created_at": _now_iso()}
        # Even with 100 citations, score can't exceed 1.0 (capped)
        assert compute_relevance_score(row) <= 1.0

    def test_status_weight(self):
        active = {"kind": "working", "status": "active", "citation_count": 0, "created_at": _now_iso()}
        resolved = {"kind": "working", "status": "resolved", "citation_count": 0, "created_at": _now_iso()}
        outdated = {"kind": "working", "status": "outdated", "citation_count": 0, "created_at": _now_iso()}
        assert compute_relevance_score(active) > compute_relevance_score(resolved)
        assert compute_relevance_score(resolved) > compute_relevance_score(outdated)

    def test_episode_kind_weight(self):
        episode = {"kind": "episode", "status": "active", "citation_count": 0, "created_at": _now_iso()}
        working = {"kind": "working", "status": "active", "citation_count": 0, "created_at": _now_iso()}
        assert compute_relevance_score(episode) > compute_relevance_score(working)

    def test_age_in_days_empty(self):
        assert _age_in_days("") == 0.0

    def test_age_in_days_valid(self):
        yesterday = _to_iso(datetime.now(timezone.utc) - timedelta(days=1))
        age = _age_in_days(yesterday)
        assert 0.9 < age < 1.1


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestCRUD:
    @pytest.mark.asyncio
    async def test_add_fact(self, api, db):
        fact = await api.add_fact(1, "Likes pizza")
        assert fact.id > 0
        assert fact.text == "Likes pizza"
        assert fact.kind == "working"
        assert fact.relevance_score > 0

    @pytest.mark.asyncio
    async def test_add_fact_with_source(self, api, db):
        from pairtner.storage.messages import log_message

        msg_id = await log_message(db, 1, "user", "I love pizza")
        fact = await api.add_fact(1, "Likes pizza", source_message_id=msg_id)
        assert fact.source_message_id == msg_id

    @pytest.mark.asyncio
    async def test_get_fact(self, api, db):
        created = await api.add_fact(1, "Test fact")
        fetched = await api.get_fact(created.id)
        assert fetched is not None
        assert fetched.text == "Test fact"

    @pytest.mark.asyncio
    async def test_get_fact_not_found(self, api):
        assert await api.get_fact(99999) is None

    @pytest.mark.asyncio
    async def test_update_text(self, api, db):
        fact = await api.add_fact(1, "Son is 6")
        updated = await api.update_text(fact.id, "Son is 7")
        assert updated is not None
        assert updated.text == "Son is 7"

    @pytest.mark.asyncio
    async def test_set_status(self, api, db):
        fact = await api.add_fact(1, "Big deadline Friday")
        updated = await api.set_status(fact.id, "resolved", reason="deadline passed")
        assert updated is not None
        assert updated.status == "resolved"

    @pytest.mark.asyncio
    async def test_promote_to_core(self, api, db):
        fact = await api.add_fact(1, "Introvert")
        promoted = await api.promote_to_core(fact.id)
        assert promoted is not None
        assert promoted.kind == "core"
        assert promoted.relevance_score == 1.0

    @pytest.mark.asyncio
    async def test_archive(self, api, db):
        fact = await api.add_fact(1, "Old fact")
        archived = await api.archive(fact.id, reason="stale")
        assert archived is not None

    @pytest.mark.asyncio
    async def test_delete(self, api, db):
        fact = await api.add_fact(1, "To delete")
        assert await api.delete(fact.id, 1) is True
        assert await api.get_fact(fact.id) is None

    @pytest.mark.asyncio
    async def test_delete_wrong_owner(self, api, db):
        fact = await api.add_fact(1, "Not yours")
        assert await api.delete(fact.id, 999) is False


# ---------------------------------------------------------------------------
# List & search
# ---------------------------------------------------------------------------


class TestListSearch:
    @pytest.mark.asyncio
    async def test_list_facts(self, api, db):
        await api.add_fact(1, "Fact A")
        await api.add_fact(1, "Fact B")
        result = await api.list_facts(1)
        assert result.total == 2
        assert len(result.facts) == 2

    @pytest.mark.asyncio
    async def test_list_facts_by_kind(self, api, db):
        await api.add_fact(1, "Working fact")
        core = await api.add_fact(1, "Core fact")
        await api.promote_to_core(core.id)
        result = await api.list_facts(1, kind="core")
        assert result.total == 1
        assert result.facts[0].kind == "core"

    @pytest.mark.asyncio
    async def test_list_facts_scored(self, api, db):
        f1 = await api.add_fact(1, "Older fact")
        f2 = await api.add_fact(1, "New core")
        await api.promote_to_core(f2.id)
        result = await api.list_facts(1, scored=True)
        # Core should be first (score 1.0)
        assert result.facts[0].kind == "core"

    @pytest.mark.asyncio
    async def test_list_pagination(self, api, db):
        for i in range(5):
            await api.add_fact(1, f"Fact {i}")
        page1 = await api.list_facts(1, limit=2, offset=0)
        page2 = await api.list_facts(1, limit=2, offset=2)
        assert len(page1.facts) == 2
        assert len(page2.facts) == 2
        assert page1.facts[0].id != page2.facts[0].id


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


class TestProvenance:
    @pytest.mark.asyncio
    async def test_provenance_with_source(self, api, db):
        from pairtner.storage.messages import log_message

        msg_id = await log_message(db, 1, "user", "I'm married for 6 years")
        fact = await api.add_fact(1, "Married 6 years", source_message_id=msg_id)
        prov = await api.get_provenance(fact.id)
        assert prov is not None
        assert prov.source_message == "I'm married for 6 years"
        assert prov.version_count >= 1

    @pytest.mark.asyncio
    async def test_provenance_without_source(self, api, db):
        fact = await api.add_fact(1, "Some fact")
        prov = await api.get_provenance(fact.id)
        assert prov is not None
        assert prov.source_message is None

    @pytest.mark.asyncio
    async def test_provenance_not_found(self, api):
        assert await api.get_provenance(99999) is None


# ---------------------------------------------------------------------------
# Stats & health
# ---------------------------------------------------------------------------


class TestStats:
    @pytest.mark.asyncio
    async def test_empty_stats(self, api):
        stats = await api.get_stats(1)
        assert stats.total == 0
        assert stats.avg_score == 0.0

    @pytest.mark.asyncio
    async def test_stats_counts(self, api, db):
        await api.add_fact(1, "A")
        await api.add_fact(1, "B")
        f3 = await api.add_fact(1, "C")
        await api.promote_to_core(f3.id)
        stats = await api.get_stats(1)
        assert stats.total == 3
        assert stats.by_kind.get("core") == 1
        assert stats.by_kind.get("working") == 2

    @pytest.mark.asyncio
    async def test_recency_report(self, api, db):
        await api.add_fact(1, "Recent")
        report = await api.get_recency_report(1)
        assert report.healthy == 1  # just created = within 7 days
        assert report.contradictions == 0

    @pytest.mark.asyncio
    async def test_stats_to_dict(self, api, db):
        stats = await api.get_stats(1)
        d = stats.to_dict()
        assert "total" in d
        assert "by_kind" in d


# ---------------------------------------------------------------------------
# Scored context
# ---------------------------------------------------------------------------


class TestScoredContext:
    @pytest.mark.asyncio
    async def test_empty_context(self, api):
        ctx = await api.get_scored_context(1)
        assert ctx.lines == []
        assert ctx.facts_included == 0

    @pytest.mark.asyncio
    async def test_core_first(self, api, db):
        await api.add_fact(1, "Working fact")
        core = await api.add_fact(1, "Core identity")
        await api.promote_to_core(core.id)
        ctx = await api.get_scored_context(1)
        assert "(core)" in ctx.lines[0]

    @pytest.mark.asyncio
    async def test_budget_limit(self, api, db):
        for i in range(20):
            await api.add_fact(1, f"Fact number {i} with some extra words to fill budget")
        ctx = await api.get_scored_context(1, budget_words=50)
        assert ctx.total_words <= 70  # some slack for the first item

    @pytest.mark.asyncio
    async def test_citations_increment(self, api, db):
        fact = await api.add_fact(1, "Cited fact")
        assert fact.citation_count == 0
        await api.get_scored_context(1)
        updated = await api.get_fact(fact.id)
        assert updated is not None
        assert updated.citation_count == 1


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TestSerialization:
    @pytest.mark.asyncio
    async def test_fact_to_dict(self, api, db):
        fact = await api.add_fact(1, "Test")
        d = fact.to_dict()
        assert d["id"] == fact.id
        assert d["text"] == "Test"
        assert "relevance_score" in d

    @pytest.mark.asyncio
    async def test_fact_list_to_dict(self, api, db):
        await api.add_fact(1, "A")
        result = await api.list_facts(1)
        d = result.to_dict()
        assert "facts" in d
        assert "total" in d

    @pytest.mark.asyncio
    async def test_recency_to_dict(self, api, db):
        report = await api.get_recency_report(1)
        d = report.to_dict()
        assert "healthy" in d
        assert "stale" in d
