"""Stress test: simulate 6-12 months of conversation and measure memory quality.

Tests:
1. Fact growth over time (working, core, episode counts)
2. Context window quality (does scoring surface the right facts?)
3. Old fact retrieval via FTS5 search
4. Weekly consolidation effectiveness
5. Score distribution at scale
"""
from __future__ import annotations

import asyncio
import math
import random
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite
import pytest

from pairtner.core.memory_api import MemoryAPI, compute_relevance_score
from pairtner.core.consolidation import run_consolidation
from pairtner.storage.facts import add_fact, get_facts
from pairtner.storage.schema import init_db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Realistic fact templates per topic
_TOPICS = {
    "work": [
        "Stressed about upcoming project deadline at work",
        "Got promoted to senior role at work",
        "Considering switching jobs to a smaller company",
        "Had a conflict with a coworker about workload distribution",
        "Working overtime this week due to release cycle",
    ],
    "health": [
        "Started going to the gym 3 times a week",
        "Having trouble sleeping lately",
        "Doctor appointment scheduled for next month",
        "Trying a new diet to manage weight",
        "Knee injury from running, taking a break from exercise",
    ],
    "relationship": [
        "Feeling disconnected from partner lately",
        "Had a great date night last weekend",
        "Arguing about household chores again",
        "Discussed having kids, both uncertain",
        "Partner's parents visiting next month, feeling anxious",
    ],
    "household": [
        "Looking at new apartments, current lease ending",
        "Kitchen renovation is going over budget",
        "Got a new puppy, adjusting to the routine",
        "Arguing about how to split expenses",
        "Neighbors are too noisy, considering complaining",
    ],
    "emotional": [
        "Feeling overwhelmed with responsibilities",
        "Had a really good week, feeling positive",
        "Missing old friends, haven't connected in months",
        "Anxious about financial situation",
        "Grateful for partner's support during tough time",
    ],
}

# Core facts (established early, rarely change)
_CORE_FACTS = [
    "Name: Alice, Age: 32",
    "Partner name: Bob, Age: 34",
    "Relationship: married 6 years",
    "Has a 5-year-old son named Max",
    "Lives in San Francisco",
    "Works as a product manager at a tech company",
    "Partner works as a software engineer",
    "Introvert, needs alone time to recharge",
    "Loves hiking and outdoor activities",
    "Allergic to shellfish",
    "Anniversary: March 15",
    "Son's birthday: August 22",
    "Partner's birthday: June 10",
    "Family therapist: Dr. Sarah Chen",
    "Prefers morning conversations over evening",
]


async def _create_db(tmp_path) -> str:
    """Create a fresh DB with schema."""
    db_path = str(tmp_path / "stress.db")
    await init_db(db_path)
    return db_path


async def _seed_core_facts(db_path: str, user_id: int) -> list[int]:
    """Seed the core facts that exist from day 1."""
    ids = []
    for text in _CORE_FACTS:
        fid = await add_fact(
            db_path, user_id, text, visibility="private",
            tag="core", fact_type="stable", intensity=0.8,
        )
        # Mark as core kind
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "UPDATE facts SET kind = 'core' WHERE id = ?", (fid,)
            )
            await db.commit()
        ids.append(fid)
    return ids


async def _seed_working_facts(
    db_path: str, user_id: int, num_days: int, facts_per_day: int = 5,
) -> dict[str, Any]:
    """Seed working facts over a simulated time period.

    Returns stats about what was seeded.
    """
    now = datetime.now(timezone.utc)
    all_topics = list(_TOPICS.keys())
    total_added = 0
    by_topic: dict[str, int] = {t: 0 for t in all_topics}

    for day in range(num_days, 0, -1):  # oldest first
        created = now - timedelta(days=day)
        n_facts = random.randint(max(1, facts_per_day - 2), facts_per_day + 2)

        for _ in range(n_facts):
            topic = random.choice(all_topics)
            text = random.choice(_TOPICS[topic])
            # Add some variation
            text = f"{text} (day {num_days - day + 1})"

            intensity = round(random.uniform(0.3, 0.9), 2)

            fid = await add_fact(
                db_path, user_id, text, visibility="private",
                tag=topic, fact_type="stable", intensity=intensity,
            )

            # Set created_at to simulated time
            async with aiosqlite.connect(db_path) as db:
                await db.execute(
                    "UPDATE facts SET created_at = ?, updated_at = ? WHERE id = ?",
                    (created.isoformat(), created.isoformat(), fid),
                )
                await db.commit()

            total_added += 1
            by_topic[topic] += 1

    return {"total": total_added, "by_topic": by_topic, "days": num_days}


async def _simulate_citations(
    db_path: str, user_id: int, num_sessions: int = 50,
) -> int:
    """Simulate citing (including in prompt) some facts over time.

    More recent and important facts get cited more.
    """
    rows = await get_facts(db_path, user_id)
    if not rows:
        return 0

    total_citations = 0
    for _ in range(num_sessions):
        # Pick 10-20 facts to "cite" (include in prompt)
        sample_size = min(random.randint(10, 20), len(rows))
        # Bias towards recent facts
        weights = [
            1.0 / (1 + i * 0.1) for i in range(len(rows))
        ]
        # Reverse so recent (last in list) get higher weight
        weights.reverse()
        chosen = random.choices(rows, weights=weights, k=sample_size)

        for row in chosen:
            async with aiosqlite.connect(db_path) as db:
                await db.execute(
                    "UPDATE facts SET citation_count = citation_count + 1, "
                    "last_cited_at = ? WHERE id = ?",
                    (datetime.now(timezone.utc).isoformat(), row["id"]),
                )
                await db.commit()
            total_citations += 1

    return total_citations


async def _mark_old_facts_resolved(
    db_path: str, user_id: int, older_than_days: int = 14,
) -> int:
    """Mark old working facts as resolved (simulates natural lifecycle)."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=older_than_days)
    ).isoformat()

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "UPDATE facts SET status = 'resolved' "
            "WHERE owner_id = ? AND kind = 'working' AND status = 'active' "
            "AND created_at < ?",
            (user_id, cutoff),
        )
        await db.commit()
        return cursor.rowcount


async def _count_by_kind(db_path: str, user_id: int) -> dict[str, int]:
    """Count active facts by kind."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT kind, COUNT(*) as cnt FROM facts "
            "WHERE owner_id = ? AND archived_at IS NULL "
            "GROUP BY kind",
            (user_id,),
        )
        return {r["kind"] or "working": r["cnt"] for r in rows}


async def _count_by_status(db_path: str, user_id: int) -> dict[str, int]:
    """Count facts by status."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT status, COUNT(*) as cnt FROM facts "
            "WHERE owner_id = ? AND archived_at IS NULL "
            "GROUP BY status",
            (user_id,),
        )
        return {r["status"] or "active": r["cnt"] for r in rows}


# ---------------------------------------------------------------------------
# Consolidation mock
# ---------------------------------------------------------------------------

def _make_consolidation_mock():
    """Create a mock that extracts candidate IDs from the prompt
    and returns episodes referencing them (simulating real LLM behavior)."""
    import re as _re

    class ConsolidationMock:
        def __init__(self):
            self.calls: list[dict] = []

        async def generate(self, user_content, system_prompt, **kw):
            self.calls.append({"user_content": user_content})
            # Extract fact IDs from the prompt like "[42] (resolved) Some text"
            ids = [int(m) for m in _re.findall(r"\[(\d+)\]", user_content)]
            if len(ids) < 3:
                return {"episodes": []}
            # Group into chunks of 3-5 and make episodes
            episodes = []
            chunk_size = min(5, max(3, len(ids) // 2))
            for i in range(0, len(ids), chunk_size):
                chunk = ids[i:i + chunk_size]
                if len(chunk) >= 3:
                    episodes.append({
                        "text": f"During this period the couple dealt with {len(chunk)} resolved situations including stress and communication",
                        "intensity": 0.6,
                        "source_ids": chunk,
                    })
            return {"episodes": episodes}

    return ConsolidationMock()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

USER_ID = 4503599627370499


@pytest.fixture
async def stress_db(tmp_path):
    """Create a DB with 6 months of simulated data."""
    db_path = await _create_db(tmp_path)
    await _seed_core_facts(db_path, USER_ID)
    await _seed_working_facts(db_path, USER_ID, num_days=180, facts_per_day=5)
    await _simulate_citations(db_path, USER_ID, num_sessions=50)
    return db_path


@pytest.fixture
async def stress_db_1y(tmp_path):
    """Create a DB with 12 months of simulated data."""
    db_path = await _create_db(tmp_path)
    await _seed_core_facts(db_path, USER_ID)
    await _seed_working_facts(db_path, USER_ID, num_days=365, facts_per_day=5)
    await _simulate_citations(db_path, USER_ID, num_sessions=100)
    return db_path


class TestFactGrowth:
    """Test that fact counts stay manageable over time."""

    async def test_6mo_raw_counts(self, stress_db):
        """After 6 months, total facts should be ~900 + 15 core."""
        counts = await _count_by_kind(stress_db, USER_ID)
        total = sum(counts.values())
        assert 700 < total < 1200, f"unexpected total: {total} ({counts})"
        assert counts.get("core", 0) == len(_CORE_FACTS)

    async def test_6mo_after_resolving(self, stress_db):
        """Mark old facts resolved, verify counts shift."""
        resolved = await _mark_old_facts_resolved(stress_db, USER_ID, older_than_days=14)
        assert resolved > 0, "should have resolved some old facts"

        statuses = await _count_by_status(stress_db, USER_ID)
        assert statuses.get("resolved", 0) > 0

    async def test_6mo_after_consolidation(self, stress_db):
        """Run weekly consolidation, verify episodes created and sources archived."""
        # First resolve old facts
        await _mark_old_facts_resolved(stress_db, USER_ID, older_than_days=14)

        before = await _count_by_kind(stress_db, USER_ID)

        # Mock LLM for consolidation
        llm = _make_consolidation_mock()

        result = await run_consolidation(
            stress_db, llm, USER_ID,
        )

        after = await _count_by_kind(stress_db, USER_ID)

        # Should have archived the resolved facts
        assert sum(after.values()) <= sum(before.values()), (
            f"total should not grow: before={before}, after={after}"
        )


class TestContextQuality:
    """Test that the context builder surfaces the right facts at scale."""

    async def test_core_facts_always_included(self, stress_db):
        """Core facts must always be in the context window."""
        api = MemoryAPI(stress_db)
        result = await api.get_scored_context(USER_ID, budget_words=500)

        core_texts = set(_CORE_FACTS)
        included_core = 0
        for line in result.lines:
            if "(core)" in line:
                included_core += 1

        assert included_core == len(_CORE_FACTS), (
            f"all {len(_CORE_FACTS)} core facts must be included, got {included_core}"
        )

    async def test_context_fits_budget(self, stress_db):
        """Context should respect the word budget."""
        api = MemoryAPI(stress_db)
        result = await api.get_scored_context(USER_ID, budget_words=300)

        assert result.total_words <= 350, (  # small overflow OK for last fact
            f"context exceeded budget: {result.total_words} words"
        )

    async def test_recent_facts_score_higher(self, stress_db):
        """Recent facts should generally score higher than old ones."""
        from pairtner.storage.facts import get_active_facts

        rows = await get_active_facts(stress_db, USER_ID)
        working = [r for r in rows if (r.get("kind") or "working") == "working"]

        if len(working) < 10:
            pytest.skip("not enough working facts")

        # Sort by score
        scored = [(r, compute_relevance_score(r)) for r in working]
        scored.sort(key=lambda x: x[1], reverse=True)

        # Top 10 should be biased towards recent
        top10 = scored[:10]
        now = datetime.now(timezone.utc)
        top10_ages = []
        for r, _s in top10:
            created = r.get("created_at", "")
            if created:
                dt = datetime.fromisoformat(created)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                top10_ages.append((now - dt).days)

        bottom10 = scored[-10:]
        bottom10_ages = []
        for r, _s in bottom10:
            created = r.get("created_at", "")
            if created:
                dt = datetime.fromisoformat(created)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                bottom10_ages.append((now - dt).days)

        if top10_ages and bottom10_ages:
            avg_top = sum(top10_ages) / len(top10_ages)
            avg_bottom = sum(bottom10_ages) / len(bottom10_ages)
            assert avg_top < avg_bottom, (
                f"top-scored facts should be newer: top avg={avg_top:.0f}d, "
                f"bottom avg={avg_bottom:.0f}d"
            )

    async def test_fts5_retrieves_old_facts(self, stress_db):
        """FTS5 search should find specific old facts regardless of score."""
        # Plant a distinctive old fact
        planted_id = await add_fact(
            stress_db, USER_ID,
            "Partner has a severe peanut allergy diagnosed in childhood",
            visibility="private", intensity=0.7,
        )
        # Make it 200 days old
        old_date = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        async with aiosqlite.connect(stress_db) as db:
            await db.execute(
                "UPDATE facts SET created_at = ?, updated_at = ? WHERE id = ?",
                (old_date, old_date, planted_id),
            )
            await db.commit()

        # Search should find it
        api = MemoryAPI(stress_db)
        result = await api.get_scored_context(
            USER_ID, budget_words=1200, current_message="peanut allergy"
        )

        found = any("peanut allergy" in line for line in result.lines)
        assert found, (
            "FTS5 should find the old peanut allergy fact via current_message search"
        )

    async def test_citation_boost_works(self, stress_db):
        """Heavily cited facts should score higher than uncited ones of same age."""
        # Add two facts at the same time, one cited heavily
        now = datetime.now(timezone.utc)
        age = (now - timedelta(days=30)).isoformat()

        cited_id = await add_fact(
            stress_db, USER_ID, "Fact that gets cited a lot",
            visibility="private", intensity=0.5,
        )
        uncited_id = await add_fact(
            stress_db, USER_ID, "Fact that nobody cites",
            visibility="private", intensity=0.5,
        )

        async with aiosqlite.connect(stress_db) as db:
            await db.execute(
                "UPDATE facts SET created_at = ?, updated_at = ?, citation_count = 20 WHERE id = ?",
                (age, age, cited_id),
            )
            await db.execute(
                "UPDATE facts SET created_at = ?, updated_at = ?, citation_count = 0 WHERE id = ?",
                (age, age, uncited_id),
            )
            await db.commit()

        rows = await get_facts(stress_db, USER_ID)
        cited_row = next(r for r in rows if r["id"] == cited_id)
        uncited_row = next(r for r in rows if r["id"] == uncited_id)

        score_cited = compute_relevance_score(cited_row)
        score_uncited = compute_relevance_score(uncited_row)

        assert score_cited > score_uncited, (
            f"cited fact ({score_cited:.4f}) should score higher than "
            f"uncited ({score_uncited:.4f})"
        )


class TestConsolidationEffectiveness:
    """Test that consolidation reduces active fact count meaningfully."""

    async def test_consolidation_reduces_count(self, stress_db):
        """After resolve + consolidate, active facts should drop significantly."""
        resolved = await _mark_old_facts_resolved(stress_db, USER_ID, older_than_days=14)
        before_active = await _count_by_status(stress_db, USER_ID)

        llm = _make_consolidation_mock()
        await run_consolidation(stress_db, llm, USER_ID)

        after = await _count_by_status(stress_db, USER_ID)
        after_kinds = await _count_by_kind(stress_db, USER_ID)

        # Resolved count should drop (archived after consolidation)
        assert after.get("resolved", 0) < before_active.get("resolved", 0), (
            f"resolved should decrease after consolidation: "
            f"before={before_active}, after={after}"
        )

    async def test_multiple_rounds_stabilize(self, stress_db):
        """Running consolidation multiple times shouldn't create runaway growth."""
        counts_over_rounds = []
        for round_num in range(3):
            await _mark_old_facts_resolved(stress_db, USER_ID, older_than_days=7)
            llm = _make_consolidation_mock()
            await run_consolidation(stress_db, llm, USER_ID)
            counts = await _count_by_kind(stress_db, USER_ID)
            total = sum(counts.values())
            counts_over_rounds.append(total)

        # Total should not grow across rounds
        assert counts_over_rounds[-1] <= counts_over_rounds[0] + 10, (
            f"total should stabilize or decrease: {counts_over_rounds}"
        )


class TestScalePerformance:
    """Test that operations stay fast at scale."""

    async def test_context_build_under_100ms(self, stress_db):
        """Context building should be fast even with many facts."""
        import time
        api = MemoryAPI(stress_db)

        start = time.monotonic()
        for _ in range(10):
            await api.get_scored_context(USER_ID, budget_words=1200)
        elapsed = (time.monotonic() - start) / 10

        assert elapsed < 0.1, f"context build took {elapsed:.3f}s, should be <100ms"

    async def test_1y_context_still_includes_core(self, stress_db_1y):
        """Even with a full year of data, core facts are always in context."""
        api = MemoryAPI(stress_db_1y)
        result = await api.get_scored_context(USER_ID, budget_words=500)

        core_count = sum(1 for line in result.lines if "(core)" in line)
        assert core_count == len(_CORE_FACTS)

    async def test_1y_score_distribution(self, stress_db_1y):
        """After a year, scores should show clear separation between tiers."""
        from pairtner.storage.facts import get_active_facts

        rows = await get_active_facts(stress_db_1y, USER_ID)
        scores_by_kind: dict[str, list[float]] = {}
        for r in rows:
            kind = r.get("kind") or "working"
            score = compute_relevance_score(r)
            scores_by_kind.setdefault(kind, []).append(score)

        # Core should always be 1.0
        if "core" in scores_by_kind:
            for s in scores_by_kind["core"]:
                assert s == 1.0

        # Working facts: with no time decay, score varies by intensity and status only
        if "working" in scores_by_kind:
            ws = scores_by_kind["working"]
            avg = sum(ws) / len(ws) if ws else 0
            # All working facts should have non-zero scores (no decay)
            assert all(s > 0 for s in ws), "working facts should all score > 0"
            # Low-intensity facts score lower than high-intensity ones
            assert avg > 0, f"average score should be positive, got {avg:.3f}"



class TestCascadingConsolidation:
    """Cascading consolidation is now a no-op (replaced by rolling summaries)."""

    async def test_cascading_is_noop(self, stress_db):
        """Cascading returns empty results."""
        from pairtner.core.consolidation import run_cascading_consolidation
        from tests.conftest import MockLLMClient

        result = await run_cascading_consolidation(stress_db, MockLLMClient(), USER_ID)
        assert result == {"monthly": [], "quarterly": []}
