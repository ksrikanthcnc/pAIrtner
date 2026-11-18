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
) -> list[dict[str, Any]]:
    """Consolidate resolved/outdated working facts into episodes.

    Returns a list of created episode dicts.
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    episodes_created: list[dict[str, Any]] = []

    # Find candidates: resolved/outdated working facts
    candidates = await _find_consolidation_candidates(db_path, user_id, cfg)
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
) -> list[dict[str, Any]]:
    """Find working facts eligible for consolidation."""
    now = datetime.now(timezone.utc)
    all_facts = await db_mod.get_active_facts(db_path, user_id)
    candidates = []

    for f in all_facts:
        kind = f.get("kind") or "working"
        status = f.get("status") or "active"

        # Only working facts
        if kind != "working":
            continue

        # Resolved or outdated facts are candidates
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
