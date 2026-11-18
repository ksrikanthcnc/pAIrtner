"""Context builder — assembles the prompt context for a user.

Canonical implementation of the context-building logic.
"""
from __future__ import annotations

from typing import Any


async def get_user_context_data(
    db_path: str, user_id: int
) -> dict[str, Any]:
    """Gather all context data for prompt building.

    Returns a dict with keys: user_name, partner_name, private_facts,
    shared_facts, recent_messages, lang.

    Facts are formatted with IDs, kind labels, and status when non-active.
    Core facts are listed first (always included), then working (ranked by
    status: active > improving > resolved > outdated), then episodes if any.
    """
    from pairtner.storage.users import get_user
    from pairtner.storage.facts import get_active_facts
    from pairtner.storage.messages import get_recent_messages
    from pairtner.storage.profiles import get_user_profile

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

    # Build profile summary for prompt (always available, even with no facts)
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

    all_private = await get_active_facts(db_path, user_id, "private")

    # Sort by kind priority, then status priority within kind
    _KIND_ORDER = {"core": 0, "episode": 1, "working": 2, "partner_view": 3}
    _STATUS_ORDER = {"active": 0, "improving": 1, "resolved": 2, "outdated": 3}

    def _sort_key(f: dict) -> tuple[int, int, str]:
        kind = f.get("kind") or "working"
        status = f.get("status") or "active"
        created = f.get("created_at") or ""
        return (_KIND_ORDER.get(kind, 9), _STATUS_ORDER.get(status, 9), created)

    all_private.sort(key=_sort_key)

    private_facts: list[str] = []
    kind_words: dict[str, int] = {}

    # Budget caps (words per kind) -- loaded from pack or defaults
    budgets = _get_context_budgets()

    for f in all_private:
        kind = f.get("kind") or "working"
        status = f.get("status") or "active"
        prefix = f"[{f['id']}]"
        if kind == "core":
            prefix += " (core)"
        elif kind == "episode":
            prefix += " (episode)"
        suffix = ""
        if status != "active":
            suffix = f" [{status}]"
        line = f"{prefix} {f['text']}{suffix}"

        # Check budget cap
        line_words = len(line.split())
        budget_key = kind if kind in budgets else "working"
        current = kind_words.get(budget_key, 0)
        cap = budgets.get(budget_key, 400)
        if current + line_words > cap and current > 0:
            continue  # Over budget, skip (but allow at least one item)

        private_facts.append(line)
        kind_words[budget_key] = current + line_words

    partner_id = user.get("partner_id")
    partner_name = None
    if partner_id:
        shared_facts = [
            f"[{f['id']}] {f['text']}"
            for f in await get_active_facts(db_path, user_id, "shared")
        ]
        partner_shared = [
            f["text"]  # no IDs for partner's shared items (privacy)
            for f in await get_active_facts(db_path, partner_id, "shared")
        ]
        # Merge without duplicates while preserving order
        seen = set(shared_facts)
        for t_ in partner_shared:
            if t_ not in seen:
                shared_facts.append(t_)
                seen.add(t_)

        # Apply shared budget
        shared_cap = budgets.get("shared", 150)
        shared_facts = _apply_word_cap(shared_facts, shared_cap)
        partner = await get_user(db_path, partner_id)
        partner_name = partner["name"] if partner else None
    else:
        shared_facts = [
            f"[{f['id']}] {f['text']}" for f in await get_active_facts(db_path, user_id, "shared")
        ]

    recent = await get_recent_messages(db_path, user_id)
    # Reverse so oldest is first
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
        return {"core": 250, "shared": 150, "partner_view": 150, "working": 400, "episodes": 300}


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
