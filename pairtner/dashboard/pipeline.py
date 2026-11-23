"""Pipeline debug view: message -> extracted facts -> versions -> summaries.

Read-only; joins existing columns only (``facts.source_message_id``,
``facts.source_ids``, ``facts.metadata``, ``memory_versions``). The dashboard
CSP forbids inline scripts, so expansion uses native ``<details>`` elements.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from pairtner.dashboard.render import esc, link
from pairtner.dashboard.store import NotFound
from pairtner.storage.tagfmt import parse_fact_tags

DEFAULT_LIMIT = 25
MAX_LIMIT = 100
HIDDEN = "[hidden]"
# Facts that are summaries of other facts (source_ids points at their inputs).
_SUMMARY_KINDS = (
    "daily", "triday", "weekly", "biweekly", "monthly", "bimonthly",
    "quarterly", "semiannual", "yearly", "biennium", "quinquennium", "decade",
)


def parse_limit(raw: str | None) -> int:
    try:
        n = int(raw) if raw is not None else DEFAULT_LIMIT
    except ValueError:
        return DEFAULT_LIMIT
    return max(1, min(n, MAX_LIMIT))


def _parse_json(raw: Any) -> Any:
    if raw is None or raw == "":
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def parse_source_ids(raw: Any) -> list[int]:
    """``facts.source_ids`` (JSON array) -> list of ints; bad data -> []."""
    data = _parse_json(raw)
    if not isinstance(data, list):
        return []
    out: list[int] = []
    for v in data:
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            out.append(v)
        elif isinstance(v, str) and v.strip().lstrip("-").isdigit():
            out.append(int(v))
    return out


def _txt(value: Any, show_raw: bool) -> str | None:
    if value is None:
        return None
    return str(value) if show_raw else HIDDEN


def _fact_row(r: sqlite3.Row, show_raw: bool) -> dict[str, Any]:
    meta = _parse_json(r["metadata"])
    if not show_raw and isinstance(meta, dict):
        # Keep the key names (debugging value) but drop the values.
        meta = {k: HIDDEN for k in meta}
    elif not show_raw:
        meta = None if meta is None else HIDDEN
    return {
        "id": r["id"], "owner_id": r["owner_id"], "text": _txt(r["text"], show_raw),
        "kind": r["kind"], "status": r["status"], "visibility": r["visibility"],
        "tags": parse_fact_tags(r["tag"]), "model": r["model"],
        "source_message_id": r["source_message_id"],
        "source_ids": parse_source_ids(r["source_ids"]),
        "metadata": meta, "created_at": r["created_at"],
        "supersedes_id": r["supersedes_id"],
    }


_FACT_COLS = (
    "id, owner_id, text, kind, status, visibility, tag, model, source_message_id, "
    "source_ids, metadata, created_at, supersedes_id"
)


def _versions(conn: sqlite3.Connection, fact_ids: list[int], show_raw: bool
              ) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = {i: [] for i in fact_ids}
    if not fact_ids:
        return out
    marks = ",".join("?" * len(fact_ids))
    for r in conn.execute(
        "SELECT fact_id, version, text, status, op, reason, source_msg_id, created_at "
        f"FROM memory_versions WHERE fact_id IN ({marks}) ORDER BY fact_id, version",
        fact_ids,
    ):
        out[r["fact_id"]].append({
            "version": r["version"], "op": r["op"], "status": r["status"],
            "reason": _txt(r["reason"], show_raw), "text": _txt(r["text"], show_raw),
            "source_msg_id": r["source_msg_id"], "created_at": r["created_at"],
        })
    return out


def _consumers(conn: sqlite3.Connection, fact_ids: list[int], show_raw: bool
               ) -> dict[int, list[dict[str, Any]]]:
    """Summaries whose ``source_ids`` reference each fact id (matched exactly)."""
    out: dict[int, list[dict[str, Any]]] = {i: [] for i in fact_ids}
    if not fact_ids:
        return out
    wanted = set(fact_ids)
    marks = ",".join("?" * len(_SUMMARY_KINDS))
    for r in conn.execute(
        "SELECT id, kind, text, owner_id, relationship_id, source_ids, created_at "
        f"FROM facts WHERE source_ids IS NOT NULL AND kind IN ({marks}) "
        "ORDER BY id",
        _SUMMARY_KINDS,
    ):
        hits = wanted.intersection(parse_source_ids(r["source_ids"]))
        for fid in hits:
            out[fid].append({
                "id": r["id"], "kind": r["kind"], "owner_id": r["owner_id"],
                "relationship_id": r["relationship_id"], "created_at": r["created_at"],
                "text": _txt(r["text"], show_raw),
                "source_count": len(parse_source_ids(r["source_ids"])),
            })
    return out


def _attach(conn: sqlite3.Connection, facts: list[dict[str, Any]], show_raw: bool) -> None:
    ids = [f["id"] for f in facts]
    versions = _versions(conn, ids, show_raw)
    consumers = _consumers(conn, ids, show_raw)
    for f in facts:
        f["versions"] = versions[f["id"]]
        f["summaries"] = consumers[f["id"]]


def build_pipeline(
    conn: sqlite3.Connection, *, limit: int = DEFAULT_LIMIT,
    before_id: int | None = None, user_id: int | None = None, show_raw: bool = True,
) -> dict[str, Any]:
    """Newest ``limit`` messages (id < before_id) with their extraction traces."""
    where, params = ["deleted_at IS NULL"], []  # type: list[str], list[Any]
    if before_id is not None:
        where.append("id < ?")
        params.append(before_id)
    if user_id is not None:
        where.append("user_id = ?")
        params.append(user_id)
    try:
        msgs = conn.execute(
            "SELECT id, user_id, role, content, created_at FROM messages "
            f"WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?",
            (*params, limit + 1),
        ).fetchall()
        has_more = len(msgs) > limit
        msgs = msgs[:limit]
        ids = [m["id"] for m in msgs]
        by_msg: dict[int, list[dict[str, Any]]] = {i: [] for i in ids}
        if ids:
            marks = ",".join("?" * len(ids))
            for r in conn.execute(
                f"SELECT {_FACT_COLS} FROM facts WHERE source_message_id IN ({marks}) "
                "ORDER BY id", ids,
            ):
                by_msg[r["source_message_id"]].append(_fact_row(r, show_raw))
        all_facts = [f for fs in by_msg.values() for f in fs]
        _attach(conn, all_facts, show_raw)
        # Version entries that name the message but whose fact has another
        # source_message_id (e.g. an update triggered by a later message).
        touched: dict[int, list[dict[str, Any]]] = {i: [] for i in ids}
        if ids:
            for r in conn.execute(
                "SELECT fact_id, version, op, reason, source_msg_id FROM memory_versions "
                f"WHERE source_msg_id IN ({marks}) ORDER BY id", ids,
            ):
                touched[r["source_msg_id"]].append({
                    "fact_id": r["fact_id"], "version": r["version"], "op": r["op"],
                    "reason": _txt(r["reason"], show_raw),
                })
    except sqlite3.OperationalError as exc:
        if "no such" in str(exc):
            raise NotFound("pipeline view needs the v2 schema") from exc
        raise
    return {
        "messages": [{
            "id": m["id"], "user_id": m["user_id"], "role": m["role"],
            "content": _txt(m["content"], show_raw), "created_at": m["created_at"],
            "facts": by_msg[m["id"]], "version_ops": touched[m["id"]],
        } for m in msgs],
        "has_more": has_more,
        "next_before": msgs[-1]["id"] if msgs else None,
    }


def build_fact_trace(conn: sqlite3.Connection, fact_id: int, show_raw: bool = True
                     ) -> dict[str, Any]:
    """One fact with origin message, version history, source facts, consumers."""
    try:
        r = conn.execute(f"SELECT {_FACT_COLS} FROM facts WHERE id = ?", (fact_id,)).fetchone()
        if r is None:
            raise NotFound(f"no such fact: {fact_id}")
        fact = _fact_row(r, show_raw)
        _attach(conn, [fact], show_raw)
        origin = None
        if fact["source_message_id"] is not None:
            m = conn.execute(
                "SELECT id, user_id, role, content, created_at FROM messages WHERE id = ?",
                (fact["source_message_id"],),
            ).fetchone()
            if m:
                origin = {"id": m["id"], "user_id": m["user_id"], "role": m["role"],
                          "content": _txt(m["content"], show_raw),
                          "created_at": m["created_at"]}
        sources: list[dict[str, Any]] = []
        if fact["source_ids"]:
            marks = ",".join("?" * len(fact["source_ids"]))
            sources = [
                _fact_row(s, show_raw) for s in conn.execute(
                    f"SELECT {_FACT_COLS} FROM facts WHERE id IN ({marks}) ORDER BY id",
                    fact["source_ids"],
                )
            ]
    except sqlite3.OperationalError as exc:
        if "no such" in str(exc):
            raise NotFound("pipeline view needs the v2 schema") from exc
        raise
    return {"fact": fact, "origin": origin, "sources": sources}


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

CSS = """<style>
.pl details{background:var(--surface);border:1px solid var(--border);border-radius:8px;
margin:6px 0;padding:6px 10px}
.pl details details{margin:6px 0 6px 12px;background:transparent}
.pl summary{cursor:pointer}
.pl .meta{color:var(--muted);font-size:12px;margin-left:6px}
.pl .tag{display:inline-block;font-size:11px;padding:0 7px;border-radius:999px;
border:1px solid var(--border);margin-right:4px}
.pl pre{font-size:12px;margin:4px 0}
.pl ul{margin:4px 0;padding-left:18px}
</style>"""


def _json_block(label: str, data: Any) -> str:
    if data in (None, {}, []):
        return ""
    return (f"<details><summary>{esc(label)}</summary><pre>"
            f"{esc(json.dumps(data, indent=2, sort_keys=True, default=str))}</pre></details>")


def _fact_html(f: dict[str, Any]) -> str:
    tags = "".join(f'<span class="tag">{esc(t)}</span>' for t in f["tags"])
    head = (f'#{esc(f["id"])} <span class="tag">{esc(f["kind"])}</span>'
            f'<span class="tag">{esc(f["visibility"])}</span>{tags}'
            f' {esc(f["text"])}')
    parts = [f'<p class="meta">status {esc(f["status"])} · owner {esc(f["owner_id"])} · '
             f'model {esc(f["model"] or "n/a")} · {esc(f["created_at"])} · '
             f'{link(f"/pipeline/fact/{f["id"]}", "open trace")}</p>']
    if f.get("versions"):
        items = "".join(
            f'<li>v{esc(v["version"])} {esc(v["op"])} ({esc(v["status"])})'
            f'{" · " + esc(v["reason"]) if v["reason"] else ""} · '
            f'src msg {esc(v["source_msg_id"])} · {esc(v["created_at"])}</li>'
            for v in f["versions"])
        parts.append(f"<details><summary>Version history ({len(f['versions'])})"
                     f"</summary><ul>{items}</ul></details>")
    summ = f.get("summaries") or []
    if summ:
        items = "".join(
            f'<li>{link(f"/pipeline/fact/{s["id"]}", f"#{s["id"]}")} '
            f'{esc(s["kind"])} (from {esc(s["source_count"])} facts) '
            f'{esc(s["created_at"])}</li>' for s in summ)
        parts.append(f"<details><summary>Consumed by {len(summ)} summar"
                     f"{'y' if len(summ) == 1 else 'ies'}</summary><ul>{items}</ul></details>")
    else:
        parts.append('<p class="meta">not referenced by any summary</p>')
    parts.append(_json_block("metadata", f["metadata"]))
    if f["source_ids"]:
        ids = ", ".join(str(i) for i in f["source_ids"])
        parts.append(f'<p class="meta">source fact ids: {esc(ids)}</p>')
    return f"<details><summary>{head}</summary>{''.join(parts)}</details>"


def render_pipeline(data: dict[str, Any], *, user_id: int | None = None,
                    limit: int = DEFAULT_LIMIT) -> str:
    out = [CSS, '<div class="pl">']
    out.append('<form method="get" action="/pipeline"><input type="text" name="user_id" '
               f'placeholder="user id (optional)" value="{esc(user_id or "")}"> '
               f'<input type="hidden" name="limit" value="{esc(limit)}"> '
               "<button>Filter</button></form>")
    if not data["messages"]:
        out.append('<p class="muted">No messages.</p>')
    for m in data["messages"]:
        n = len(m["facts"])
        badge = (f'<span class="tag">{n} fact{"s" if n != 1 else ""}</span>' if n
                 else '<span class="tag">no extraction</span>')
        body = []
        if m["facts"]:
            body.append("".join(_fact_html(f) for f in m["facts"]))
        else:
            body.append('<p class="meta">No facts reference this message as source.</p>')
        if m["version_ops"]:
            items = "".join(
                f'<li>fact #{esc(v["fact_id"])} v{esc(v["version"])} {esc(v["op"])}'
                f'{" · " + esc(v["reason"]) if v["reason"] else ""}</li>'
                for v in m["version_ops"])
            body.append(f"<details><summary>Memory changes ({len(m['version_ops'])})"
                        f"</summary><ul>{items}</ul></details>")
        text = m["content"] or ""
        short = text if len(text) <= 120 else text[:120] + "…"
        out.append(
            f'<details><summary>#{esc(m["id"])} <b>{esc(m["role"])}</b> '
            f'u{esc(m["user_id"])} {esc(short)} {badge}'
            f'<span class="meta">{esc(m["created_at"])}</span></summary>'
            f'<pre>{esc(text)}</pre>{"".join(body)}</details>')
    out.append("</div>")
    if data["has_more"] and data["next_before"] is not None:
        q = f"/pipeline?before={data['next_before']}&limit={limit}"
        if user_id is not None:
            q += f"&user_id={user_id}"
        out.append(f"<p>{link(q, 'Older ›')}</p>")
    return "".join(out)


def render_fact_trace(t: dict[str, Any]) -> str:
    f = t["fact"]
    out = [CSS, '<div class="pl">']
    o = t["origin"]
    if o:
        out.append(f'<h2>Origin message #{esc(o["id"])}</h2><pre>{esc(o["content"])}</pre>'
                   f'<p class="meta">{esc(o["role"])} · user {esc(o["user_id"])} · '
                   f'{esc(o["created_at"])}</p>')
    else:
        out.append('<h2>Origin message</h2><p class="muted">none recorded '
                   "(not extracted from a single message)</p>")
    out.append("<h2>Fact</h2>" + _fact_html(f).replace("<details>", "<details open>", 1))
    if t["sources"]:
        out.append("<h2>Source facts</h2>" + "".join(_fact_html(s) for s in t["sources"]))
    out.append("</div>")
    return "".join(out)
