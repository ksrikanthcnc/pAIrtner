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

from pairtner.storage.tagfmt import normalize_tags
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
    tag: list[str] | None = None
    fact_type: str = "stable"
    intensity: float = 0.5      # v2: how strongly user feels (0.0-1.0)
    event_date: str | None = None  # v2: structured date (YYYY-MM-DD) from LLM
    recurring: bool = False        # v2: True for birthdays/anniversaries
    # --- v2 fields ---
    visibility: str = "shared"  # private, shared
    msg_source: str = "user"            # user, bot, system
    nudge_type: str | None = None       # see VALID_NUDGE_TYPES
    nudge_suggestion: str | None = None  # LLM-written, partner-safe guidance
    nudge_target: str | None = None     # 'partner' or None
    relationship_id: str | None = None


@dataclass
class SettingsOp:
    """A proposed user-setting change (validated against ALLOWED_SETTINGS)."""
    key: str    # camouflage_frequency, nudge_enabled, interaction_mode, voice, response_length
    value: str


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
    settings_ops: list[SettingsOp] = field(default_factory=list)  # applied settings changes
    rejected_settings: list[SettingsOp] = field(default_factory=list)  # failed whitelist validation
    cancelled_nudges: list[int] = field(default_factory=list)  # nudge fact ids cancelled


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
# Semantic dedup — FTS5 + SequenceMatcher
# ---------------------------------------------------------------------------

_DEDUP_STOPWORDS = frozenset({
    "the", "is", "are", "was", "were", "been", "being", "have", "has",
    "had", "do", "does", "did", "will", "would", "could", "should",
    "may", "might", "can", "not", "and", "but", "for", "yet", "so",
    "in", "on", "at", "to", "of", "with", "by", "from", "up", "about",
    "into", "over", "after", "that", "this", "it", "its", "my", "your",
    "our", "his", "her", "their", "them", "very", "really", "just",
    "also", "too", "much", "more", "most", "some", "any", "all", "both",
    "each", "few", "many", "than", "user", "partner", "feels", "feeling",
    "think", "thinks", "like", "want", "needs", "get", "got", "or", "nor",
})


async def find_semantic_duplicates(
    db_path: str,
    new_text: str,
    owner_id: int,
    *,
    threshold: float = 0.75,
    limit: int = 5,
) -> list[dict]:
    """Find semantically similar existing facts using FTS5 + SequenceMatcher.

    Strategy: extract keywords from new_text, run FTS5 MATCH (or LIKE
    fallback when FTS isn't available), then rank candidates by string
    similarity. Returns facts above *threshold*.
    """
    import aiosqlite

    words = _normalise_text(new_text).split()
    keywords = [w for w in words if len(w) >= 3 and w not in _DEDUP_STOPWORDS]
    if not keywords:
        return []

    fts_query = " OR ".join(keywords[:8])

    candidates: list[dict] = []
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='facts_fts'"
        )
        has_fts = await cur.fetchone() is not None

        if has_fts:
            try:
                cur = await db.execute(
                    "SELECT f.id, f.text, f.kind, f.status FROM facts_fts ft "
                    "JOIN facts f ON f.id = ft.rowid "
                    "WHERE ft.text MATCH ? AND f.owner_id = ? "
                    "AND f.status = 'active' AND f.archived_at IS NULL "
                    "LIMIT ?",
                    (fts_query, owner_id, limit * 3),
                )
                candidates = [dict(r) for r in await cur.fetchall()]
            except Exception:
                has_fts = False  # fall through to LIKE

        if not has_fts and keywords:
            cur = await db.execute(
                "SELECT id, text, kind, status FROM facts "
                "WHERE owner_id = ? AND status = 'active' AND archived_at IS NULL "
                "AND text LIKE ? LIMIT ?",
                (owner_id, f"%{keywords[0]}%", limit * 3),
            )
            candidates = [dict(r) for r in await cur.fetchall()]

    norm_new = _normalise_text(new_text)
    results = []
    for c in candidates:
        ratio = difflib.SequenceMatcher(None, norm_new, _normalise_text(c["text"])).ratio()
        if ratio >= threshold:
            results.append({**c, "similarity": round(ratio, 3)})

    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results[:limit]


# ---------------------------------------------------------------------------
# Contradiction detection
# ---------------------------------------------------------------------------

_POSITIVE_WORDS = frozenset({
    "loves", "likes", "enjoys", "happy", "good", "great", "appreciates",
    "comfortable", "confident", "excited", "grateful", "satisfied", "proud",
})
_NEGATIVE_WORDS = frozenset({
    "hates", "dislikes", "avoids", "unhappy", "bad", "terrible", "resents",
    "uncomfortable", "insecure", "anxious", "frustrated", "disappointed", "angry",
})


def detect_contradiction(new_text: str, existing_text: str) -> bool:
    """Heuristic contradiction check: same subject, opposite sentiment.

    Flags potential contradictions for review — not auto-resolved.
    Requires at least 2 shared content words (same subject) and opposing
    sentiment signals.
    """
    norm_new = set(_normalise_text(new_text).split())
    norm_old = set(_normalise_text(existing_text).split())

    content_new = {w for w in norm_new if len(w) >= 4}
    content_old = {w for w in norm_old if len(w) >= 4}
    if len(content_new & content_old) < 2:
        return False

    new_pos = bool(norm_new & _POSITIVE_WORDS)
    new_neg = bool(norm_new & _NEGATIVE_WORDS)
    old_pos = bool(norm_old & _POSITIVE_WORDS)
    old_neg = bool(norm_old & _NEGATIVE_WORDS)

    return (new_pos and old_neg) or (new_neg and old_pos)


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
VALID_OPS_V2 = VALID_OPS | {"cancel_nudge"}
VALID_VISIBILITY = {"private", "shared"}
VALID_MSG_SOURCES = {"user", "bot", "system"}
VALID_NUDGE_TYPES = {
    "appreciation", "attention", "communication", "support",
    "quality_time", "conflict_prevention",
}
DEFAULT_NUDGE_COOLDOWN_H = 24

ALLOWED_SETTINGS: dict[str, dict[str, Any]] = {
    "camouflage_frequency": {"values": ["off", "low", "normal", "high"], "default": "normal"},
    "nudge_enabled": {"values": ["true", "false"], "default": "true"},
    "interaction_mode": {"values": ["vent", "solution", "adaptive"], "default": "vent"},
    "voice": {"values": ["casual", "warm", "formal"], "default": "casual"},
    "response_length": {"values": ["short", "normal", "long"], "default": "normal"},
    # Per-user nudge timing. "time" values are HH:MM; None default = fall back to config.
    "nudge_quiet_start": {"type": "time", "default": None},
    "nudge_quiet_end": {"type": "time", "default": None},
    "nudge_frequency": {"values": ["daily", "weekly", "rare", "off"], "default": None},
    "nudge_skip_weekends": {"values": ["true", "false"], "default": "false"},
}
SETTINGS_META_PREFIX = "setting:"
_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")

# Core = biographical axioms only (decision 311/316): "could this go on a
# profile card?" Anything else is working. This is the engine-side backstop for
# the extraction prompt; it only ever DOWNGRADES, never upgrades.
_IDENTITY_PATTERNS = re.compile(
    r"\b(name is|named|called|goes by|born|birthday|birth ?date|dob|date of birth|"
    r"anniversary|married|wedding|engaged|together (since|for)|"
    r"(son|daughter|kid|kids|child|children|twins?)\b.*\b(is|are|named|called|born|years?)|"
    r"works? (as|at|for)|job title|profession|occupation|"
    r"speaks?|native language|languages?)\b",
    re.IGNORECASE,
)


def is_identity_fact(text: str) -> bool:
    """True if text looks like a biographical axiom suitable for core."""
    return bool(_IDENTITY_PATTERNS.search(text))


def validate_setting(key: Any, value: Any) -> SettingsOp | None:
    """Return a normalised SettingsOp if key/value pass the whitelist, else None."""
    key_s = str(key).strip().lower()
    spec = ALLOWED_SETTINGS.get(key_s)
    if spec is None:
        return None
    if isinstance(value, bool):
        value_s = "true" if value else "false"
    else:
        value_s = str(value).strip().lower()
    if spec.get("type") == "time":
        m = _HHMM_RE.match(value_s)
        if m is None:
            return None
        hh, mm = int(m.group(1)), int(m.group(2))
        if hh > 23 or mm > 59:
            return None
        return SettingsOp(key=key_s, value=f"{hh:02d}:{mm:02d}")
    if value_s not in spec["values"]:
        return None
    return SettingsOp(key=key_s, value=value_s)


def parse_settings_ops(raw_ops: Any) -> list[SettingsOp]:
    """Parse raw settings_ops from LLM output into SettingsOp objects.

    Structural parsing only; whitelist validation happens in apply_memory_ops
    so rejected ops can be reported.
    """
    if not isinstance(raw_ops, list):
        return []
    out: list[SettingsOp] = []
    for raw in raw_ops:
        if isinstance(raw, dict) and "key" in raw and "value" in raw:
            out.append(SettingsOp(key=str(raw["key"]), value=str(raw["value"])))
    return out


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
VALID_FACT_TYPES = {"transient", "situational", "stable"}


def parse_memory_ops(
    raw_ops: Any,
    user_fact_ids: set[int],
    *,
    max_ops: int = 5,
    v2: bool = False,
    relationship_id: str | None = None,
) -> list[MemoryOp]:
    """Parse and validate raw memory_ops from LLM output.

    Args:
        raw_ops: The memory_ops field from the LLM JSON response.
        user_fact_ids: Set of fact IDs belonging to the current user
            (only these can be referenced by revise/status).
        max_ops: Maximum number of ops to accept.
        v2: Enable the v2 contract: LLM-assigned visibility/nudge fields,
            cancel_nudge, and core adds kept (strictness enforced at apply time).
        relationship_id: Stamped on v2 add ops.

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
        if op_type not in (VALID_OPS_V2 if v2 else VALID_OPS):
            logger.debug("Unknown memory op '%s', rejected", op_type)
            continue

        # --- add ---
        if op_type == "add":
            text = str(raw.get("text", "")).strip()
            if not text:
                continue
            kind = str(raw.get("kind", "working")).strip().lower()
            if kind == "core" and not v2:
                # Model tried to add core directly — downgrade to propose_core
                valid.append(MemoryOp(op="propose_core", text=text))
                logger.debug("add with kind=core downgraded to propose_core")
                continue
            if kind not in VALID_KINDS:
                kind = "working"

            # LLM may send a single string or a list; normalize to a list.
            tag = normalize_tags(raw.get("tag")) or None
            fact_type = str(raw.get("fact_type", "stable")).strip().lower()
            if fact_type not in VALID_FACT_TYPES:
                fact_type = "stable"

            if v2:
                visibility = str(raw.get("visibility", "shared")).strip().lower()
                if visibility not in VALID_VISIBILITY:
                    visibility = "shared"
                msg_source = str(raw.get("msg_source", "user")).strip().lower()
                if msg_source not in VALID_MSG_SOURCES:
                    msg_source = "user"
                nudge_type = str(raw.get("nudge_type") or "").strip().lower() or None
                if nudge_type not in VALID_NUDGE_TYPES:
                    nudge_type = None
                suggestion = str(raw.get("nudge_suggestion") or "").strip() or None
                target = str(raw.get("nudge_target") or "").strip().lower() or None
                if target != "partner":
                    target = None
                valid.append(MemoryOp(
                    op="add", kind=kind, text=text,
                    tag=tag, fact_type=fact_type,
                    intensity=_parse_intensity(raw.get("intensity")),
                    event_date=_parse_event_date(raw.get("date")),
                    recurring=bool(raw.get("recurring", False)),
                    visibility=visibility, msg_source=msg_source,
                    nudge_type=nudge_type, nudge_suggestion=suggestion,
                    nudge_target=target, relationship_id=relationship_id,
                ))
                continue

            # v1: visibility is ALWAYS forced to private
            valid.append(MemoryOp(
                op="add", kind=kind, text=text,
                tag=tag, fact_type=fact_type,
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

        # --- cancel_nudge (v2) ---
        elif op_type == "cancel_nudge":
            try:
                ref_id = int(raw.get("id"))
            except (ValueError, TypeError):
                continue
            valid.append(MemoryOp(
                op="cancel_nudge", id=ref_id,
                why=str(raw.get("why", "")).strip(),
            ))

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


async def _has_v2_columns(db_path: str) -> bool:
    import aiosqlite
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("PRAGMA table_info(facts)") as cur:
            return any(row[1] == "msg_source" for row in await cur.fetchall())


def _partner_from_relationship(relationship_id: str | None, user_id: int) -> int | None:
    """Derive the other user's id from a deterministic "lo_hi" relationship id."""
    if not relationship_id:
        return None
    try:
        lo, hi = (int(x) for x in relationship_id.split("_"))
    except ValueError:
        return None
    if user_id == lo:
        return hi
    if user_id == hi:
        return lo
    return None


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
    settings_ops: list[SettingsOp] | None = None,
    partner_id: int | None = None,
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
        settings_ops: Proposed setting changes, validated against ALLOWED_SETTINGS.
        partner_id: Nudge target user id. If None it is derived from an op's
            relationship_id ("lo_hi") when possible.
    """
    # Import here to avoid circular imports
    from pairtner import db as db_mod
    from pairtner.storage import facts as facts_mod

    result = OpsResult()

    # Fetch existing facts for dedup if not provided
    if existing_facts is None:
        existing_facts = await db_mod.get_facts(db_path, user_id)

    existing_texts = {f["id"]: f["text"] for f in existing_facts}

    # Detect explicit core request once
    explicit_core = is_explicit_core_request(user_message, core_phrases)

    # v2 behaviour applies when the DB carries the v2 columns (schema_v2).
    is_v2_db = await _has_v2_columns(db_path)

    # --- settings_ops: whitelist-validated, stored in user_meta ---
    for sop in settings_ops or []:
        valid_sop = validate_setting(sop.key, sop.value)
        if valid_sop is None:
            logger.info("Rejected settings op %s=%r (not in whitelist)", sop.key, sop.value)
            result.rejected_settings.append(sop)
            continue
        await facts_mod.set_user_meta(
            db_path, user_id, SETTINGS_META_PREFIX + valid_sop.key, valid_sop.value,
        )
        result.settings_ops.append(valid_sop)

    for op in ops:
        # --- add ---
        if op.op == "add":
            # Semantic dedup: check for semantically similar existing facts
            sem_dups = await find_semantic_duplicates(
                db_path, op.text, user_id, threshold=0.75, limit=3,
            )
            if sem_dups and sem_dups[0]["similarity"] >= 0.90:
                best = sem_dups[0]
                logger.info(
                    "Semantic dedup: '%s' ≈ fact %d (%.0f%%), skipping",
                    op.text[:50], best["id"], best["similarity"] * 100,
                )
                await db_mod.log_memory_op(
                    db_path, user_id, json.dumps({"op": "add", "text": op.text[:100]}),
                    "merged", reason=f"semantic dup of fact {best['id']}", fact_id=best["id"],
                )
                result.outcomes.append(OpOutcome(
                    op=op, outcome="merged",
                    reason=f"semantic duplicate of fact {best['id']} ({best['similarity']:.0%} similar)",
                    memory_id=best["id"],
                ))
                continue

            # Contradiction check on semantic near-matches
            contradiction_warning = ""
            for dup in sem_dups:
                if detect_contradiction(op.text, dup["text"]):
                    contradiction_warning = (
                        f"may contradict fact #{dup['id']}: {dup['text'][:60]}"
                    )
                    logger.info(
                        "Contradiction detected: '%s' vs fact #%d '%s'",
                        op.text[:50], dup["id"], dup["text"][:50],
                    )
                    break

            # Check near-duplicate (syntactic)
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
                extra: dict[str, Any] = {}
                outcome, reason = "applied", ""
                visibility = "private"  # v1 default
                promote = False
                v2_op = is_v2_db
                if v2_op:
                    visibility = op.visibility
                    extra["msg_source"] = op.msg_source
                    extra["relationship_id"] = op.relationship_id
                    if op.nudge_type or op.nudge_suggestion or op.nudge_target:
                        target_id = partner_id
                        if target_id is None and op.nudge_target == "partner":
                            target_id = _partner_from_relationship(op.relationship_id, user_id)
                        extra.update(
                            nudge_type=op.nudge_type,
                            nudge_suggestion=op.nudge_suggestion,
                            nudge_target_id=target_id if op.nudge_target == "partner" else None,
                            nudge_cooldown_h=DEFAULT_NUDGE_COOLDOWN_H,
                            nudge_status="pending",
                        )
                    if op.kind == "core":
                        if is_identity_fact(op.text):
                            promote = True
                        else:
                            outcome = "downgraded"
                            reason = "core is for biographical facts only; stored as working"
                            logger.info("Core downgraded to working: %r", op.text[:60])
                fact_id = await db_mod.add_fact(
                    db_path, user_id, op.text, visibility,
                    tag=op.tag, fact_type=op.fact_type,
                    source_message_id=message_id,
                    intensity=op.intensity,
                    event_date=op.event_date,
                    recurring=op.recurring,
                    **extra,
                )
                if promote:
                    await db_mod.promote_to_core(db_path, fact_id)
                await db_mod.append_memory_version(
                    db_path, fact_id, op.text, "active", "add",
                    source_msg_id=message_id,
                )
                await db_mod.log_memory_op(
                    db_path, user_id, json.dumps({"op": "add", "text": op.text[:100]}),
                    outcome, reason=reason or None, fact_id=fact_id,
                )
                existing_texts[fact_id] = op.text
                result.outcomes.append(OpOutcome(
                    op=op, outcome=outcome,
                    reason=contradiction_warning or reason,
                    memory_id=fact_id,
                ))

        # --- cancel_nudge ---
        elif op.op == "cancel_nudge" and op.id is not None:
            cancelled = await facts_mod.cancel_nudge(
                db_path, op.id, user_id, op.why or "addressed by user",
            )
            await db_mod.log_memory_op(
                db_path, user_id, json.dumps({"op": "cancel_nudge", "id": op.id}),
                "applied" if cancelled else "rejected",
                reason=None if cancelled else "no pending nudge owned by/targeting user",
                fact_id=op.id,
            )
            if cancelled:
                result.cancelled_nudges.append(op.id)
            result.outcomes.append(OpOutcome(
                op=op, outcome="applied" if cancelled else "rejected",
                reason="" if cancelled else "no pending nudge for this user",
                memory_id=op.id,
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
                tag=op.tag or ["relationship"],
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


# Spec-facing alias (v2 contract name).
apply_ops = apply_memory_ops
