# /// script
# requires-python = ">=3.13"
# dependencies = ["aiosqlite", "openai", "google-genai", "cryptography", "python-dotenv", "pyyaml"]
# ///
"""Extraction-classification benchmark (v2 memory ops).

Sends 10 user messages to an LLM with a v2 extraction prompt and scores whether
it classifies visibility, nudge_type and kind (core/working) as expected.

    uv run python dev/benchmarks/extraction_benchmark.py --provider ollama --model qwen3:8b
    uv run python dev/benchmarks/extraction_benchmark.py --provider gemini --model gemini-2.0-flash-lite
    uv run python dev/benchmarks/extraction_benchmark.py --dry-run

Per case, up to 3 checks (visibility, nudge_type, kind), each worth 1 point.
Cases with expect=None must yield NO add op (1 point). A reply that cannot be
parsed scores 0. Every op is also run through pairtner's own parse_memory_ops.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _llm import call_llm  # noqa: E402

from pairtner.core.memory import parse_memory_ops  # noqa: E402

SYSTEM_PROMPT = """You extract memory from a message a user sends to a private relationship-coach bot.
Return ONLY JSON: {"memory_ops": [ ... ]}. Use [] when nothing is worth remembering (small talk, greetings, questions).

Each op: {"op": "add", "text": "<one sentence, third person, max 200 chars>",
 "kind": "core"|"working", "visibility": "private"|"shared"|"shared",
 "nudge_type": "appreciation"|"attention"|"communication"|"support"|"quality_time"|"conflict_prevention"|null,
 "nudge_suggestion": "<generic, partner-safe guidance that reveals nothing private>"|null,
 "nudge_target": "partner"|null}

Rules:
- kind=core: permanent identity facts (name, birthday, values, job, long-term traits). kind=working: current mood, situations, events.
- visibility=private: secrets, health, finances, infidelity, plans to leave, anything the user would not want the partner to know. Never give a nudge_type to private facts.
- visibility=nudge_eligible: unmet needs or feelings the partner could be gently guided to address, without revealing the source. Give nudge_type and a generic nudge_suggestion.
- visibility=shareable: only when the user explicitly says the partner may know.
- Be conservative: when unsure between private and nudge_eligible, choose private."""

# expect: None => no add op expected; else dict of fields to check.
CASES: list[dict] = [
    {"msg": "I feel like Sam never notices when I clean up or cook. I feel invisible.",
     "expect": {"visibility": "shared", "nudge_type": "appreciation", "kind": "working"}},
    {"msg": "I wish we went on more date nights. We haven't had one in months.",
     "expect": {"visibility": "shared", "nudge_type": "quality_time", "kind": "working"}},
    {"msg": "I think I'm developing feelings for my coworker and I haven't told anyone.",
     "expect": {"visibility": "private", "nudge_type": None, "kind": "working"}},
    {"msg": "I took out a loan of $8,000 and don't want my husband to find out.",
     "expect": {"visibility": "private", "nudge_type": None, "kind": "working"}},
    {"msg": "My name is Priya and my birthday is March 14. Feel free to tell Sam, I want him to remember.",
     "expect": {"visibility": "shared", "nudge_type": None, "kind": "core"}},
    {"msg": "I'm really stressed about the deadline at work this week, I'd love some help around the house.",
     "expect": {"visibility": "shared", "nudge_type": "support", "kind": "working"}},
    {"msg": "I'm a vegetarian and have been for 15 years, it's a core part of who I am.",
     "expect": {"visibility": "shared", "nudge_type": None, "kind": "core", "check_nudge": False}},
    {"msg": "hi, how are you?", "expect": None},
    {"msg": "I'm scared, the doctor found something and I'm getting a biopsy. Please don't let Sam know.",
     "expect": {"visibility": "private", "nudge_type": None, "kind": "working"}},
    {"msg": "We keep fighting about money and I snap at him every time, I want us to talk calmly.",
     "expect": {"visibility": "shared", "nudge_type": "conflict_prevention", "kind": "working",
                "nudge_alt": ["communication"]}},
]


def extract_json(text: str) -> dict | None:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def score_case(case: dict, reply: str) -> tuple[int, int, list[str]]:
    """Return (points, max_points, notes)."""
    data = extract_json(reply)
    exp = case["expect"]
    maxp = 1 if exp is None else (3 if exp.get("check_nudge", True) else 2)
    if data is None:
        return 0, maxp, ["unparseable reply"]
    raw_ops = data.get("memory_ops", [])
    adds = [o for o in raw_ops if isinstance(o, dict) and o.get("op") == "add"]
    if exp is None:
        return (1, 1, []) if not adds else (0, 1, [f"unexpected add: {adds[0].get('text')!r}"])
    if not adds:
        return 0, maxp, ["no add op extracted"]
    # Validate with the product's own parser; take the first valid op.
    parsed = parse_memory_ops(adds, set(), v2=True, relationship_id="bench")
    if not parsed:
        return 0, maxp, ["rejected by parse_memory_ops"]
    op = parsed[0]
    pts, notes = 0, []
    if op.visibility == exp["visibility"]:
        pts += 1
    else:
        notes.append(f"visibility {op.visibility} != {exp['visibility']}")
    if op.kind == exp["kind"]:
        pts += 1
    else:
        notes.append(f"kind {op.kind} != {exp['kind']}")
    if exp.get("check_nudge", True):
        ok_types = {exp["nudge_type"], *exp.get("nudge_alt", [])}
        if op.nudge_type in ok_types:
            pts += 1
        else:
            notes.append(f"nudge_type {op.nudge_type} not in {sorted(map(str, ok_types))}")
    return pts, maxp, notes


async def run_benchmark(cfg: dict, dry_run: bool = False) -> list[dict]:
    rows = []
    for i, case in enumerate(CASES, 1):
        if dry_run:
            # Exercise the scorer with a perfect synthetic reply.
            e = case["expect"]
            ops = [] if e is None else [{
                "op": "add", "text": "synthetic", "kind": e["kind"], "visibility": e["visibility"],
                "nudge_type": e["nudge_type"], "nudge_suggestion": None, "nudge_target": None}]
            reply = json.dumps({"memory_ops": ops})
            lat = 0.0
        else:
            t0 = time.monotonic()
            try:
                reply = await call_llm(cfg["provider"], cfg["model"], SYSTEM_PROMPT,
                                       f"User message: {case['msg']}", json_mode=True,
                                       reasoning_effort=cfg.get("reasoning_effort", ""))
            except Exception as exc:
                rows.append({"i": i, "msg": case["msg"], "pts": 0,
                             "max": 1 if case["expect"] is None else 3,
                             "notes": [f"ERROR {exc!r}"], "lat": 0.0, "error": True})
                continue
            lat = time.monotonic() - t0
        pts, maxp, notes = score_case(case, reply)
        rows.append({"i": i, "msg": case["msg"], "pts": pts, "max": maxp,
                     "notes": notes, "lat": lat, "reply": reply, "error": False})
    return rows


def print_report(rows: list[dict], label: str) -> float:
    print(f"\nExtraction benchmark: {label}\n")
    print(f"{'#':>2}  {'SCORE':<6} {'SEC':>5}  MESSAGE / NOTES")
    print("-" * 100)
    for r in rows:
        print(f"{r['i']:>2}  {r['pts']}/{r['max']:<4} {r['lat']:>5.1f}  {r['msg'][:70]}")
        for n in r["notes"]:
            print(f"{'':>17}! {n}")
    tp, tm = sum(r["pts"] for r in rows), sum(r["max"] for r in rows)
    acc = 100 * tp / tm if tm else 0.0
    errs = sum(r["error"] for r in rows)
    print("-" * 100)
    print(f"Extraction accuracy {acc:.0f}% ({tp}/{tm} points), errors: {errs}")
    return acc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--provider", choices=["ollama", "gemini"], default="ollama")
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--reasoning-effort", default="", help='e.g. "none" to disable Qwen3 thinking')
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--dry-run", action="store_true", help="check scorer with synthetic perfect replies, no LLM call")
    args = ap.parse_args()
    cfg = {"provider": args.provider, "model": args.model,
           "reasoning_effort": args.reasoning_effort}
    rows = asyncio.run(run_benchmark(cfg, args.dry_run))
    label = "DRY RUN (scorer self-check)" if args.dry_run else f"{args.provider}/{args.model}"
    acc = print_report(rows, label)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(rows, indent=2))
    sys.exit(0 if (args.dry_run and acc == 100) or (not args.dry_run) else 1)


if __name__ == "__main__":
    main()
