"""Context builder — assembles the prompt context for a user.

Uses the Memory API's relevance scoring for fact selection.
"""
from __future__ import annotations

from typing import Any


async def get_user_context_data(
    db_path: str, user_id: int, current_message: str | None = None,
) -> dict[str, Any]:
    """Gather all context data for prompt building.

    Returns a dict with keys: user_name, partner_name, private_facts,
    shared_facts, recent_messages, lang, profile_summary.

    Facts are ranked by relevance score + FTS5 search for current message.
    """
    from pairtner.storage.users import get_user
    from pairtner.storage.facts import get_active_facts
    from pairtner.storage.messages import get_recent_messages
    from pairtner.storage.profiles import get_user_profile
    from pairtner.core.memory_api import MemoryAPI

    user = await get_user(db_path, user_id)
    if not user:
        return {
            "user_name": "",
            "partner_name": None,
            "private_facts": [],
            "shared_facts": [],
            "recent_messages": [],
            "lang": "en",
            "profile_summary": "",
        }

    # Build profile summary
    profile = await get_user_profile(db_path, user_id)
    profile_lines: list[str] = []
    if profile:
        if profile.get("relationship_type"):
            profile_lines.append(f"Relationship: {profile['relationship_type']}")
        if profile.get("duration_years"):
            profile_lines.append(f"Together: {profile['duration_years']} years")
        if profile.get("has_kids") is not None:
            kids = "yes" if profile["has_kids"] else "no"
            profile_lines.append(f"Has kids: {kids}")
    profile_summary = "; ".join(profile_lines) if profile_lines else ""

    # Use Memory API for scored context + FTS5 search
    api = MemoryAPI(db_path)
    budgets = _get_context_budgets()
    total_budget = budgets.get("total", 1200)

    ctx = await api.get_scored_context(
        user_id, budget_words=total_budget, current_message=current_message,
    )
    private_facts = ctx.lines

    # Shared facts from partner
    partner_id = user.get("partner_id")
    partner_name = None
    if partner_id:
        shared_facts = [
            f"[{f['id']}] {f['text']}"
            for f in await get_active_facts(db_path, user_id, "shared")
        ]
        partner_shared = [
            f["text"]
            for f in await get_active_facts(db_path, partner_id, "shared")
        ]
        seen = set(shared_facts)
        for t_ in partner_shared:
            if t_ not in seen:
                shared_facts.append(t_)
                seen.add(t_)

        shared_cap = budgets.get("shared", 150)
        shared_facts = _apply_word_cap(shared_facts, shared_cap)
        partner = await get_user(db_path, partner_id)
        partner_name = partner["name"] if partner else None
    else:
        shared_facts = [
            f"[{f['id']}] {f['text']}" for f in await get_active_facts(db_path, user_id, "shared")
        ]

    recent = await get_recent_messages(db_path, user_id)
    recent_messages = [(m["role"], m["content"]) for m in reversed(recent)]

    return {
        "user_name": user.get("name") or "",
        "partner_name": partner_name,
        "private_facts": private_facts,
        "shared_facts": shared_facts,
        "recent_messages": recent_messages,
        "lang": user.get("lang") or "en",
        "profile_summary": profile_summary,
    }


def _get_context_budgets() -> dict[str, int]:
    """Load context budgets from the active pack, or use defaults."""
    try:
        from pairtner import pack as pack_mod
        p = pack_mod.current()
        manifest = p._state.manifest or {}
        budgets_raw = manifest.get("budgets") or {}
        return {k: int(v) for k, v in budgets_raw.items() if isinstance(v, (int, float))}
    except Exception:
        return {
            "core": 250, "shared": 150, "partner_view": 150,
            "working": 400, "episodes": 300, "total": 1200,
        }


def _apply_word_cap(items: list[str], cap: int) -> list[str]:
    """Keep items until the cumulative word count exceeds *cap*."""
    result: list[str] = []
    total = 0
    for item in items:
        words = len(item.split())
        if total + words > cap and total > 0:
            break
        result.append(item)
        total += words
    return result
