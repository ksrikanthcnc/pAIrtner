"""v2 nudge engine -- lifecycle (pending -> delivered | cancelled | expired).

Privacy: nothing here ever SELECTs ``facts.text`` for a nudge. Callers get
``nudge_suggestion`` + ``nudge_type`` only (structural isolation; decisions
201-206, 233, 265, 274, 295, 309).
"""

from __future__ import annotations

import logging
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pairtner.storage.helpers import _fetch_all

log = logging.getLogger(__name__)

import aiosqlite

DEFAULT_TTL_H: dict[str, int] = {
    "appreciation": 168,
    "attention": 120,
    "communication": 336,
    "support": 72,
    "quality_time": 168,
    "conflict_prevention": 336,
}
FALLBACK_TTL_H = 168

_QUESTIONS_PATH = Path(__file__).resolve().parents[2] / "packs" / "default" / "random_questions.md"


def load_ttl_config(path: str | Path | None) -> dict[str, int]:
    """TTLs per nudge type, overridden by ``nudges.ttl_h`` in a scoring.yaml."""
    ttl = dict(DEFAULT_TTL_H)
    if path and Path(path).exists():
        import yaml

        data = yaml.safe_load(Path(path).read_text()) or {}
        for k, v in ((data.get("nudges") or {}).get("ttl_h") or {}).items():
            ttl[str(k)] = int(v)
    return ttl


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _ttl_hours(row: dict[str, Any], ttl_cfg: dict[str, int]) -> int:
    if row["nudge_ttl_h"] is not None:
        return int(row["nudge_ttl_h"])
    return ttl_cfg.get(row["nudge_type"] or "", FALLBACK_TTL_H)


def _is_expired(row: dict[str, Any], now: datetime, ttl_cfg: dict[str, int]) -> bool:
    created = _parse(row["created_at"])
    return created is not None and now >= created + timedelta(hours=_ttl_hours(row, ttl_cfg))


_NUDGE_COLS = "id, nudge_type, nudge_ttl_h, nudge_cooldown_h, created_at"


async def _pending_rows(db: aiosqlite.Connection, where: str, params: tuple) -> list[dict[str, Any]]:
    db.row_factory = aiosqlite.Row
    async with db.execute(
        f"SELECT {_NUDGE_COLS}, nudge_suggestion FROM facts "
        f"WHERE nudge_status = 'pending' AND nudge_target_id IS NOT NULL "
        f"AND archived_at IS NULL AND {where} ORDER BY created_at, id",
        params,
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def get_pending_nudges(
    db_path: str, target_user_id: int, max_count: int = 5,
    ttl_cfg: dict[str, int] | None = None,
) -> list[dict]:
    """Pending nudges for a target user: ``nudge_suggestion`` + ``nudge_type`` (+ id) only.

    Filters: status pending, cooldown elapsed, TTL not exceeded.
    """
    cfg = ttl_cfg or DEFAULT_TTL_H
    now = _now()
    async with aiosqlite.connect(db_path) as db:
        rows = await _pending_rows(
            db, "nudge_target_id = ? AND nudge_suggestion IS NOT NULL AND nudge_suggestion != ''",
            (target_user_id,),
        )
    out: list[dict] = []
    for r in rows:
        created = _parse(r["created_at"])
        if created is None or _is_expired(r, now, cfg):
            continue
        if now < created + timedelta(hours=r["nudge_cooldown_h"] or 0):
            continue
        out.append({"fact_id": r["id"], "nudge_type": r["nudge_type"],
                    "nudge_suggestion": r["nudge_suggestion"]})
        if len(out) >= max_count:
            break
    return out


async def deliver_nudge(db_path: str, fact_id: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE facts SET nudge_status = 'delivered', nudge_delivered_at = ?, "
            "updated_at = ? WHERE id = ? AND nudge_status = 'pending'",
            (_fmt(_now()), _fmt(_now()), fact_id),
        )
        await db.commit()


async def cancel_nudge(db_path: str, fact_id: int, reason: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE facts SET nudge_status = 'cancelled', nudge_cancelled_at = ?, "
            "nudge_cancel_reason = ?, updated_at = ? WHERE id = ? AND nudge_status = 'pending'",
            (_fmt(_now()), reason, _fmt(_now()), fact_id),
        )
        await db.commit()


async def _expire_ids(db: aiosqlite.Connection, ids: list[int]) -> None:
    if ids:
        await db.executemany(
            "UPDATE facts SET nudge_status = 'expired', updated_at = ? WHERE id = ?",
            [(_fmt(_now()), i) for i in ids],
        )


async def expire_stale_nudges(db_path: str, ttl_cfg: dict[str, int] | None = None) -> int:
    cfg = ttl_cfg or DEFAULT_TTL_H
    now = _now()
    async with aiosqlite.connect(db_path) as db:
        rows = await _pending_rows(db, "1=1", ())
        ids = [r["id"] for r in rows if _is_expired(r, now, cfg)]
        await _expire_ids(db, ids)
        await db.commit()
    return len(ids)


async def enforce_max_active(db_path: str, relationship_id: str, max_active: int = 5) -> int:
    """Keep at most *max_active* pending nudges; expire the oldest extras."""
    async with aiosqlite.connect(db_path) as db:
        rows = await _pending_rows(db, "relationship_id = ?", (relationship_id,))
        excess = max(0, len(rows) - max_active)
        ids = [r["id"] for r in rows[:excess]]  # ordered oldest first
        await _expire_ids(db, ids)
        await db.commit()
    return len(ids)


# ---------------------------------------------------------------------------
# Camouflage questions
# ---------------------------------------------------------------------------

def load_question_bank(path: str | Path | None = None) -> dict[str, list[str]]:
    """Parse ``## category`` / ``- question`` markdown into {category: [questions]}."""
    bank: dict[str, list[str]] = {}
    cat: str | None = None
    for line in Path(path or _QUESTIONS_PATH).read_text().splitlines():
        m = re.match(r"##\s+(\S+)", line)
        if m:
            cat = m.group(1).lower()
            bank.setdefault(cat, [])
        elif cat and line.startswith("- "):
            bank[cat].append(line[2:].strip())
    return bank


def questions_for_activity(messages_last_week: int, rng: random.Random | None = None) -> int:
    """quiet = 1/week, normal = 2-3, active = 3-5."""
    r = rng or random
    if messages_last_week < 5:
        return 1
    if messages_last_week < 30:
        return r.randint(2, 3)
    return r.randint(3, 5)


async def count_recent_messages(db_path: str, user_id: int, days: int = 7) -> int:
    since = _fmt(_now() - timedelta(days=days))
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM messages WHERE user_id = ? AND role = 'user' "
            "AND deleted_at IS NULL AND created_at >= ?", (user_id, since),
        ) as cur:
            return (await cur.fetchone())[0]


async def get_camouflage_questions(
    db_path: str, user_id: int, count: int | None = 1,
    bank_path: str | Path | None = None, rng: random.Random | None = None,
) -> list[str]:
    """Random check-in questions, spread across categories.

    ``count=None`` scales with the user's activity over the last 7 days.
    """
    r = rng or random
    if count is None:
        count = questions_for_activity(await count_recent_messages(db_path, user_id), r)
    bank = load_question_bank(bank_path)
    cats = [c for c, qs in bank.items() if qs]
    picked: list[str] = []
    pool = {c: list(bank[c]) for c in cats}
    while len(picked) < count and any(pool.values()):
        cat = r.choice([c for c in cats if pool[c]])
        picked.append(pool[cat].pop(r.randrange(len(pool[cat]))))
    return picked


# ---------------------------------------------------------------------------
# Partner-informed question generation (non-attributable)
# ---------------------------------------------------------------------------

_QUESTION_GEN_PROMPT = """\
You are generating natural check-in questions for a person. These questions
should feel casual and generic — the person must NOT be able to tell that you
know specific information about their partner.

Given these facts about their partner (which you must NEVER reveal directly):
{partner_facts}

Generate {count} natural questions that could surface a conversation about
these topics WITHOUT revealing that you know anything. Mix in topics the
questions could plausibly ask anyone.

Rules:
- Questions must be generic enough that ANYONE could be asked them
- Never reference the partner, their name, or specific details
- Vary the tone: some light, some reflective
- One question per line, no numbering, no bullets

Examples of GOOD questions (given "partner stressed about work"):
- How's work been treating you lately?
- What's taking up most of your mental energy these days?

Examples of BAD questions (too specific, reveals knowledge):
- Is your partner stressed about work?
- How is [partner name]'s job going?
"""


async def generate_partner_informed_questions(
    db_path: str,
    user_id: int,
    partner_id: int,
    llm_fn,
    *,
    count: int = 3,
) -> list[str]:
    """Generate questions informed by partner's shared facts.

    These look generic but are designed to surface topics the partner
    cares about. Mixed into the camouflage question pool so the user
    can't tell which questions are informed by partner context.

    Args:
        db_path: database path
        user_id: the user who will RECEIVE these questions
        partner_id: the user whose shared facts inform the questions
        llm_fn: async callable(system, user) -> str (the LLM)
        count: how many questions to generate

    Returns:
        List of question strings, or empty list if no partner facts.
    """
    # Get partner's recent shared facts (what user_id is allowed to see)
    rows = await _fetch_all(
        db_path,
        "SELECT text FROM facts "
        "WHERE owner_id = ? AND visibility = 'shared' "
        "AND status = 'active' AND archived_at IS NULL "
        "ORDER BY updated_at DESC LIMIT 10",
        (partner_id,),
    )

    if not rows:
        return []

    partner_facts = "\n".join(f"- {r['text']}" for r in rows)
    prompt = _QUESTION_GEN_PROMPT.format(
        partner_facts=partner_facts, count=count,
    )

    try:
        raw = await llm_fn(
            "You generate natural, non-attributable check-in questions.",
            prompt,
        )
        questions = [
            line.strip().lstrip("- ").lstrip("•").strip()
            for line in raw.strip().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        return questions[:count]
    except Exception as e:
        log.warning("Failed to generate partner-informed questions: %s", e)
        return []


async def get_mixed_questions(
    db_path: str,
    user_id: int,
    partner_id: int | None = None,
    llm_fn=None,
    *,
    total: int = 3,
    bank_path: str | Path | None = None,
    rng: random.Random | None = None,
) -> list[str]:
    """Get a mix of generic + partner-informed questions.

    If partner_id and llm_fn are provided, generates 1-2 partner-informed
    questions and fills the rest with generic ones. Otherwise, all generic.
    The questions are shuffled so the user can't tell which are informed.
    """
    r = rng or random
    informed: list[str] = []

    if partner_id and llm_fn:
        informed_count = min(2, max(1, total // 2))
        try:
            informed = await generate_partner_informed_questions(
                db_path, user_id, partner_id, llm_fn,
                count=informed_count,
            )
        except Exception:
            pass  # fall back to all generic

    generic_count = total - len(informed)
    generic = await get_camouflage_questions(
        db_path, user_id, count=generic_count,
        bank_path=bank_path, rng=r,
    )

    mixed = informed + generic
    r.shuffle(mixed)
    return mixed
