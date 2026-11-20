"""LLM-callable tools — functions the bot can invoke mid-conversation.

The LLM returns a ``tool_calls`` array in its response. The engine executes
each tool, appends the results to the conversation, and re-calls the LLM.

Currently: ``search_memory`` (FTS5 search over the user's facts + archived
summaries for deep retrieval when summaries in context aren't detailed enough).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

# Tool definitions for the LLM (OpenAI-compatible function calling format)
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": (
                "Search your memory for facts and past summaries about a specific topic. "
                "Use this when the user asks about something from the past that isn't "
                "in your current context, or when you need more detail than what the "
                "summaries provide. Returns the most relevant memories."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search for (a topic, event, person, or theme)",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


async def execute_search_memory(
    db_path: str,
    user_id: int,
    query: str,
    relationship_id: str | None = None,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search the user's facts and summaries via FTS5.

    Returns up to *limit* results, each with {id, text, kind, visibility, created_at}.
    Includes archived facts and summaries so the LLM can retrieve deep history.
    """
    # Build FTS5 query from the search terms
    import re

    terms = [
        t for t in re.sub(r"[^\w\s]", " ", query, flags=re.UNICODE).split()
        if len(t) > 2
    ]
    if not terms:
        return []
    fts_query = " OR ".join(f'"{t}"' for t in terms[:12])

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        try:
            # Search across all facts (including archived) and summaries
            async with db.execute(
                "SELECT f.id, f.text, f.kind, f.visibility, f.created_at "
                "FROM facts_fts JOIN facts f ON f.id = facts_fts.rowid "
                "WHERE facts_fts MATCH ? AND f.owner_id = ? "
                "ORDER BY rank LIMIT ?",
                (fts_query, user_id, limit),
            ) as cur:
                own = [dict(r) for r in await cur.fetchall()]
        except aiosqlite.OperationalError:
            logger.debug("FTS5 unavailable for search_memory")
            own = []

        # Also search partner's non-private facts if in a relationship
        partner_results: list[dict[str, Any]] = []
        if relationship_id:
            try:
                async with db.execute(
                    "SELECT user_a_id, user_b_id FROM relationships WHERE id = ?",
                    (relationship_id,),
                ) as cur:
                    rel = await cur.fetchone()
                if rel:
                    partner_id = rel["user_b_id"] if user_id == rel["user_a_id"] else rel["user_a_id"]
                    async with db.execute(
                        "SELECT f.id, f.text, f.kind, f.visibility, f.created_at "
                        "FROM facts_fts JOIN facts f ON f.id = facts_fts.rowid "
                        "WHERE facts_fts MATCH ? AND f.owner_id = ? "
                        "AND f.visibility IN ('nudge_eligible', 'shareable') "
                        "ORDER BY rank LIMIT ?",
                        (fts_query, partner_id, limit // 2),
                    ) as cur:
                        partner_results = [dict(r) for r in await cur.fetchall()]
            except aiosqlite.OperationalError:
                pass

    # Merge, deduplicate, and limit
    all_results = own + partner_results
    all_results.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return all_results[:limit]


def format_tool_results(results: list[dict[str, Any]]) -> str:
    """Format search results for injection back into the LLM conversation."""
    if not results:
        return "No memories found for that topic."
    lines = []
    for r in results:
        kind = r.get("kind", "working")
        text = r.get("text", "")
        created = (r.get("created_at") or "")[:10]
        lines.append(f"[{kind}] ({created}) {text}")
    return "\n".join(lines)
