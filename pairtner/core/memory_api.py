"""Memory API — unified interface for all memory operations.

All memory access goes through this module. Consumers:
- LLM (via apply_llm_ops)
- REST API / PWA (via CRUD methods)
- Commands (via CRUD methods)
- Background scheduler (via maintenance methods)

No caller should import from storage.facts or storage.context directly
for write operations. Reads that don't need scoring can still use storage
directly for performance.
"""

from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pairtner.storage.helpers import _now_iso, _to_iso

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Response types
# ---------------------------------------------------------------------------


@dataclass
class Fact:
    """A memory fact with computed relevance score."""

    id: int
    text: str
    kind: str
    status: str
    category: str | None
    fact_type: str
    visibility: str
    relevance_score: float
    citation_count: int
    last_cited_at: str | None
    source_message_id: int | None
    source_ids: str | None
    contradicts_id: int | None
    intensity: float
    model: str | None
    metadata: str | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "kind": self.kind,
            "status": self.status,
            "category": self.category,
            "fact_type": self.fact_type,
            "visibility": self.visibility,
            "relevance_score": round(self.relevance_score, 3),
            "citation_count": self.citation_count,
            "last_cited_at": self.last_cited_at,
            "source_message_id": self.source_message_id,
            "source_ids": self.source_ids,
            "contradicts_id": self.contradicts_id,
            "intensity": self.intensity,
            "model": self.model,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class FactList:
    facts: list[Fact]
    total: int
    offset: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "facts": [f.to_dict() for f in self.facts],
            "total": self.total,
            "offset": self.offset,
        }


@dataclass
class MemoryStats:
    total: int
    by_kind: dict[str, int]
    by_status: dict[str, int]
    avg_score: float
    oldest_uncited_days: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "by_kind": self.by_kind,
            "by_status": self.by_status,
            "avg_score": round(self.avg_score, 3),
            "oldest_uncited_days": self.oldest_uncited_days,
        }


@dataclass
class RecencyReport:
    """Memory health for the PWA panel."""

    healthy: int  # cited in last 7 days
    aging: int  # 7-30 days since last citation
    stale: int  # >30 days, archival candidates
    contradictions: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "aging": self.aging,
            "stale": self.stale,
            "contradictions": self.contradictions,
        }


@dataclass
class Provenance:
    fact: Fact
    source_message: str | None
    source_timestamp: str | None
    version_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact": self.fact.to_dict(),
            "source_message": self.source_message,
            "source_timestamp": self.source_timestamp,
            "version_count": self.version_count,
        }


@dataclass
class ContextResult:
    """Result of building the scored memory context for the LLM prompt."""

    lines: list[str]
    facts_included: int
    total_words: int
    cited_ids: list[int]


@dataclass
class AgingResult:
    archived_transient: int
    marked_outdated: int
    needs_confirmation: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

# Halflife in days per kind (core has infinite halflife = no decay)
_HALFLIFE: dict[str, float] = {
    "core": float("inf"),
    "episode": 180.0,
    "working": 21.0,
    "partner_view": 30.0,
}

_KIND_WEIGHT: dict[str, float] = {
    "core": 1.0,
    "episode": 0.7,
    "working": 0.5,
    "partner_view": 0.4,
}

_STATUS_WEIGHT: dict[str, float] = {
    "active": 1.0,
    "improving": 0.9,
    "resolved": 0.3,
    "outdated": 0.1,
}


def compute_relevance_score(fact_row: dict[str, Any]) -> float:
    """Compute the relevance score for a fact.

    score = status_weight × recency × intensity × citation_boost
    Core facts always score 1.0 (permanent axioms).
    Working facts: simple linear recency decay over 14 days.
    Summaries are handled separately (not ranked by this score).
    """
    kind = fact_row.get("kind") or "working"
    status = fact_row.get("status") or "active"
    citations = fact_row.get("citation_count") or 0
    intensity = fact_row.get("intensity") or 0.5

    status_w = _STATUS_WEIGHT.get(status, 1.0)
    citation_boost = min(1.0 + 0.1 * citations, 1.5)

    # Core facts: always 1.0
    if kind == "core":
        return 1.0

    # Simple linear recency: 1.0 at day 0, 0.0 at day 14+
    # Working facts get summarized within ~7 days, so 14-day window is generous.
    created = fact_row.get("created_at") or ""
    age_days = _age_in_days(created)
    recency = max(0.05, 1.0 - age_days / 14.0)  # floor at 0.05 so old facts aren't invisible

    score = recency * status_w * intensity * citation_boost
    return min(score, 1.0)  # cap at 1.0


def _age_in_days(iso_timestamp: str) -> float:
    """Return the age in days of an ISO timestamp."""
    if not iso_timestamp:
        return 0.0
    try:
        dt = datetime.fromisoformat(iso_timestamp)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return max(delta.total_seconds() / 86400, 0.0)
    except (ValueError, TypeError):
        return 0.0


def _row_to_fact(row: dict[str, Any]) -> Fact:
    """Convert a DB row dict to a Fact dataclass with computed score."""
    return Fact(
        id=row["id"],
        text=row.get("text", ""),
        kind=row.get("kind") or "working",
        status=row.get("status") or "active",
        category=row.get("category"),
        fact_type=row.get("fact_type") or "stable",
        visibility=row.get("visibility") or "private",
        relevance_score=compute_relevance_score(row),
        citation_count=row.get("citation_count") or 0,
        last_cited_at=row.get("last_cited_at"),
        source_message_id=row.get("source_message_id"),
        source_ids=row.get("source_ids"),
        contradicts_id=row.get("contradicts_id"),
        intensity=row.get("intensity") or 0.5,
        model=row.get("model"),
        metadata=row.get("metadata"),
        created_at=row.get("created_at") or "",
        updated_at=row.get("updated_at") or "",
    )


# ---------------------------------------------------------------------------
# Memory API class
# ---------------------------------------------------------------------------


class MemoryAPI:
    """Unified memory interface. All memory access goes through here."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    # --- Read ---

    async def list_facts(
        self,
        user_id: int,
        *,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        scored: bool = False,
    ) -> FactList:
        """List facts for a user, optionally scored and sorted by relevance."""
        from pairtner.storage.facts import get_active_facts, get_facts

        if status == "archived":
            # Special case: show archived facts
            all_facts = await get_facts(self.db_path, user_id)
            rows = [f for f in all_facts if f.get("archived_at")]
        else:
            rows = await get_active_facts(self.db_path, user_id)

        # Filter by kind
        if kind:
            rows = [r for r in rows if (r.get("kind") or "working") == kind]
        # Filter by status (unless we already filtered for archived)
        if status and status != "archived":
            rows = [r for r in rows if (r.get("status") or "active") == status]

        total = len(rows)
        facts = [_row_to_fact(r) for r in rows]

        if scored:
            facts.sort(key=lambda f: f.relevance_score, reverse=True)

        # Paginate
        page = facts[offset : offset + limit]
        return FactList(facts=page, total=total, offset=offset)

    async def get_fact(self, fact_id: int) -> Fact | None:
        """Get a single fact by ID."""
        from pairtner.storage.facts import get_fact_by_id

        row = await get_fact_by_id(self.db_path, fact_id)
        if not row:
            return None
        return _row_to_fact(row)

    async def search(
        self, user_id: int, query: str, limit: int = 10
    ) -> list[Fact]:
        """Search facts using FTS5 or fallback to LIKE."""
        from pairtner.core.search import search_facts

        rows = await search_facts(self.db_path, user_id, query, limit=limit)
        return [_row_to_fact(r) for r in rows]

    async def get_provenance(self, fact_id: int) -> Provenance | None:
        """Get the source message that created a fact."""
        from pairtner.storage.facts import get_fact_by_id, get_memory_versions

        row = await get_fact_by_id(self.db_path, fact_id)
        if not row:
            return None

        fact = _row_to_fact(row)
        versions = await get_memory_versions(self.db_path, fact_id)

        source_msg = None
        source_ts = None
        msg_id = row.get("source_message_id")
        if msg_id:
            from pairtner.storage.helpers import _fetch_one

            msg_row = await _fetch_one(
                self.db_path,
                "SELECT content, created_at FROM messages WHERE id = ?",
                (msg_id,),
            )
            if msg_row:
                source_msg = msg_row["content"]
                source_ts = msg_row["created_at"]

        return Provenance(
            fact=fact,
            source_message=source_msg,
            source_timestamp=source_ts,
            version_count=len(versions),
        )

    async def get_versions(self, fact_id: int) -> list[dict[str, Any]]:
        """Get version history for a fact."""
        from pairtner.storage.facts import get_memory_versions

        return await get_memory_versions(self.db_path, fact_id)

    async def get_stats(self, user_id: int) -> MemoryStats:
        """Get memory statistics for a user."""
        from pairtner.storage.facts import get_active_facts

        rows = await get_active_facts(self.db_path, user_id)
        if not rows:
            return MemoryStats(
                total=0,
                by_kind={},
                by_status={},
                avg_score=0.0,
                oldest_uncited_days=0,
            )

        by_kind: dict[str, int] = {}
        by_status: dict[str, int] = {}
        scores: list[float] = []
        oldest_uncited = 0

        for r in rows:
            kind = r.get("kind") or "working"
            status = r.get("status") or "active"
            by_kind[kind] = by_kind.get(kind, 0) + 1
            by_status[status] = by_status.get(status, 0) + 1
            scores.append(compute_relevance_score(r))

            # Track oldest uncited active fact
            if status == "active" and not r.get("last_cited_at"):
                age = _age_in_days(r.get("created_at") or "")
                if age > oldest_uncited:
                    oldest_uncited = int(age)

        return MemoryStats(
            total=len(rows),
            by_kind=by_kind,
            by_status=by_status,
            avg_score=sum(scores) / len(scores) if scores else 0.0,
            oldest_uncited_days=oldest_uncited,
        )

    async def get_recency_report(self, user_id: int) -> RecencyReport:
        """Memory health breakdown for PWA."""
        from pairtner.storage.facts import get_active_facts

        rows = await get_active_facts(self.db_path, user_id)
        healthy = aging = stale = contradictions = 0

        for r in rows:
            if r.get("contradicts_id"):
                contradictions += 1

            last_cited = r.get("last_cited_at")
            if last_cited:
                days = _age_in_days(last_cited)
            else:
                days = _age_in_days(r.get("created_at") or "")

            if days <= 7:
                healthy += 1
            elif days <= 30:
                aging += 1
            else:
                stale += 1

        return RecencyReport(
            healthy=healthy,
            aging=aging,
            stale=stale,
            contradictions=contradictions,
        )

    # --- Write ---

    async def add_fact(
        self,
        user_id: int,
        text: str,
        *,
        kind: str = "working",
        category: str | None = None,
        fact_type: str = "stable",
        source_message_id: int | None = None,
        source_ids: str | None = None,
        intensity: float = 0.5,
        model: str | None = None,
        metadata: str | None = None,
    ) -> Fact:
        """Add a new fact and return it with computed score."""
        from pairtner.storage import facts as facts_mod

        fact_id = await facts_mod.add_fact(
            self.db_path,
            user_id,
            text,
            "private",
            category=category,
            fact_type=fact_type,
            source_message_id=source_message_id,
            source_ids=source_ids,
            intensity=intensity,
            model=model,
            metadata=metadata,
        )

        await facts_mod.append_memory_version(
            self.db_path,
            fact_id,
            text,
            "active",
            "add",
            source_msg_id=source_message_id,
        )

        row = await facts_mod.get_fact_by_id(self.db_path, fact_id)
        return _row_to_fact(row)  # type: ignore[arg-type]

    async def update_text(
        self, fact_id: int, new_text: str, *, reason: str | None = None
    ) -> Fact | None:
        """Update a fact's text. Returns updated Fact or None."""
        from pairtner.storage import facts as facts_mod

        updated = await facts_mod.update_fact_text(
            self.db_path, fact_id, new_text
        )
        if not updated:
            return None

        await facts_mod.append_memory_version(
            self.db_path, fact_id, new_text, "active", "revise", reason=reason
        )

        row = await facts_mod.get_fact_by_id(self.db_path, fact_id)
        return _row_to_fact(row) if row else None

    async def set_status(
        self, fact_id: int, status: str, *, reason: str | None = None
    ) -> Fact | None:
        """Change a fact's status. Returns updated Fact or None."""
        from pairtner.storage import facts as facts_mod

        updated = await facts_mod.set_fact_status(self.db_path, fact_id, status)
        if not updated:
            return None

        row = await facts_mod.get_fact_by_id(self.db_path, fact_id)
        if row:
            await facts_mod.append_memory_version(
                self.db_path,
                fact_id,
                row["text"],
                status,
                "status",
                reason=reason,
            )
        return _row_to_fact(row) if row else None

    async def promote_to_core(self, fact_id: int) -> Fact | None:
        """Promote a fact to core kind."""
        from pairtner.storage import facts as facts_mod

        promoted = await facts_mod.promote_to_core(self.db_path, fact_id)
        if not promoted:
            return None

        row = await facts_mod.get_fact_by_id(self.db_path, fact_id)
        if row:
            await facts_mod.append_memory_version(
                self.db_path,
                fact_id,
                row["text"],
                "active",
                "promote_core",
            )
        return _row_to_fact(row) if row else None

    async def archive(
        self, fact_id: int, *, reason: str | None = None
    ) -> Fact | None:
        """Archive a fact (soft-delete)."""
        from pairtner.storage import facts as facts_mod

        await facts_mod.archive_fact(self.db_path, fact_id)
        row = await facts_mod.get_fact_by_id(self.db_path, fact_id)
        if row:
            await facts_mod.append_memory_version(
                self.db_path,
                fact_id,
                row["text"],
                "archived",
                "archive",
                reason=reason,
            )
        return _row_to_fact(row) if row else None

    async def delete(self, fact_id: int, user_id: int) -> bool:
        """Hard delete -- user-initiated only."""
        from pairtner.storage.facts import delete_fact

        return await delete_fact(self.db_path, fact_id, user_id)

    # --- Scoring & Context ---

    async def get_scored_context(
        self, user_id: int, budget_words: int = 1200,
        current_message: str | None = None,
    ) -> ContextResult:
        """Build score-ranked memory context for the LLM prompt.

        If current_message is provided, also includes FTS5 matches
        for relevant facts that might not rank high by score alone.
        """
        from pairtner.storage.facts import get_active_facts

        rows = await get_active_facts(self.db_path, user_id)
        if not rows:
            return ContextResult(
                lines=[], facts_included=0, total_words=0, cited_ids=[]
            )

        # Score all facts
        scored: list[tuple[dict[str, Any], float]] = []
        core_rows: list[dict[str, Any]] = []
        for r in rows:
            kind = r.get("kind") or "working"
            if kind == "core":
                core_rows.append(r)
            else:
                scored.append((r, compute_relevance_score(r)))

        # Sort non-core by score descending
        scored.sort(key=lambda x: x[1], reverse=True)

        # FTS5 search for current message (finds relevant facts regardless of score)
        fts_ids: set[int] = set()
        if current_message:
            try:
                from pairtner.core.search import search_facts
                fts_results = await search_facts(
                    self.db_path, user_id, current_message, limit=5
                )
                fts_ids = {r["id"] for r in fts_results}
            except Exception:
                pass  # FTS5 not available, skip

        lines: list[str] = []
        cited_ids: list[int] = []
        word_count = 0

        # Core facts first (always included)
        for r in core_rows:
            line = f"[{r['id']}] (core) {r['text']}"
            words = len(line.split())
            lines.append(line)
            word_count += words
            cited_ids.append(r["id"])

        # Then top-scored non-core
        included_ids = set(cited_ids)
        for r, score in scored:
            if word_count >= budget_words:
                break
            kind = r.get("kind") or "working"
            status = r.get("status") or "active"
            prefix = f"[{r['id']}]"
            if kind == "episode":
                prefix += " (episode)"
            suffix = f" [{status}]" if status != "active" else ""
            line = f"{prefix} {r['text']}{suffix}"
            words = len(line.split())
            if word_count + words > budget_words and word_count > 0:
                break
            lines.append(line)
            word_count += words
            cited_ids.append(r["id"])
            included_ids.add(r["id"])

        # FTS5 matches not already included (relevant to current message)
        if fts_ids:
            fts_rows = {r["id"]: r for r in rows}
            for fid in fts_ids - included_ids:
                if fid not in fts_rows:
                    continue
                r = fts_rows[fid]
                if r.get("archived_at"):
                    continue
                line = f"[{r['id']}] (relevant) {r['text']}"
                words = len(line.split())
                if word_count + words > budget_words + 200:  # slight overflow OK for FTS
                    break
                lines.append(line)
                word_count += words
                cited_ids.append(r["id"])

        # Record citations
        await self._record_citations(cited_ids)

        return ContextResult(
            lines=lines,
            facts_included=len(cited_ids),
            total_words=word_count,
            cited_ids=cited_ids,
        )

    async def _record_citations(self, fact_ids: list[int]) -> None:
        """Increment citation_count and set last_cited_at for included facts."""
        if not fact_ids:
            return
        now = _now_iso()
        import aiosqlite

        async with aiosqlite.connect(self.db_path) as db:
            for fid in fact_ids:
                await db.execute(
                    "UPDATE facts SET citation_count = COALESCE(citation_count, 0) + 1, "
                    "last_cited_at = ? WHERE id = ?",
                    (now, fid),
                )
            await db.commit()

    # --- Maintenance ---

    async def run_aging(self) -> AgingResult:
        """Run fact aging: archive expired, mark stale as outdated."""
        from pairtner.storage.facts import run_fact_aging

        needs_confirm = await run_fact_aging(self.db_path)

        # Auto-mark outdated: active working facts uncited for 30+ days
        import aiosqlite

        now = datetime.now(timezone.utc)
        cutoff = _to_iso(now - __import__("datetime").timedelta(days=30))
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "UPDATE facts SET status = 'outdated', updated_at = ? "
                "WHERE kind = 'working' AND status = 'active' "
                "AND archived_at IS NULL "
                "AND (last_cited_at IS NULL OR last_cited_at < ?) "
                "AND created_at < ?",
                (_now_iso(), cutoff, cutoff),
            )
            marked = cur.rowcount
            await db.commit()

        return AgingResult(
            archived_transient=0,  # run_fact_aging handles this internally
            marked_outdated=marked,
            needs_confirmation=needs_confirm,
        )

    # --- LLM integration ---

    async def apply_llm_ops(
        self,
        user_id: int,
        raw_ops: Any,
        user_message: str,
        *,
        message_id: int | None = None,
    ) -> Any:
        """Apply LLM-proposed memory ops. Delegates to core.memory engine.

        This is the bridge between the LLM contract and the Memory API.
        The core.memory module handles validation, dedup, and application.
        Future: route through this API's write methods for citation tracking.
        """
        from pairtner.core.memory import parse_memory_ops, apply_memory_ops
        from pairtner.storage.facts import get_facts

        existing = await get_facts(self.db_path, user_id)
        user_fact_ids = {f["id"] for f in existing}

        ops = parse_memory_ops(raw_ops, user_fact_ids)
        if not ops:
            from pairtner.core.memory import OpsResult

            return OpsResult()

        result = await apply_memory_ops(
            self.db_path,
            user_id,
            ops,
            user_message,
            message_id=message_id,
            existing_facts=existing,
        )

        # Set source_message_id on newly created facts
        if message_id:
            for outcome in result.outcomes:
                if outcome.outcome == "applied" and outcome.memory_id:
                    import aiosqlite

                    async with aiosqlite.connect(self.db_path) as db:
                        await db.execute(
                            "UPDATE facts SET source_message_id = ? "
                            "WHERE id = ? AND source_message_id IS NULL",
                            (message_id, outcome.memory_id),
                        )
                        await db.commit()

        return result
