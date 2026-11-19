"""Memory engine — parse, validate, and apply LLM-proposed memory operations.

This module is the safety net between the LLM and the database. Every memory_op
the model proposes passes through here. The engine:
- Validates ownership (only current user's memories)
- Validates op types and fields
- Forces private visibility (model can never share)
- Enforces the ops-per-message cap
- Detects explicit core requests (keyword-based, not LLM-based)
- Merges near-duplicates instead of adding
- Logs every proposed op and its outcome

The module does NOT touch the database schema — that's db.py.
It does NOT build prompts — that's prompts.py + pack.py.
It ONLY validates and applies ops.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MemoryOp:
    """A parsed, validated memory operation."""
    op: str              # add, revise, status, propose_core, pin_core
    kind: str = ""       # working, core (for add)
    text: str = ""
    id: int | None = None       # fact id (for revise, status)
    status: str = ""            # new status (for status op)
    why: str = ""               # reason for status change
    category: str | None = None
    fact_type: str = "stable"
    intensity: float = 0.5      # v2: how strongly user feels (0.0-1.0)
    event_date: str | None = None  # v2: structured date (YYYY-MM-DD) from LLM
    recurring: bool = False        # v2: True for birthdays/anniversaries


@dataclass
class OpOutcome:
    """Result of applying one memory op."""
    op: MemoryOp
    outcome: str         # applied, merged, downgraded, rejected
    reason: str = ""
    memory_id: int | None = None  # the affected fact id
    follow_up: str | None = None  # message to send to user (e.g. confirm button)


@dataclass
class OpsResult:
    """Result of applying all memory_ops from one LLM response."""
    outcomes: list[OpOutcome] = field(default_factory=list)
    propose_core_items: list[dict[str, Any]] = field(default_factory=list)  # items needing user confirm


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _normalise_text(text: str) -> str:
    """Normalise text for comparison: casefold, NFKC, strip punctuation, collapse spaces."""
    text = unicodedata.normalize("NFKC", text)
    text = text.casefold()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def is_near_duplicate(new_text: str, existing_text: str, threshold: float = 0.85) -> bool:
    """Check if two memory texts are near-duplicates.

    Returns True if normalised texts are equal or SequenceMatcher ratio >= threshold.
    """
    norm_new = _normalise_text(new_text)
    norm_existing = _normalise_text(existing_text)
    if norm_new == norm_existing:
        return True
    return difflib.SequenceMatcher(None, norm_new, norm_existing).ratio() >= threshold


# ---------------------------------------------------------------------------
# Explicit core request detection
# ---------------------------------------------------------------------------

# Default phrases (English + Telugu). The pack can override via core_explicit_phrases
# in strings.yaml.
DEFAULT_CORE_PHRASES = [
    "remember this about me",
    "remember that about me",
    "this is about us",
    "this is how we are",
    "note this about me",
    "this is important about me",
    "please remember",
    "don't forget this about me",
    "dont forget this about me",
    # Telugu
    "ఇది నా గురించి గుర్తుంచుకో",
    "ఇది మా గురించి గుర్తుంచుకో",
    "దయచేసి గుర్తుపెట్టుకో",
]


def is_explicit_core_request(
    user_message: str,
    phrases: list[str] | None = None,
) -> bool:
    """Detect if the user explicitly asked to remember something as core.

    Detection is keyword-based in the engine, NOT LLM-based.
    The phrases come from the pack's strings.yaml or the defaults above.
    """
    if phrases is None:
        phrases = DEFAULT_CORE_PHRASES

    # Also check for /remember command or #remember tag
    msg_stripped = user_message.strip()
    if msg_stripped.startswith("/remember") or "#remember" in msg_stripped.lower():
        return True

    normalised = _normalise_text(user_message)
    for phrase in phrases:
        if _normalise_text(phrase) in normalised:
            return True
    return False


# ---------------------------------------------------------------------------
# Op parsing and validation
# ---------------------------------------------------------------------------

VALID_OPS = {"add", "revise", "status", "propose_core", "pin_core"}


def _parse_intensity(raw: Any) -> float:
    """Parse intensity value, clamping to 0.0-1.0 range."""
    if raw is None:
        return 0.5
    try:
        val = float(raw)
        return max(0.0, min(1.0, val))
    except (ValueError, TypeError):
        return 0.5


def _parse_event_date(raw: Any) -> str | None:
    """Validate and return a YYYY-MM-DD date string, or None if invalid."""
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    try:
        from datetime import datetime as _dt
        _dt.strptime(raw, "%Y-%m-%d")
        return raw
    except ValueError:
        return None
VALID_KINDS = {"working", "core"}
VALID_STATUSES = {"active", "improving", "resolved", "outdated"}
VALID_CATEGORIES = {
    "relationship", "household", "emotional", "work", "health", "other", None,
}
VALID_FACT_TYPES = {"transient", "situational", "stable"}


def parse_memory_ops(
    raw_ops: Any,
    user_fact_ids: set[int],
    *,
    max_ops: int = 5,
) -> list[MemoryOp]:
    """Parse and validate raw memory_ops from LLM output.

    Args:
        raw_ops: The memory_ops field from the LLM JSON response.
        user_fact_ids: Set of fact IDs belonging to the current user
            (only these can be referenced by revise/status).
        max_ops: Maximum number of ops to accept.

    Returns:
        List of validated MemoryOp objects. Invalid ops are logged and dropped.
    """
    if not isinstance(raw_ops, list):
        if raw_ops is not None:
            logger.debug("memory_ops is not a list: %s", type(raw_ops).__name__)
        return []

    valid: list[MemoryOp] = []

    for i, raw in enumerate(raw_ops):
        if i >= max_ops:
            logger.info(
                "Dropped %d memory_ops over cap of %d",
                len(raw_ops) - max_ops, max_ops,
            )
            break

        if not isinstance(raw, dict):
            logger.debug("memory_ops[%d] is not a dict, skipping", i)
            continue

        op_type = str(raw.get("op", "")).strip().lower()
        if op_type not in VALID_OPS:
            logger.debug("Unknown memory op '%s', rejected", op_type)
            continue

        # --- add ---
        if op_type == "add":
            text = str(raw.get("text", "")).strip()
            if not text:
                continue
            kind = str(raw.get("kind", "working")).strip().lower()
            if kind == "core":
                # Model tried to add core directly — downgrade to propose_core
                valid.append(MemoryOp(op="propose_core", text=text))
                logger.debug("add with kind=core downgraded to propose_core")
                continue
            if kind not in VALID_KINDS:
                kind = "working"

            category = raw.get("category")
            if category not in VALID_CATEGORIES:
                category = None
            fact_type = str(raw.get("fact_type", "stable")).strip().lower()
            if fact_type not in VALID_FACT_TYPES:
                fact_type = "stable"

            # Visibility is ALWAYS forced to private
            valid.append(MemoryOp(
                op="add", kind=kind, text=text,
                category=category, fact_type=fact_type,
                intensity=_parse_intensity(raw.get("intensity")),
                event_date=_parse_event_date(raw.get("date")),
                recurring=bool(raw.get("recurring", False)),
            ))

        # --- revise ---
        elif op_type == "revise":
            ref_id = raw.get("id")
            if ref_id is None:
                continue
            try:
                ref_id = int(ref_id)
            except (ValueError, TypeError):
                continue
            if ref_id not in user_fact_ids:
                logger.debug("revise op references id %d not in user's set, rejected", ref_id)
                continue
            text = str(raw.get("text", "")).strip()
            if not text:
                continue
            valid.append(MemoryOp(op="revise", id=ref_id, text=text))

        # --- status ---
        elif op_type == "status":
            ref_id = raw.get("id")
            if ref_id is None:
                continue
            try:
                ref_id = int(ref_id)
            except (ValueError, TypeError):
                continue
            if ref_id not in user_fact_ids:
                logger.debug("status op references id %d not in user's set, rejected", ref_id)
                continue
            status = str(raw.get("status", "")).strip().lower()
            if status not in VALID_STATUSES:
                logger.debug("Invalid status '%s', rejected", status)
                continue
            why = str(raw.get("why", "")).strip()
            valid.append(MemoryOp(op="status", id=ref_id, status=status, why=why))

        # --- propose_core ---
        elif op_type == "propose_core":
            text = str(raw.get("text", "")).strip()
            if not text:
                continue
            valid.append(MemoryOp(op="propose_core", text=text))

        # --- pin_core ---
        elif op_type == "pin_core":
            text = str(raw.get("text", "")).strip()
            if not text:
                continue
            # Acceptance is decided by the caller (is_explicit_core_request).
            # We just parse it here.
            valid.append(MemoryOp(op="pin_core", text=text))

    return valid


async def apply_memory_ops(
    db_path: str,
    user_id: int,
    ops: list[MemoryOp],
    user_message: str,
    *,
    message_id: int | None = None,
    allow_pin: bool = True,
    core_phrases: list[str] | None = None,
    existing_facts: list[dict[str, Any]] | None = None,
) -> OpsResult:
    """Apply validated memory ops to the database.

    This is the core engine function. It:
    1. Checks pin_core against explicit request detection
    2. Checks for near-duplicates before adding
    3. Applies each op to the database
    4. Returns outcomes and any items needing user confirmation

    Args:
        db_path: Path to the SQLite database.
        user_id: The current user's ID.
        ops: Validated MemoryOp list from parse_memory_ops.
        user_message: The user's raw message (for pin_core detection).
        message_id: The message row ID (for audit trail).
        allow_pin: Whether pin_core is allowed (False for /tell, #send).
        core_phrases: Override phrases for explicit core detection.
        existing_facts: User's current facts (for dedup). Fetched if None.
    """
    # Import here to avoid circular imports
    from pairtner import db as db_mod

    result = OpsResult()

    # Fetch existing facts for dedup if not provided
    if existing_facts is None:
        existing_facts = await db_mod.get_facts(db_path, user_id)

    existing_texts = {f["id"]: f["text"] for f in existing_facts}

    # Detect explicit core request once
    explicit_core = is_explicit_core_request(user_message, core_phrases)

    for op in ops:
        # --- add ---
        if op.op == "add":
            # Check near-duplicate
            for fact_id, fact_text in existing_texts.items():
                if is_near_duplicate(op.text, fact_text):
                    logger.info(
                        "Near-duplicate detected: '%s' ≈ fact %d '%s', merging",
                        op.text[:50], fact_id, fact_text[:50],
                    )
                    await db_mod.log_memory_op(
                        db_path, user_id, json.dumps({"op": "add", "text": op.text[:100]}),
                        "merged", reason=f"near-duplicate of fact {fact_id}", fact_id=fact_id,
                    )
                    result.outcomes.append(OpOutcome(
                        op=op, outcome="merged",
                        reason=f"near-duplicate of fact {fact_id}",
                        memory_id=fact_id,
                    ))
                    break
            else:
                # No duplicate — add new fact
                fact_id = await db_mod.add_fact(
                    db_path, user_id, op.text, "private",
                    category=op.category, fact_type=op.fact_type,
                    source_message_id=message_id,
                    intensity=op.intensity,
                    event_date=op.event_date,
                    recurring=op.recurring,
                )
                await db_mod.append_memory_version(
                    db_path, fact_id, op.text, "active", "add",
                    source_msg_id=message_id,
                )
                await db_mod.log_memory_op(
                    db_path, user_id, json.dumps({"op": "add", "text": op.text[:100]}),
                    "applied", fact_id=fact_id,
                )
                existing_texts[fact_id] = op.text
                result.outcomes.append(OpOutcome(
                    op=op, outcome="applied", memory_id=fact_id,
                ))

        # --- revise ---
        elif op.op == "revise" and op.id is not None:
            old_text = existing_texts.get(op.id, "")
            if _normalise_text(op.text) == _normalise_text(old_text):
                result.outcomes.append(OpOutcome(
                    op=op, outcome="rejected", reason="unchanged text",
                ))
                continue

            await db_mod.update_fact_text(db_path, op.id, op.text)
            await db_mod.append_memory_version(
                db_path, op.id, op.text, "active", "revise",
                source_msg_id=message_id,
            )
            await db_mod.log_memory_op(
                db_path, user_id, json.dumps({"op": "revise", "id": op.id, "text": op.text[:100]}),
                "applied", fact_id=op.id,
            )
            existing_texts[op.id] = op.text
            result.outcomes.append(OpOutcome(
                op=op, outcome="applied", memory_id=op.id,
            ))

        # --- status ---
        elif op.op == "status" and op.id is not None:
            # Core memories can't be resolved/outdated
            fact = next((f for f in existing_facts if f["id"] == op.id), None)
            if fact and fact.get("kind") == "core" and op.status in ("resolved", "outdated"):
                result.outcomes.append(OpOutcome(
                    op=op, outcome="rejected",
                    reason="core memories cannot be resolved/outdated",
                ))
                continue

            await db_mod.set_fact_status(db_path, op.id, op.status)
            await db_mod.append_memory_version(
                db_path, op.id, existing_texts.get(op.id, ""), op.status, "status",
                reason=op.why, source_msg_id=message_id,
            )
            await db_mod.log_memory_op(
                db_path, user_id,
                json.dumps({"op": "status", "id": op.id, "status": op.status}),
                "applied", fact_id=op.id,
            )
            result.outcomes.append(OpOutcome(
                op=op, outcome="applied", memory_id=op.id,
            ))

        # --- propose_core ---
        elif op.op == "propose_core":
            # Store as working (nothing lost if user ignores) and queue for confirm
            fact_id = await db_mod.add_fact(
                db_path, user_id, op.text, "private",
                category=op.category or "relationship",
                fact_type="stable",
                source_message_id=message_id,
            )
            existing_texts[fact_id] = op.text
            result.propose_core_items.append({
                "id": fact_id, "text": op.text,
            })
            result.outcomes.append(OpOutcome(
                op=op, outcome="applied", memory_id=fact_id,
                follow_up="confirm_core",
            ))

        # --- pin_core ---
        elif op.op == "pin_core":
            if not allow_pin:
                # In relay context, downgrade to propose_core
                result.propose_core_items.append({"text": op.text})
                result.outcomes.append(OpOutcome(
                    op=op, outcome="downgraded",
                    reason="pin_core not allowed in relay context",
                ))
                continue

            if not explicit_core:
                # No explicit command — downgrade to propose_core
                fact_id = await db_mod.add_fact(
                    db_path, user_id, op.text, "private",
                    fact_type="stable",
                    source_message_id=message_id,
                )
                result.propose_core_items.append({
                    "id": fact_id, "text": op.text,
                })
                result.outcomes.append(OpOutcome(
                    op=op, outcome="downgraded",
                    reason="no explicit core request detected",
                    memory_id=fact_id,
                    follow_up="confirm_core",
                ))
                continue

            # Explicit request — check for near-duplicate to promote instead of add
            for fact_id, fact_text in existing_texts.items():
                if is_near_duplicate(op.text, fact_text):
                    await db_mod.promote_to_core(db_path, fact_id)
                    result.outcomes.append(OpOutcome(
                        op=op, outcome="applied",
                        reason=f"promoted existing fact {fact_id} to core",
                        memory_id=fact_id,
                    ))
                    break
            else:
                fact_id = await db_mod.add_fact(
                    db_path, user_id, op.text, "private",
                    fact_type="stable",
                    source_message_id=message_id,
                )
                await db_mod.promote_to_core(db_path, fact_id)
                existing_texts[fact_id] = op.text
                result.outcomes.append(OpOutcome(
                    op=op, outcome="applied", memory_id=fact_id,
                ))

    return result


# ---------------------------------------------------------------------------
# Partner theme recording
# ---------------------------------------------------------------------------

async def record_partner_theme(
    db_path: str,
    user_id: int,
    about_id: int,
    theme: str,
    sentiment: str = "neutral",
    confidence: float = 0.5,
) -> None:
    """Record or merge a partner theme.

    If a near-duplicate theme exists (same observer, same about), merge
    (bump count) instead of adding a new row.
    """
    from pairtner import db as db_mod

    # Check existing perceptions for near-duplicate
    existing = await db_mod.get_partner_perceptions(db_path, about_id)
    for p in existing:
        if p.get("user_id") == user_id and is_near_duplicate(theme, p.get("theme", ""), threshold=0.8):
            logger.debug("Merging partner theme: '%s' ≈ '%s'", theme[:40], p["theme"][:40])
            # For now, just don't add a duplicate. Full merge (mention_count) comes with schema migration.
            return

    await db_mod.add_partner_perception(
        db_path, user_id, about_id, theme,
        sentiment=sentiment, confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Context formatting (ids in prompts)
# ---------------------------------------------------------------------------

def format_memory_line(fact: dict[str, Any], show_id: bool = True) -> str:
    """Format a single memory for inclusion in the prompt.

    Examples:
        [41] Son is 6 years old
        [17] (resolved) Dishes argument
        - Feels unheard about household decisions  (partner_view, no id)
    """
    text = fact.get("text", "")
    status = fact.get("status", "active")

    if not show_id:
        return f"- {text}"

    fact_id = fact.get("id", "?")
    if status and status != "active":
        return f"[{fact_id}] ({status}) {text}"
    return f"[{fact_id}] {text}"
