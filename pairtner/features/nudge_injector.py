"""Inject v2 nudge suggestions / camouflage questions into the system prompt.

Privacy: only ``nudge_suggestion`` (via get_pending_nudges) is ever used;
``facts.text`` is never read here.
"""

from __future__ import annotations

import random

from pairtner.core.memory import SETTINGS_META_PREFIX
from pairtner.features.nudge_engine import (
    deliver_nudge,
    get_camouflage_questions,
    get_pending_nudges,
)
from pairtner.storage import settings as settings_mod

CAMOUFLAGE_PROBABILITY: dict[str, float] = {
    "off": 0.0, "low": 0.1, "normal": 0.25, "high": 0.5,
}

NUDGE_HEADER = "## Therapeutic guidance (weave in naturally; never reveal its source)"
CAMOUFLAGE_HEADER = (
    "## Optional check-in (ask naturally, at most once, only if it fits the flow)"
)


async def _setting(db_path: str, user_id: int, key: str, default: str) -> str:
    val = await settings_mod.get_user_preference(
        db_path, user_id, SETTINGS_META_PREFIX + key, default=default,
    )
    return val or default


async def inject_nudge_context(
    db_path: str, user_id: int, relationship_id: str | None, system_prompt: str,
) -> tuple[str, list[int]]:
    """Append pending nudge suggestions for ``user_id``; return (prompt, injected ids)."""
    if await _setting(db_path, user_id, "nudge_enabled", "true") == "false":
        return system_prompt, []
    nudges = await get_pending_nudges(db_path, user_id)
    if not nudges:
        return system_prompt, []
    lines = [f"- ({n['nudge_type']}) {n['nudge_suggestion']}" for n in nudges]
    prompt = f"{system_prompt}\n\n{NUDGE_HEADER}\n" + "\n".join(lines)
    return prompt, [n["fact_id"] for n in nudges]


async def mark_nudges_delivered(db_path: str, fact_ids: list[int]) -> None:
    for fid in fact_ids:
        await deliver_nudge(db_path, fid)


async def maybe_inject_camouflage(
    db_path: str, user_id: int, system_prompt: str, relationship_id: str | None = None,
    rng: random.Random | None = None,
) -> str:
    """Randomly (per camouflage_frequency) append one check-in question."""
    freq = await _setting(db_path, user_id, "camouflage_frequency", "normal")
    prob = CAMOUFLAGE_PROBABILITY.get(freq, CAMOUFLAGE_PROBABILITY["normal"])
    r = rng or random
    if prob <= 0 or r.random() >= prob:
        return system_prompt
    questions = await get_camouflage_questions(db_path, user_id, count=1, rng=rng)
    if not questions:
        return system_prompt
    return f"{system_prompt}\n\n{CAMOUFLAGE_HEADER}\n- {questions[0]}"
