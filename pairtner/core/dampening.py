"""Logarithmic dampening — modulates LLM output intensity.

The formula compresses high-intensity input so the bot's responses
stay proportionally softer.  Higher dampening → gentler output.
"""

from __future__ import annotations

import math

from pairtner import db as db_mod
from pairtner import pack
from pairtner.config import Config


def calculate_dampening(
    base: float,
    mood_override: float | None,
    max_intensity: float,
) -> float:
    """Apply logarithmic dampening to *base* intensity.

    Formula::

        output = effective_base * log(1 + input) / log(1 + max_intensity)

    Where *input* is fixed at 1.0 (the current message's raw intensity)
    and *effective_base* is ``mood_override`` when present, else ``base``.

    Returns a float in ``(0, 1]``.
    """
    effective_base = mood_override if mood_override is not None else base
    if max_intensity <= 0:
        max_intensity = 10.0
    # input_intensity is normalised to 1.0 for a single message
    numerator = math.log(1 + 1.0)
    denominator = math.log(1 + max_intensity)
    return effective_base * (numerator / denominator)


def get_dampening_instruction(level: float) -> str:
    """Map a dampening *level* to a natural-language LLM instruction.

    Higher level → softer language.
    """
    p = pack.current()
    if level >= 0.8:
        return p.s("dampening_very_high")
    if level >= 0.5:
        return p.s("dampening_high")
    if level >= 0.2:
        return p.s("dampening_medium")
    return p.s("dampening_low")


async def resolve_dampening(
    db_path: str,
    user_id: int,
    config: Config,
    has_blunt_tag: bool = False,
) -> tuple[float, str]:
    """Full resolution pipeline: profile → mood → formula → instruction.

    Returns ``(level, instruction_text)``.
    If *has_blunt_tag* and ``config.dampening_blunt_bypass``, returns
    ``(0.0, <the pack's blunt-bypass instruction>)``.
    """
    if has_blunt_tag and config.dampening_blunt_bypass:
        return 0.0, pack.current().s("dampening_blunt_bypass")

    if not config.dampening_enabled:
        return 0.0, pack.current().s("dampening_low")

    profile = await db_mod.get_user_profile(db_path, user_id)
    base = (profile or {}).get("dampening_base", config.dampening_default_base)
    if isinstance(base, (int, float)):
        base = float(base)
    else:
        base = config.dampening_default_base

    mood = await db_mod.get_active_mood(db_path, user_id)
    mood_override = None
    if mood and mood.get("dampening_override") is not None:
        mood_override = float(mood["dampening_override"])

    level = calculate_dampening(base, mood_override, config.dampening_max_intensity)
    instruction = get_dampening_instruction(level)
    return level, instruction
