"""Memory consolidation — archive old resolved/outdated working facts.

Previously created 'episode' summaries from working facts; now the rolling
summary system (``memory_maintenance.run_periodic_summaries``) handles
summarization. This module only archives stale facts so they leave the prompt.

Episodes that already exist in the DB are still readable but no new ones
are created.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from pairtner import db as db_mod
from pairtner.llm import LLMClient
from pairtner.storage.helpers import _now_iso

log = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "resolved_age_days": 30,
    "outdated_age_days": 14,
    "max_memories_per_episode": 10,
    "min_memories_for_episode": 3,
}

CONSOLIDATION_SYSTEM = """You are a knowledge consolidation engine. Given a list of resolved/outdated facts
about a person, group them by theme and summarize each group into one episode.

Respond with ONLY valid JSON:
{
  "episodes": [
    {
      "text": "One paragraph summarizing this group of related facts.",
      "source_ids": [1, 2, 5]
    }
  ]
}

Rules:
- Group by theme (household, work, emotional, relationship dynamics), NOT by time.
- Each episode should be a self-contained paragraph, 1-3 sentences.
- Include ALL source fact IDs in source_ids.
- Facts that don't fit any group of 3+ can be left out (they stay as-is).
- Be concise. Capture the key events and outcomes.
- Write in third person ("They argued about..." not "You argued about...").
"""


async def run_consolidation(
    db_path: str,
    llm: LLMClient,
    user_id: int,
    config: dict[str, Any] | None = None,
    api_key_override: str | None = None,
    window_start: str | None = None,
    window_end: str | None = None,
) -> list[dict[str, Any]]:
    """Archive resolved/outdated/stale working facts.

    Rolling summaries now handle summarization; this function only archives
    stale facts so they leave the prompt context.  No episodes are created
    and no LLM call is made.

    Returns a list of archived fact dicts (for backward compat with callers
    that inspect the result).
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}

    candidates = await _find_consolidation_candidates(
        db_path, user_id, cfg,
        window_start=window_start, window_end=window_end,
    )
    if not candidates:
        return []

    archived: list[dict[str, Any]] = []
    for f in candidates:
        await db_mod.archive_fact(db_path, f["id"])
        archived.append({"id": f["id"], "text": f["text"], "source_ids": [f["id"]]})

    log.info(
        "consolidation: user %s — archived %d stale facts (no episodes created)",
        user_id, len(archived),
    )
    return archived


def _parse_episodes(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract episodes from LLM response."""
    # Try direct field access (structured JSON response)
    episodes = result.get("episodes")
    if isinstance(episodes, list):
        return episodes

    # Try parsing the reply as JSON
    reply = result.get("reply", "")
    if isinstance(reply, str):
        try:
            parsed = json.loads(reply)
            if isinstance(parsed, dict) and "episodes" in parsed:
                return parsed["episodes"]
        except (json.JSONDecodeError, TypeError):
            pass

    return []


async def _find_consolidation_candidates(
    db_path: str, user_id: int, cfg: dict[str, Any],
    *,
    window_start: str | None = None,
    window_end: str | None = None,
) -> list[dict[str, Any]]:
    """Find working facts eligible for weekly consolidation.

    Two paths:
    1. Resolved/outdated facts (any age) — these are done, compress them.
    2. Active stale facts (90d+ with no citations) — forgotten, auto-archive.

    When *window_start*/*window_end* are given, only facts whose updated_at
    (status-change timestamp) or created_at falls within [start, end) are
    considered.  This implements fixed-calendar-window consolidation.
    """
    now = datetime.now(timezone.utc)
    all_facts = await db_mod.get_active_facts(db_path, user_id)

    # Parse window boundaries if provided
    win_start_dt = win_end_dt = None
    if window_start:
        win_start_dt = datetime.fromisoformat(window_start)
        if win_start_dt.tzinfo is None:
            win_start_dt = win_start_dt.replace(tzinfo=timezone.utc)
    if window_end:
        win_end_dt = datetime.fromisoformat(window_end)
        if win_end_dt.tzinfo is None:
            win_end_dt = win_end_dt.replace(tzinfo=timezone.utc)

    candidates = []

    for f in all_facts:
        kind = f.get("kind") or "working"
        status = f.get("status") or "active"

        # Only working facts
        if kind != "working":
            continue

        # Window filter: if a window is specified, check the fact's timestamp
        if win_start_dt or win_end_dt:
            # Prefer updated_at (when status changed), fall back to created_at
            ts_str = f.get("updated_at") or f.get("created_at")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
            if win_start_dt and ts < win_start_dt:
                continue
            if win_end_dt and ts >= win_end_dt:
                continue

        # Resolved or outdated facts are always candidates
        if status in ("resolved", "outdated"):
            candidates.append(f)
            continue

        # Active working facts older than 90 days with no citations
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
        citations = f.get("citation_count") or 0
        if status == "active" and age_days >= 90 and citations == 0:
            candidates.append(f)

    return candidates


# ---------------------------------------------------------------------------
# Cascading consolidation: monthly + quarterly
# ---------------------------------------------------------------------------

CASCADING_CONFIG = {
    "monthly_age_days": 30,     # consolidate weekly episodes older than 30d
    "monthly_max_age_days": 90, # but not older than 90d (those go to quarterly)
    "quarterly_age_days": 90,   # consolidate monthly episodes older than 90d
    "min_episodes_for_cascade": 3,
}

MONTHLY_SYSTEM = """You are a knowledge consolidation engine. Given a set of weekly episode summaries,
merge them into a broader monthly summary. Combine themes, track how situations evolved.

Respond with ONLY valid JSON:
{
  "episodes": [
    {
      "text": "A concise 2-3 sentence summary covering the themes from these episodes.",
      "source_ids": [101, 102, 105]
    }
  ]
}

Rules:
- Merge related episodes (e.g. multiple work-stress episodes → one work theme summary).
- Track evolution ("started stressed about interview → got the job → settling in").
- Keep key details, drop redundant ones.
- Write in third person.
"""

QUARTERLY_SYSTEM = """You are a knowledge consolidation engine. Given monthly episode summaries,
create a broad quarterly overview. Focus on major themes and how they evolved.

Respond with ONLY valid JSON:
{
  "episodes": [
    {
      "text": "A concise summary of the major themes and evolution over this quarter.",
      "source_ids": [201, 202]
    }
  ]
}

Rules:
- Maximum 2-3 episodes per quarter (very compressed).
- Track major arcs, not individual events.
- Important details (decisions, outcomes, unresolved tensions) survive; daily noise does not.
- Write in third person.
"""


async def run_cascading_consolidation(
    db_path: str,
    llm: LLMClient,
    user_id: int,
    config: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """No-op — cascading consolidation is replaced by rolling summaries.

    Existing episodes in the DB are still readable but no new ones are created.
    Returns empty results for backward compat.
    """
    log.info("cascading consolidation skipped for user %s (replaced by rolling summaries)", user_id)
    return {"monthly": [], "quarterly": []}


async def _find_episode_candidates(
    db_path: str, user_id: int, level: str, age_days: int,
    max_age_days: int | None = None,
) -> list[dict[str, Any]]:
    """Find episodes of a given level that are old enough to cascade.

    Episodes are tagged by their metadata level field (weekly/monthly).
    Episodes without a level tag are assumed weekly (backward compat).

    age_days: minimum age to be eligible
    max_age_days: maximum age (exclusive). None = no upper bound.
    """
    now = datetime.now(timezone.utc)
    all_facts = await db_mod.get_active_facts(db_path, user_id)
    candidates = []

    for f in all_facts:
        kind = f.get("kind") or "working"
        if kind != "episode":
            continue

        # Determine episode level from metadata
        ep_level = "weekly"  # default
        meta = f.get("metadata")
        if meta:
            try:
                meta_dict = json.loads(meta) if isinstance(meta, str) else meta
                ep_level = meta_dict.get("level", "weekly")
            except (json.JSONDecodeError, TypeError):
                pass

        if ep_level != level:
            continue

        # Check age
        created = f.get("created_at")
        if not created:
            continue
        try:
            created_dt = datetime.fromisoformat(created)
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue

        fact_age = (now - created_dt).days
        if fact_age >= age_days and (max_age_days is None or fact_age < max_age_days):
            candidates.append(f)

    return candidates


async def _consolidate_episodes(
    db_path: str,
    user_id: int,
    candidates: list[dict[str, Any]],
    llm: LLMClient,
    system_prompt: str,
    target_level: str,
    min_sources: int = 3,
) -> list[dict[str, Any]]:
    """Consolidate a list of episodes into higher-level ones."""
    episodes_created: list[dict[str, Any]] = []

    # Build prompt
    fact_lines = []
    for f in candidates:
        fact_lines.append(f"[{f['id']}] {f['text']}")
    user_msg = f"Episodes to consolidate into {target_level} summaries:\n" + "\n".join(fact_lines)

    try:
        result = await llm.generate(user_msg, system_prompt)
        raw_episodes = _parse_episodes(result)
        if not raw_episodes:
            return episodes_created

        candidate_ids = {f["id"] for f in candidates}

        for ep in raw_episodes:
            text = ep.get("text", "").strip()
            source_ids = ep.get("source_ids", [])
            if not text or len(source_ids) < min_sources:
                continue

            valid_ids = [sid for sid in source_ids if sid in candidate_ids]
            if len(valid_ids) < min_sources:
                continue

            # Collect provenance from source episodes
            raw_msg_ids: list[int] = []
            for sid in valid_ids:
                fact = next((f for f in candidates if f["id"] == sid), None)
                if fact:
                    sids = fact.get("source_ids")
                    if sids:
                        try:
                            raw_msg_ids.extend(json.loads(sids))
                        except (json.JSONDecodeError, TypeError):
                            pass

            # Create the higher-level episode
            meta = json.dumps({"level": target_level})
            episode_id = await db_mod.add_fact(
                db_path, user_id, text, "private",
                fact_type="stable",
                source_ids=json.dumps(raw_msg_ids) if raw_msg_ids else None,
                metadata=meta,
            )

            # Set kind to episode
            async with aiosqlite.connect(db_path) as db:
                await db.execute(
                    "UPDATE facts SET kind = 'episode', status = 'active' WHERE id = ?",
                    (episode_id,),
                )
                await db.commit()

            # Archive the source episodes
            for sid in valid_ids:
                await db_mod.archive_fact(db_path, sid)

            episodes_created.append({
                "id": episode_id,
                "text": text,
                "level": target_level,
                "source_count": len(valid_ids),
            })

    except Exception:
        log.exception("cascading consolidation failed for user %s", user_id)

    return episodes_created
