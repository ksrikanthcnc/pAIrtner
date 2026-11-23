"""Tests for persona tuning — adaptive tone based on couple patterns."""

import pytest
from pairtner.core.persona_tuning import _build_overlay, compute_tone_overlay
from pairtner.storage.schema import init_db
from pairtner import db as db_mod


class TestBuildOverlay:
    def test_warm_couple(self):
        signals = {"ratio": 6.0, "horsemen_count": 0, "open_conflicts": 0}
        result = _build_overlay(signals)
        assert result is not None
        assert "lighter" in result or "playful" in result

    def test_strained_couple_low_ratio(self):
        signals = {"ratio": 1.5, "horsemen_count": 1, "open_conflicts": 0}
        result = _build_overlay(signals)
        assert result is not None
        assert "gentle" in result or "tense" in result

    def test_strained_couple_many_horsemen(self):
        signals = {"ratio": 3.0, "horsemen_count": 4, "open_conflicts": 0}
        result = _build_overlay(signals)
        assert result is not None
        assert "tense" in result or "gentle" in result

    def test_active_conflicts(self):
        signals = {"ratio": 3.5, "horsemen_count": 0, "open_conflicts": 3}
        result = _build_overlay(signals)
        assert result is not None
        assert "conflict" in result.lower()

    def test_normal_couple(self):
        signals = {"ratio": 3.5, "horsemen_count": 0, "open_conflicts": 0}
        result = _build_overlay(signals)
        assert result is not None
        assert "natural" in result or "balanced" in result

    def test_insufficient_data(self):
        signals = {"ratio": 2.5, "horsemen_count": 0, "open_conflicts": 0}
        # Ratio between 2 and 3, no horsemen, no conflicts -> returns None (default)
        result = _build_overlay(signals)
        assert result is None

    def test_horsemen_awareness(self):
        signals = {
            "ratio": 1.5,
            "horsemen_count": 2,
            "horsemen_types": ["criticism", "defensiveness"],
            "open_conflicts": 0,
        }
        result = _build_overlay(signals)
        assert result is not None
        assert "criticism" in result.lower()


@pytest.fixture
async def test_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    return db_path


@pytest.mark.asyncio
async def test_compute_tone_overlay_no_data(test_db):
    """With no data, returns None."""
    result = await compute_tone_overlay(test_db, 1001, couple_id="1001:1002")
    assert result is None


@pytest.mark.asyncio
async def test_compute_tone_overlay_with_data(test_db):
    """With sufficient data, returns a tone overlay."""
    # Seed some interaction data
    for i in range(10):
        await db_mod.record_interaction(test_db, "1001:1002", 1001, "positive")
    for i in range(2):
        await db_mod.record_interaction(test_db, "1001:1002", 1001, "negative")

    result = await compute_tone_overlay(test_db, 1001, couple_id="1001:1002")
    # 10:2 ratio = 5.0, should get warm overlay
    assert result is not None
    assert "lighter" in result or "playful" in result or "natural" in result
