"""Search v2: relationship-scoped pagination and two-pass (FTS5 -> LLM) retrieval.

Pass 1 is a fast FTS5/BM25 query with relevance scoring (no LLM).
Pass 2 asks the LLM to pick the facts most relevant to the query from the
pass-1 candidates. Any LLM failure falls back to the pass-1 order.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import aiosqlite

from pairtner.core.memory_api import compute_relevance_score
from pairtner.llm import LLMClient

logger = logging.getLogger(__name__)

DEFAULT_CANDIDATES = 50
DEFAULT_TOP_K = 15
MAX_PER_PAGE = 100
_VALID_KINDS = ("core", "working")
# Visibilities a partner's fact may have for the other partner to see it.
_PARTNER_VISIBLE = ("nudge_eligible", "shareable", "shared")

RERANK_SYSTEM_PROMPT = (
    "You are a relevance ranker. Given a query and a list of facts, return the IDs "
    "of the most relevant facts as a JSON array, ordered by relevance. "
    "Return ONLY the JSON array of IDs."
)

_JSON_ARRAY_RE = re.compile(r"\[[^\[\]]*\]")


def _fts_match(query: str) -> str:
    """Build a safe FTS5 MATCH expression (quoted terms joined by OR)."""
    terms = re.sub(r"[^\w\s]", " ", query, flags=re.UNICODE).split()
    return " OR ".join(f'"{t}"' for t in terms[:16])


async def _scope(
    db: aiosqlite.Connection, user_id: int, relationship_id: str | None,
    own_only: bool,
) -> tuple[str, list[Any]]:
    """Return (SQL condition, params) limiting facts to what *user_id* may see."""
    partner_id: int | None = None
    if not own_only:
        if relationship_id:
            async with db.execute(
                "SELECT user_a_id, user_b_id FROM relationships WHERE id = ?",
                (relationship_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is None or user_id not in (row[0], row[1]):
                raise PermissionError(
                    f"user {user_id} is not a member of relationship {relationship_id}"
                )
            partner_id = row[1] if user_id == row[0] else row[0]
        else:
            async with db.execute(
                "SELECT partner_id FROM users WHERE tg_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
            partner_id = row[0] if row else None

    where = ["f.archived_at IS NULL", "f.status != 'outdated'"]
    params: list[Any] = []
    if relationship_id:
        where.append("f.relationship_id = ?")
        params.append(relationship_id)
    if partner_id:
        marks = ",".join("?" * len(_PARTNER_VISIBLE))
        where.append(
            f"(f.owner_id = ? OR (f.owner_id = ? AND f.visibility IN ({marks})))"
        )
        params += [user_id, partner_id, *_PARTNER_VISIBLE]
    else:
        where.append("f.owner_id = ?")
        params.append(user_id)
    return " AND ".join(where), params


def _kind_clause(
    kind_filter: str | None, kinds: tuple[str, ...] | None,
) -> tuple[str, list[Any]]:
    if kind_filter is not None:
        if kind_filter not in _VALID_KINDS:
            raise ValueError(f"invalid kind filter: {kind_filter!r}")
        return " AND f.kind = ?", [kind_filter]
    if kinds:
        return f" AND f.kind IN ({','.join('?' * len(kinds))})", list(kinds)
    return "", []


def _to_result(row: aiosqlite.Row) -> dict[str, Any]:
    d = dict(row)
    d["score"] = round(compute_relevance_score(d), 4)
    return {
        "id": d["id"],
        "text": d["text"],
        "kind": d.get("kind") or "working",
        "visibility": d.get("visibility"),
        "owner_id": d.get("owner_id"),
        "category": d.get("category"),
        "created_at": d.get("created_at"),
        "score": d["score"],
    }


async def paginated_search(
    db_path: str, user_id: int, query: str,
    relationship_id: str | None = None,
    *, page: int = 1, per_page: int = 10,
    kind_filter: str | None = None,  # 'core', 'working', or None for all
    own_only: bool = False,
) -> dict:
    """Paginated FTS5 search (no LLM, fast).

    Returns {results, page, per_page, total, has_more}. Partner facts are
    included only when non-private. Raises ValueError for a bad kind filter and
    PermissionError when *user_id* is not in *relationship_id*.
    """
    page = max(1, int(page))
    per_page = max(1, min(int(per_page), MAX_PER_PAGE))
    empty = {"results": [], "page": page, "per_page": per_page, "total": 0, "has_more": False}
    match = _fts_match(query)
    if not match:
        if kind_filter is not None and kind_filter not in _VALID_KINDS:
            raise ValueError(f"invalid kind filter: {kind_filter!r}")
        return empty

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        scope_sql, scope_params = await _scope(db, user_id, relationship_id, own_only)
        kind_sql, kind_params = _kind_clause(kind_filter, None)
        base = (
            "FROM facts_fts JOIN facts f ON f.id = facts_fts.rowid "
            f"WHERE facts_fts MATCH ? AND {scope_sql}{kind_sql}"
        )
        params = [match, *scope_params, *kind_params]
        try:
            async with db.execute(f"SELECT COUNT(*) {base}", params) as cur:
                total = (await cur.fetchone())[0]
            async with db.execute(
                f"SELECT f.* {base} ORDER BY bm25(facts_fts), f.id LIMIT ? OFFSET ?",
                [*params, per_page, (page - 1) * per_page],
            ) as cur:
                rows = await cur.fetchall()
        except aiosqlite.OperationalError as exc:
            logger.warning("paginated_search FTS query failed: %s", exc)
            return empty

    return {
        "results": [_to_result(r) for r in rows],
        "page": page,
        "per_page": per_page,
        "total": total,
        "has_more": page * per_page < total,
    }


async def _fts_candidates(
    db_path: str, user_id: int, query: str, relationship_id: str | None,
    limit: int, own_only: bool, kinds: tuple[str, ...] | None,
) -> list[dict[str, Any]]:
    match = _fts_match(query)
    if not match:
        return []
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        scope_sql, scope_params = await _scope(db, user_id, relationship_id, own_only)
        kind_sql, kind_params = _kind_clause(None, kinds)
        try:
            async with db.execute(
                "SELECT f.* FROM facts_fts JOIN facts f ON f.id = facts_fts.rowid "
                f"WHERE facts_fts MATCH ? AND {scope_sql}{kind_sql} "
                "ORDER BY bm25(facts_fts), f.id LIMIT ?",
                [match, *scope_params, *kind_params, limit],
            ) as cur:
                rows = await cur.fetchall()
        except aiosqlite.OperationalError as exc:
            logger.warning("FTS candidate query failed: %s", exc)
            return []
    return [_to_result(r) for r in rows]


def _parse_id_list(raw: str) -> list[int]:
    """Extract a list of integer IDs from an LLM reply (JSON array, maybe fenced)."""
    for m in _JSON_ARRAY_RE.finditer(raw):
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if not isinstance(data, list):
            continue
        ids: list[int] = []
        for x in data:
            if isinstance(x, bool):
                continue
            if isinstance(x, int):
                ids.append(x)
            elif isinstance(x, str) and x.strip().isdigit():
                ids.append(int(x.strip()))
        return ids
    return []


async def two_pass_search(
    db_path: str, llm: LLMClient, user_id: int,
    query: str, relationship_id: str | None = None,
    *, top_k: int = DEFAULT_TOP_K, candidates: int = DEFAULT_CANDIDATES,
    own_only: bool = False, kinds: tuple[str, ...] | None = None,
) -> list[dict]:
    """Two-pass retrieval: FTS5 -> LLM rerank.

    Pass 1: FTS5 keyword search + relevance score -> top ``candidates``
    Pass 2: LLM picks the ``top_k`` most relevant to the query
    Returns facts sorted by relevance, each with
    {id, text, kind, score, relevance_reason, ...}. ``relevance_reason`` is
    "llm_rerank" or "fts_fallback".
    """
    cands = await _fts_candidates(
        db_path, user_id, query, relationship_id, candidates, own_only, kinds,
    )
    if not cands:
        return []

    def fallback() -> list[dict]:
        return [{**c, "relevance_reason": "fts_fallback"} for c in cands[:top_k]]

    if len(cands) <= 1:
        return fallback()

    listing = "\n".join(f"[{c['id']}] {c['text']}" for c in cands)
    try:
        resp = await llm.generate(f"Query: {query}\n\nFacts:\n{listing}", RERANK_SYSTEM_PROMPT)
        raw = resp.get("reply", "") if isinstance(resp, dict) else str(resp)
        ranked = _parse_id_list(raw or "")
    except Exception as exc:  # LLM/provider errors are heterogeneous; fall back to pass 1
        logger.warning("two_pass_search rerank failed, using FTS order: %s", exc)
        return fallback()

    by_id = {c["id"]: c for c in cands}
    out: list[dict] = []
    seen: set[int] = set()
    for fid in ranked:
        if fid in by_id and fid not in seen:
            seen.add(fid)
            out.append({**by_id[fid], "relevance_reason": "llm_rerank"})
        if len(out) >= top_k:
            break
    return out or fallback()
