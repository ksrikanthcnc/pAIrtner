"""Dampening formula and integration tests."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from pairtner.config import Config
from pairtner.core.dampening import (
    calculate_dampening,
    get_dampening_instruction,
    resolve_dampening,
)
from pairtner.db import (
    create_user,
    ensure_user_profile,
    set_user_mood,
    update_user_profile,
)


class TestCalculateDampening:
    """Unit tests for the dampening formula."""

    def test_basic_value(self):
        """Result should be positive and less than base."""
        d = calculate_dampening(0.7, None, 10.0)
        assert 0 < d < 1
        # log(2)/log(11) ≈ 0.2891, × 0.7 ≈ 0.2024
        expected = 0.7 * math.log(2) / math.log(11)
        assert abs(d - expected) < 1e-6

    def test_mood_override_replaces_base(self):
        """mood_override should replace base in the formula."""
        d_base = calculate_dampening(0.7, None, 10.0)
        d_mood = calculate_dampening(0.7, 0.9, 10.0)
        assert d_mood > d_base  # 0.9 > 0.7

    def test_zero_base(self):
        """Zero base → zero dampening."""
        d = calculate_dampening(0.0, None, 10.0)
        assert d == 0.0

    def test_max_intensity_guard(self):
        """Zero or negative max_intensity defaults to 10.0."""
        d = calculate_dampening(0.7, None, 0.0)
        assert d > 0

    def test_high_dampening_compresses(self):
        """Higher base → higher dampening value."""
        low = calculate_dampening(0.3, None, 10.0)
        high = calculate_dampening(0.9, None, 10.0)
        assert high > low

    def test_logarithmic_shape(self):
        """Output should follow log curve — not linear."""
        # At different "max" values, the ratio should change logarithmically
        d5 = calculate_dampening(1.0, None, 5.0)
        d50 = calculate_dampening(1.0, None, 50.0)
        # With higher max, the same input produces smaller output
        assert d5 > d50


class TestGetDampeningInstruction:
    """Test the instruction text mapping."""

    def test_high_level(self):
        instr = get_dampening_instruction(0.9)
        assert "gently" in instr.lower() or "soften" in instr.lower()

    def test_medium_level(self):
        instr = get_dampening_instruction(0.5)
        assert "warm" in instr.lower() or "kind" in instr.lower()

    def test_low_level(self):
        instr = get_dampening_instruction(0.15)
        assert "direct" in instr.lower()

    def test_zero_level(self):
        instr = get_dampening_instruction(0.0)
        assert "direct" in instr.lower()

    def test_varies_with_level(self):
        """Different levels produce different instructions."""
        i1 = get_dampening_instruction(0.1)
        i2 = get_dampening_instruction(0.9)
        assert i1 != i2


class TestResolveDampening:
    """Integration tests for the full resolution pipeline."""

    @pytest.mark.asyncio
    async def test_blunt_bypass(self, test_db):
        """Blunt tag bypasses all dampening."""
        await create_user(test_db, 1, "X")
        await ensure_user_profile(test_db, 1)

        cfg = Config(telegram_token="t", dampening_blunt_bypass=True)
        level, instr = await resolve_dampening(test_db, 1, cfg, has_blunt_tag=True)
        assert level == 0.0
        assert "no softening" in instr.lower()

    @pytest.mark.asyncio
    async def test_disabled_dampening(self, test_db):
        """Disabled dampening returns zero level."""
        await create_user(test_db, 1, "X")
        await ensure_user_profile(test_db, 1)

        cfg = Config(telegram_token="t", dampening_enabled=False)
        level, instr = await resolve_dampening(test_db, 1, cfg, has_blunt_tag=False)
        assert level == 0.0
        assert "direct" in instr.lower()

    @pytest.mark.asyncio
    async def test_profile_base_used(self, test_db):
        """User's custom dampening_base is used when set."""
        await create_user(test_db, 1, "X")
        await ensure_user_profile(test_db, 1)
        await update_user_profile(test_db, 1, dampening_base=0.3)

        cfg = Config(telegram_token="t")
        level, _ = await resolve_dampening(test_db, 1, cfg, has_blunt_tag=False)
        # Should be lower than default 0.7 base
        level_default = calculate_dampening(0.7, None, 10.0)
        assert level < level_default

    @pytest.mark.asyncio
    async def test_mood_override_applied(self, test_db):
        """Active mood's dampening_override overrides the profile base."""
        await create_user(test_db, 1, "X")
        await ensure_user_profile(test_db, 1)

        expires = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        await set_user_mood(test_db, 1, "stressed", expires, dampening_override=0.95)

        cfg = Config(telegram_token="t")
        level, instr = await resolve_dampening(test_db, 1, cfg, has_blunt_tag=False)
        # 0.95 override → should be higher than 0.7 base
        level_base = calculate_dampening(0.7, None, 10.0)
        assert level > level_base
        assert "gently" in instr.lower() or "soften" in instr.lower()

    @pytest.mark.asyncio
    async def test_no_profile_uses_defaults(self, test_db):
        """User with no profile row uses config defaults."""
        await create_user(test_db, 1, "X")
        cfg = Config(telegram_token="t", dampening_default_base=0.5)
        level, _ = await resolve_dampening(test_db, 1, cfg, has_blunt_tag=False)
        expected = calculate_dampening(0.5, None, 10.0)
        assert abs(level - expected) < 1e-6
