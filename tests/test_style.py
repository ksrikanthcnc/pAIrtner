"""Style command tests — /style, voice selection, intensity callbacks."""

from __future__ import annotations

import pytest

from pairtner.db import (
    create_user,
    ensure_user_profile,
    get_user_profile,
    set_partner,
    update_user_profile,
)


@pytest.mark.asyncio
async def test_default_voice_on_new_user(test_db, paired_users):
    """New user profile has default voice casual_en."""
    a_id, _ = paired_users
    profile = await ensure_user_profile(test_db, a_id)
    assert profile["default_voice"] == "casual_en"
    assert abs(profile["voice_intensity"] - 0.5) < 0.01
