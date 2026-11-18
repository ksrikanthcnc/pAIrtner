"""Memory consolidation — compress old memories into episode summaries.

Runs periodically (weekly or on-demand). Reads the pack's consolidation config
and produces episode summaries from resolved/outdated working memories.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from pairtner import db as db_mod
from pairtner.llm import LLMClient
from pairtner.core.prompts import build_consolidation_prompt

log = logging.getLogger(__name__)

# Default config (overridden by pack.yaml consolidation section)
DEFAULT_CONFIG = {
    "resolved_age_days": 30,
    "outdated_age_days": 14,
    "max_memories_per_episode": 10,
    "min_memories_for_episode": 3,
}


async def run_consolidation(
    db_path: str,
    llm: LLMClient,
    user_id: int,
    config: dict[str, Any] | None = None,
    api_key_override: str | None = None,
) -> list[dict[str, Any]]:
    """Consolidate old memories into episode summaries.

    Returns a list of created episode dicts.
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    episodes_created: list[dict[str, Any]] = []

    # Find candidates: resolved memories older than threshold
    candidates = await _find_consolidation_candidates(db_path, user_id, cfg)
    if len(candidates) < cfg["min_memories_for_episode"]:
        log.info(
            "consolidation: user %s has %d candidates, need %d",
            user_id, len(candidates), cfg["min_memories_for_episode"],
        )
        return episodes_created

    # Group candidates by time period (month)
    groups = _group_by_period(candidates)

    for period, memories in groups.items():
        if len(memories) < cfg["min_memories_for_episode"]:
            continue

        batch = memories[:cfg["max_memories_per_episode"]]

        # Build prompt for summarization
        system, content = build_consolidation_prompt(batch, period)

        try:
            result = await llm.generate(
                content, system, api_key_override=api_key_override,
            )
            summary = result.get("reply", "").strip()
            if not summary:
                continue

            # Save the episode as a new fact with kind='episode'
            episode_id = await db_mod.add_fact(
                db_path, user_id, summary, "private",
                fact_type="stable",
            )
            # Update kind to episode
            async with aiosqlite.connect(db_path) as db:
                await db.execute(
                    "UPDATE facts SET kind = 'episode', status = 'active' WHERE id = ?",
                    (episode_id,),
                )
                await db.commit()

            # Archive the source memories (they're now captured in the episode)
            for m in batch:
                await db_mod.archive_fact(db_path, m["id"])

            episodes_created.append({
                "id": episode_id,
                "period": period,
                "summary": summary,
                "source_count": len(batch),
                "source_ids": [m["id"] for m in batch],
            })

            log.info(
                "consolidation: user %s — episode for %s from %d memories",
                user_id, period, len(batch),
            )
        except Exception:
            log.exception(
                "consolidation: failed to create episode for user %s period %s",
                user_id, period,
            )

    return episodes_created


async def _find_consolidation_candidates(
    db_path: str, user_id: int, cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    """Find memories eligible for consolidation."""
    now = datetime.now(timezone.utc)

    # Get all non-archived, non-core memories
    all_facts = await db_mod.get_active_facts(db_path, user_id)
    candidates = []

    for f in all_facts:
        kind = f.get("kind") or "working"
        status = f.get("status") or "active"

        # Skip core memories -- they never age
        if kind == "core":
            continue

        # Skip episodes -- they're already consolidated
        if kind == "episode":
            continue

        created = f.get("created_at")
        if not created:
            continue

        try:
            created_dt = datetime.fromisoformat(created)
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue

        age_days = (now - created_dt).days

        # Resolved memories: consolidate after resolved_age_days
        if status in ("resolved", "outdated") and age_days >= cfg["resolved_age_days"]:
            candidates.append(f)
        # Old active working memories that haven't been touched
        elif status == "active" and kind == "working" and age_days >= cfg.get("active_age_days", 90):
            candidates.append(f)

    return candidates


def _group_by_period(facts: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group facts by their creation month (YYYY-MM)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for f in facts:
        created = f.get("created_at", "")
        try:
            dt = datetime.fromisoformat(created)
            period = dt.strftime("%Y-%m")
        except (ValueError, TypeError):
            period = "unknown"
        groups.setdefault(period, []).append(f)
    return dict(sorted(groups.items()))
