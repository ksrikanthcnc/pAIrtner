"""Helpers for the pack evaluation runner (case loading, seeding, results output).

No verdicts are produced anywhere: the runner records what the model did and a
person (or a later judge) reads ``summary.md`` against each case's must/must_not.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sqlite3
from typing import Any

import yaml

from pairtner import db as db_mod

_ID_PREFIX = re.compile(r"^\[\d+\]\s*")
_CODE = re.compile(r"\b[0-9A-F]{6}\b")
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
MEMORY_TABLES = {
    "facts": ("facts", "owner_id"),
    "partner_perceptions": ("partner_perceptions", "user_id"),
    "user_patterns": ("user_patterns", "user_id"),
}
UNSUPPORTED_CONTEXT = "not yet supported (the engine has no profile-gap block yet)"
UNSUPPORTED_IDS = "ids are not supported yet; the leading [id] was stripped"


# ── cases ──────────────────────────────────────────────────────────────

def eval_path(pack_root: pathlib.Path) -> pathlib.Path:
    """Path of the pack's cases file (``eval:`` in pack.yaml)."""
    manifest = yaml.safe_load((pack_root / "pack.yaml").read_text(encoding="utf-8")) or {}
    rel = manifest.get("eval")
    if not rel:
        raise ValueError(f"pack {pack_root} has no 'eval:' entry in pack.yaml")
    return (pack_root / rel).resolve()


def load_cases(path: pathlib.Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"{path}: expected a non-empty 'cases:' list")
    seen: set[str] = set()
    for c in cases:
        if not isinstance(c, dict) or "id" not in c:
            raise ValueError(f"{path}: every case needs an 'id'")
        if c["id"] in seen:
            raise ValueError(f"{path}: duplicate case id {c['id']!r}")
        seen.add(c["id"])
    return cases


def select_cases(
    cases: list[dict[str, Any]], only: str | None, area: str | None,
) -> list[dict[str, Any]]:
    out = cases
    if only:
        wanted = [x.strip() for x in only.split(",") if x.strip()]
        unknown = sorted(set(wanted) - {c["id"] for c in cases})
        if unknown:
            raise ValueError(f"unknown case id(s): {', '.join(unknown)}")
        out = [c for c in out if c["id"] in wanted]
    if area:
        out = [c for c in out if c.get("area") == area]
    return out


def message_text(case: dict[str, Any]) -> str:
    """The text user A sends for this case."""
    msg = (case.get("message") or "").strip()
    tag = (case.get("tag") or "").strip()
    if not msg and case.get("area") == "mediate":
        return "/mediate"
    if not msg:
        raise ValueError(f"case {case['id']}: no message")
    return f"{tag} {msg}" if tag else msg


def safe_name(text: str) -> str:
    return _SAFE.sub("-", text).strip("-") or "x"


# ── seeding ────────────────────────────────────────────────────────────

async def seed_context(
    db_path: str, case: dict[str, Any], a_id: int, b_id: int,
) -> list[str]:
    """Write the case's context straight into the DB; return notes for the report."""
    ctx = case.get("context") or {}
    notes: list[str] = []
    for key in ("core", "working"):
        ftype = "stable" if key == "core" else "situational"
        for item in ctx.get(key) or []:
            text = _ID_PREFIX.sub("", str(item))
            if text != str(item):
                notes.append(f"{key}: {UNSUPPORTED_IDS}")
            await db_mod.add_fact(db_path, a_id, text, "private", "eval", None, ftype)
    for item in ctx.get("shared") or []:
        await db_mod.add_fact(db_path, a_id, str(item), "shared", "eval", None, "stable")
    for item in ctx.get("partner_view") or []:
        await db_mod.add_partner_perception(db_path, b_id, a_id, str(item), "neutral", 0.8)
    for item in ctx.get("a_view") or []:
        await db_mod.add_partner_perception(db_path, a_id, b_id, str(item), "neutral", 0.8)
    for item in ctx.get("b_view") or []:
        await db_mod.add_partner_perception(db_path, b_id, a_id, str(item), "neutral", 0.8)
    voice = ctx.get("voice")
    if voice:
        await db_mod.update_learned_voice(db_path, a_id, json.dumps(voice))
        for _ in range(10):
            await db_mod.increment_message_count(db_path, a_id)
    if ctx.get("unknown"):
        notes.append(f"unknown: {UNSUPPORTED_CONTEXT}")
    return sorted(set(notes))


def memory_snapshot(db_path: str, user_id: int) -> dict[str, dict[int, dict[str, Any]]]:
    """Rows of the memory tables that belong to *user_id* (read-only)."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        snap: dict[str, dict[int, dict[str, Any]]] = {}
        for name, (table, col) in MEMORY_TABLES.items():
            rows = con.execute(f"SELECT * FROM {table} WHERE {col} = ?", (user_id,)).fetchall()  # noqa: S608 (constants)
            snap[name] = {r["id"]: _clean_row(dict(r)) for r in rows}
        return snap
    finally:
        con.close()


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    """Drop ids of users and timestamps so runs diff cleanly."""
    return {
        k: v for k, v in row.items()
        if not k.endswith("_at") and k not in ("owner_id", "user_id", "about_id")
    }


def new_rows(
    before: dict[str, dict[int, dict[str, Any]]],
    after: dict[str, dict[int, dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    return {
        name: [row for rid, row in after[name].items() if rid not in before[name]]
        for name in MEMORY_TABLES
    }


def memory_expectation(case: dict[str, Any]) -> dict[str, Any]:
    expected = (case.get("expect") or {}).get("memory")
    if expected is None:
        return {"status": "none_specified", "expected": []}
    if not expected:
        return {"status": "expects_no_change", "expected": []}
    return {"status": "not_yet_supported", "expected": list(expected)}


def find_pairing_code(replies: list[dict[str, Any]]) -> str | None:
    for m in replies:
        found = _CODE.findall(m.get("text", "") or "")
        if found:
            return found[0]
    return None


# ── output ─────────────────────────────────────────────────────────────

def scrub(text: str) -> str:
    """Remove the Gemini key value if it ever slipped into a string."""
    key = os.environ.get("GEMINI_API_KEY", "")
    return text.replace(key, "[redacted]") if len(key) >= 8 else text


def dump_json(path: pathlib.Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(scrub(json.dumps(obj, indent=2, ensure_ascii=False, default=str)) + "\n", encoding="utf-8")


def _quote(text: str) -> list[str]:
    return [f"> {line}" if line else ">" for line in (text or "").splitlines()] or [">"]


def case_section(res: dict[str, Any]) -> list[str]:
    out = [f"## {res['id']}", ""]
    out.append(
        f"- area: {res.get('area')} | tag: {res.get('tag') or '-'} | "
        f"status: {res['status']} | LLM calls: {len(res.get('llm_calls') or [])}"
    )
    if res["status"] == "skipped":
        out += [f"- skipped: {res.get('skip_reason', '')}", ""]
        out += ["**Message**", *_quote(res.get("message") or "(none)"), ""]
        return out
    if not res.get("llm_calls"):
        out.append("- no LLM call was needed (the engine answered by itself)")
    out += ["", "**Message**", *_quote(res.get("message") or f"({res.get('sent_text')})"), ""]
    out.append("**Replies**")
    for i, r in enumerate(res.get("replies") or [], 1):
        out += [f"{i}.", *_quote(r.get("text") or "")]
        labels = [b["text"] for row in r.get("buttons") or [] for b in row]
        if labels:
            out.append(f"   buttons: {' | '.join(labels)}")
    if not res.get("replies"):
        out.append("(none)")
    out += ["", "**Relay draft**", *_quote(res.get("relay_draft") or "(none)"), ""]
    out.append("**Memory rows written**")
    wrote = False
    for table, rows in (res.get("memory_rows") or {}).items():
        for row in rows:
            wrote = True
            text = row.get("text") or row.get("theme") or row.get("pattern") or ""
            extra = row.get("visibility") or row.get("sentiment") or row.get("pattern_type") or ""
            out.append(f"- {table}: {text} ({extra})")
    if not wrote:
        out.append("- (none)")
    me = res.get("memory_expectation") or {}
    out += ["", f"**Memory expectation**: {me.get('status')}"]
    out += [f"- expected: {e}" for e in me.get("expected") or []]
    for note in res.get("seeding_notes") or []:
        out.append(f"- seeding note: {note}")
    out += ["", "**Must**"] + [f"- [ ] {m}" for m in res.get("must") or []]
    out += ["", "**Must not**"] + [f"- [ ] {m}" for m in res.get("must_not") or []]
    out.append("")
    return out


def summary_markdown(meta: dict[str, Any], results: list[dict[str, Any]]) -> str:
    ran = sum(1 for r in results if r["status"] == "run")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    calls = sum(len(r.get("llm_calls") or []) for r in results)
    lines = [
        f"# Pack evaluation: {meta['pack_name']} {meta['pack_version']}",
        "",
        f"- pack hash: {meta['pack_hash']}",
        f"- llm: {meta['llm']} | model: {meta['model']}",
        f"- date: {meta['date']}",
        f"- cases run: {ran} | skipped: {skipped} | LLM calls: {calls}",
    ]
    if meta.get("stop_note"):
        lines.append(f"- STOPPED EARLY: {meta['stop_note']}")
    lines += ["", "No verdicts: read each reply against its must / must-not list.", ""]
    for res in results:
        lines += case_section(res)
    return scrub("\n".join(lines).rstrip() + "\n")
