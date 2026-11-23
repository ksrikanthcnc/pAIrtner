"""Persona tuning — adapt bot tone based on accumulated couple patterns.

Reads interaction scores, horsemen trends, and user patterns to generate
a short "tone overlay" that the system prompt can include. This makes
the bot adapt naturally: lighter for playful couples, more supportive
for stressed ones, without changing the base persona.
"""

from __future__ import annotations

import logging
from typing import Any

from pairtner import db as db_mod

log = logging.getLogger(__name__)


async def compute_tone_overlay(
    db_path: str,
    user_id: int,
    couple_id: str | None = None,
) -> str | None:
    """Return a 1-3 sentence tone adjustment based on couple patterns.

    Returns None if not enough data to tune (< 10 messages).
    """
    # Gather signals
    signals: dict[str, Any] = {}

    # 1. Interaction ratio (Gottman 5:1)
    if couple_id:
        pos, neg = await db_mod.get_interaction_ratio(db_path, couple_id, days=30)
        total = pos + neg
        if total < 5:
            return None  # not enough data
        signals["pos"] = pos
        signals["neg"] = neg
        signals["ratio"] = pos / neg if neg > 0 else pos

    # 2. Horsemen trends
    horsemen = await db_mod.get_user_patterns_by_type(db_path, user_id, "horsemen", limit=5)
    signals["horsemen_count"] = len(horsemen)
    if horsemen:
        signals["horsemen_types"] = [h.get("pattern", "") for h in horsemen]

    # 3. Conflict density
    if couple_id:
        conflict_summary = await db_mod.get_conflict_summary(db_path, couple_id)
        if conflict_summary:
            signals["open_conflicts"] = conflict_summary.get("open", 0)
            signals["total_conflicts"] = sum(conflict_summary.values())

    # 4. Communication patterns
    patterns = await db_mod.get_user_patterns_by_type(db_path, user_id, "communication", limit=5)
    signals["comm_patterns"] = [p.get("pattern", "") for p in patterns]

    # Build the overlay from signals
    return _build_overlay(signals)


def _build_overlay(signals: dict[str, Any]) -> str | None:
    """Convert signals into a natural tone adjustment."""
    parts: list[str] = []

    ratio = signals.get("ratio", 0)
    horsemen_count = signals.get("horsemen_count", 0)
    open_conflicts = signals.get("open_conflicts", 0)

    # Determine couple "temperature"
    if ratio >= 5.0 and horsemen_count == 0 and open_conflicts == 0:
        # Warm, healthy relationship
        parts.append(
            "This couple communicates well (high positive ratio, no "
            "concerning patterns). You can be lighter, more playful, "
            "and use gentle humor. They appreciate warmth over caution."
        )
    elif ratio < 2.0 or horsemen_count >= 3:
        # Strained relationship
        parts.append(
            "This couple is going through a tense period (low positive-to-negative "
            "ratio or frequent communication pitfalls). Be extra gentle, "
            "validate feelings before suggesting anything, and avoid humor "
            "about their relationship dynamics. Focus on small, concrete steps."
        )
    elif open_conflicts >= 2:
        # Active conflicts
        parts.append(
            "There are unresolved conflicts. Be de-escalating but not "
            "dismissive. Acknowledge the difficulty. Don't bring up "
            "conflicts unless the user does — focus on the present moment."
        )
    elif ratio >= 3.0:
        # Decent, normal range
        parts.append(
            "This couple has a good-enough dynamic. Be natural and balanced — "
            "warm but not overly cautious. Match their energy."
        )
    else:
        return None  # Default persona is fine

    # Add horsemen awareness if detected
    horsemen_types = signals.get("horsemen_types", [])
    if horsemen_types:
        # Only mention the most common pattern
        common = horsemen_types[0] if horsemen_types else None
        if common and common.lower() in ("criticism", "contempt", "defensiveness", "stonewalling"):
            parts.append(
                f"Watch for {common.lower()} patterns in this user's messages — "
                f"gently redirect without labeling or lecturing."
            )

    return "\n".join(parts) if parts else None
