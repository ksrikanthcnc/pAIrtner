"""Memory consolidation v2 — compress old memories into topic-based episodes.

Runs weekly (or on-demand). Gathers resolved/outdated working facts,
sends them to the LLM for thematic grouping and summarization, and
creates episode entries. Source facts are archived after episode creation.
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
    """Consolidate resolved/outdated working facts into episodes.

    When *window_start* and *window_end* are given (ISO timestamps), only facts
    whose ``updated_at`` (or ``created_at``) falls within that range are
    considered.  This supports fixed-calendar-window consolidation.

    Returns a list of created episode dicts.
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    episodes_created: list[dict[str, Any]] = []

    # Find candidates: resolved/outdated working facts
    candidates = await _find_consolidation_candidates(
        db_path, user_id, cfg,
        window_start=window_start, window_end=window_end,
    )
    if len(candidates) < cfg["min_memories_for_episode"]:
        log.info(
            "consolidation: user %s has %d candidates, need %d",
            user_id, len(candidates), cfg["min_memories_for_episode"],
        )
        return episodes_created

    # Build the user message with all candidate facts
    fact_lines = []
    for f in candidates:
        fact_lines.append(f"[{f['id']}] ({f.get('status', 'active')}) {f['text']}")
    user_msg = "Facts to consolidate:\n" + "\n".join(fact_lines)

    # Call LLM for thematic grouping + summarization
    try:
        result = await llm.generate(
            user_msg,
            CONSOLIDATION_SYSTEM,
            api_key_override=api_key_override,
        )

        # Parse the episodes from LLM response
        raw_episodes = _parse_episodes(result)
        if not raw_episodes:
            log.info("consolidation: LLM returned no episodes for user %s", user_id)
            return episodes_created

        candidate_ids = {f["id"] for f in candidates}

        for ep in raw_episodes:
            text = ep.get("text", "").strip()
            source_ids = ep.get("source_ids", [])
            if not text or len(source_ids) < cfg["min_memories_for_episode"]:
                continue

            # Quality check: reject overly short summaries
            if len(text.split()) < 8:
                log.info("consolidation: skipping too-short episode: %r", text[:60])
                continue

            # Validate source_ids are from our candidates
            valid_ids = [sid for sid in source_ids if sid in candidate_ids]
            if len(valid_ids) < cfg["min_memories_for_episode"]:
                continue

            # Collect raw message IDs from source facts for provenance
            raw_msg_ids: list[int] = []
            for sid in valid_ids:
                fact = next((f for f in candidates if f["id"] == sid), None)
                if fact:
                    # Get source_message_id or source_ids from the fact
                    smid = fact.get("source_message_id")
                    if smid:
                        raw_msg_ids.append(smid)
                    sids = fact.get("source_ids")
                    if sids:
                        try:
                            raw_msg_ids.extend(json.loads(sids))
                        except (json.JSONDecodeError, TypeError):
                            pass

            # Create the episode
            episode_id = await db_mod.add_fact(
                db_path, user_id, text, "private",
                fact_type="stable",
                source_ids=json.dumps(raw_msg_ids) if raw_msg_ids else None,
                model=getattr(llm, '_last_model', None),
                metadata=json.dumps({"level": "weekly"}),
            )

            # Set kind to episode
            async with aiosqlite.connect(db_path) as db:
                await db.execute(
                    "UPDATE facts SET kind = 'episode', status = 'active' WHERE id = ?",
                    (episode_id,),
                )
                await db.commit()

            # Archive the source facts
            for sid in valid_ids:
                await db_mod.archive_fact(db_path, sid)

            episodes_created.append({
                "id": episode_id,
                "text": text,
                "source_count": len(valid_ids),
                "source_ids": valid_ids,
                "raw_msg_ids": raw_msg_ids,
            })

            log.info(
                "consolidation: user %s — episode from %d facts: %s",
                user_id, len(valid_ids), text[:80],
            )

        # Log coverage: what fraction of candidates were included in episodes
        covered_ids = set()
        for ep in episodes_created:
            covered_ids.update(ep.get("source_ids", []))
        coverage = len(covered_ids) / len(candidates) if candidates else 0
        if coverage < 0.5 and len(candidates) >= 6:
            log.warning(
                "consolidation: low coverage for user %s — %d/%d facts (%d%%) "
                "included in episodes. Consider adjusting min_memories_for_episode.",
                user_id, len(covered_ids), len(candidates), int(coverage * 100),
            )

    except Exception:
        log.exception("consolidation: failed for user %s", user_id)

    return episodes_created


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
    """Run cascading consolidation: monthly (from weekly episodes) and
    quarterly (from monthly episodes).

    Returns {"monthly": [...], "quarterly": [...]}.
    """
    cfg = {**CASCADING_CONFIG, **(config or {})}
    result: dict[str, list[dict[str, Any]]] = {"monthly": [], "quarterly": []}

    # --- Monthly: consolidate old weekly episodes (30d-90d) ---
    monthly_candidates = await _find_episode_candidates(
        db_path, user_id, level="weekly", age_days=cfg["monthly_age_days"],
        max_age_days=cfg.get("monthly_max_age_days"),
    )
    if len(monthly_candidates) >= cfg["min_episodes_for_cascade"]:
        monthly_eps = await _consolidate_episodes(
            db_path, user_id, monthly_candidates, llm,
            system_prompt=MONTHLY_SYSTEM, target_level="monthly",
            min_sources=cfg["min_episodes_for_cascade"],
        )
        result["monthly"] = monthly_eps
        log.info(
            "cascading: user %s — %d monthly episodes from %d weekly",
            user_id, len(monthly_eps), len(monthly_candidates),
        )

    # --- Quarterly: consolidate old monthly episodes ---
    quarterly_candidates = await _find_episode_candidates(
        db_path, user_id, level="monthly", age_days=cfg["quarterly_age_days"],
    )
    if len(quarterly_candidates) >= cfg["min_episodes_for_cascade"]:
        quarterly_eps = await _consolidate_episodes(
            db_path, user_id, quarterly_candidates, llm,
            system_prompt=QUARTERLY_SYSTEM, target_level="quarterly",
            min_sources=cfg["min_episodes_for_cascade"],
        )
        result["quarterly"] = quarterly_eps
        log.info(
            "cascading: user %s — %d quarterly episodes from %d monthly",
            user_id, len(quarterly_eps), len(quarterly_candidates),
        )

    return result


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
