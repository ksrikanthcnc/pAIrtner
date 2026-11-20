# /// script
# requires-python = ">=3.13"
# dependencies = ["aiosqlite", "openai", "google-genai", "cryptography", "python-dotenv", "pyyaml"]
# ///
"""Summary-level comparison: 5 levels vs 9 levels on 6 months of couple data.

    uv run python dev/benchmarks/level_comparison.py
    uv run python dev/benchmarks/level_comparison.py --seed 7 --months 6

Pipeline
  1. Generate a deterministic (seeded) 6-month history for Alice & Bob
     (messages + extracted facts) into one template SQLite DB.
  2. Copy the template twice; replay the 6 months day by day, calling the real
     ``run_periodic_summaries`` each day with a mock LLM:
        5-level: daily, weekly, monthly, quarterly, yearly
        9-level: all SUMMARY_INTERVALS
  3. Compare LLM calls, summaries, archiving, context size, information loss
     and retrieval of the "argument" weeks.

Mock LLM: keeps the top-N items by intensity (N grows with the period length,
see ``cap_for``) and writes them into the narrative together with their
``#<fact id>`` references. Higher levels therefore compress lower summaries and
may drop facts, which is what the information-loss metrics measure.
"""
from __future__ import annotations

import argparse
import asyncio
import math
import random
import re
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _llm import ROOT  # noqa: E402,F401  (puts repo root on sys.path)

from pairtner.core import summary as summ  # noqa: E402
from pairtner.db import create_user, init_db, set_partner  # noqa: E402
from pairtner.storage.migrate_v2 import migrate_to_v2  # noqa: E402
from pairtner.storage.schema_v2 import make_relationship_id  # noqa: E402

ALICE, BOB = 3001, 3002
USERS = (ALICE, BOB)
NAMES = {ALICE: "Alice", BOB: "Bob"}
REL = make_relationship_id(ALICE, BOB)

START = date(2026, 1, 5)  # a Monday: week 1 = ISO week 2

LEVELS_5 = ["daily", "weekly", "monthly", "quarterly", "yearly"]
LEVELS_9 = [name for name, _ in summ.SUMMARY_INTERVALS]

# --------------------------------------------------------------------------
# Realistic data generator
# --------------------------------------------------------------------------

# phase name, (first_week, last_week) 1-based inclusive
PHASES = [
    ("honeymoon", 1, 4),
    ("settling", 5, 8),
    ("argument", 9, 12),
    ("quiet", 13, 16),
    ("holiday", 17, 20),
    ("normal", 21, 24),
]


def phase_of(week: int) -> str:
    for name, a, b in PHASES:
        if a <= week <= b:
            return name
    return "normal"


# (min, max) messages per week and conversations per week
MSG_RANGE = {"honeymoon": (22, 30), "settling": (12, 18), "quiet": (5, 8),
             "holiday": (20, 30), "normal": (12, 18)}
ARG_MSGS = {9: (12, 16), 10: (28, 32), 11: (20, 24), 12: (14, 18)}  # spike, then resolution
CONV_RANGE = {"honeymoon": (1, 2), "settling": (1, 1), "quiet": (0, 1),
              "holiday": (1, 2), "normal": (1, 1)}
ARG_CONVS = {9: 1, 10: 1, 11: 2, 12: 1}

MESSAGES = {
    "honeymoon": [
        "I loved our dinner last night, thank you for cooking",
        "Can we plan a weekend trip to the coast?",
        "Your surprise flowers made my whole week",
        "I keep smiling at work thinking about Saturday",
        "Let's try that new Thai place on Friday",
        "I'm so happy we moved in together",
        "Can you pick up the kids from school today? I'll cook",
        "Work was great, my manager loved the proposal",
    ],
    "settling": [
        "Whose turn is it to take out the trash?",
        "Work is a bit heavy this week, sorry if I'm tired",
        "Did you pay the electricity bill?",
        "Shall we do groceries on Sunday?",
        "The kids have a school event on Thursday",
        "I could use some quiet time tonight",
        "Let's decide on the holiday plans soon",
    ],
    "argument": [
        "You never told me you'd be late again, I waited an hour",
        "I feel like I do all the chores and you don't notice",
        "You always take your mother's side",
        "I'm done talking, I need some space tonight",
        "I'm sorry I raised my voice earlier",
        "Can we talk about what happened calmly?",
        "I hear you, I should have called and I'll do my share",
        "Let's write down a chore rota that works for both of us",
    ],
    "quiet": [
        "Running late, don't wait for dinner",
        "Kids are fine, homework done",
        "Ok",
        "Can you get milk on the way home?",
        "Busy week, talk later",
    ],
    "holiday": [
        "Your parents arrive on the 23rd, where do they sleep?",
        "I'm stressed about gifts and the budget",
        "The kids are overexcited, school is out",
        "Thank you for handling the travel booking",
        "I can't host both families, it's too much",
        "That was a lovely evening with the tree",
        "We need to split the cooking, I'm exhausted",
    ],
    "normal": [
        "Date night this Friday? I booked a sitter",
        "Thanks for doing the dishes, I noticed",
        "Work is calmer now, I can take Thursday pickup",
        "Let's save for the summer trip",
        "I'm proud of how we handle things lately",
        "Kids want a picnic on Sunday",
    ],
}

# fact templates: (text, topic, base_intensity)
FACTS = {
    "honeymoon": [
        ("{n} loved the surprise dinner their partner cooked", "emotions", 0.5),
        ("{n} wants a weekend trip to the coast together", "plans", 0.4),
        ("{n} feels excited about moving in together", "emotions", 0.6),
        ("{n} shares school pickup with their partner", "kids", 0.3),
        ("{n} got positive feedback on a work proposal", "work", 0.4),
        ("{n} enjoys trying new restaurants as a couple", "plans", 0.3),
    ],
    "settling": [
        ("{n} wants a clearer chore schedule at home", "chores", 0.5),
        ("{n} is tired from a heavy workload", "work", 0.5),
        ("{n} worries about splitting the electricity bill fairly", "money", 0.5),
        ("{n} has a school event to attend on Thursday", "kids", 0.3),
        ("{n} needs quiet time in the evenings", "emotions", 0.4),
    ],
    "argument": [
        ("{n} felt ignored when their partner came home late without calling", "argument", 0.9),
        ("{n} resents carrying most of the chores", "argument", 0.85),
        ("{n} feels their partner always sides with their mother", "argument", 0.8),
        ("{n} asked for space after raising voices", "argument", 0.8),
        ("{n} is hurt that appreciation has been missing lately", "argument", 0.75),
    ],
    "resolution": [
        ("{n} apologised for raising their voice during the argument", "argument", 0.8),
        ("{n} and partner agreed on a written chore rota", "argument", 0.75),
        ("{n} promised to call when running late", "argument", 0.7),
        ("{n} felt heard after the calm conversation", "argument", 0.7),
    ],
    "quiet": [
        ("{n} is busy at work and communicates in short messages", "work", 0.3),
        ("{n} handled kids' homework this week", "kids", 0.25),
        ("{n} feels a bit distant but not upset", "emotions", 0.4),
    ],
    "holiday": [
        ("{n} is stressed about hosting both families", "family", 0.7),
        ("{n} worries about the holiday gift budget", "money", 0.65),
        ("{n} thanked their partner for handling travel bookings", "plans", 0.4),
        ("{n} is exhausted from cooking and wants shared duties", "chores", 0.65),
        ("{n} enjoyed the evening decorating the tree", "emotions", 0.45),
        ("{n} wants the kids to have calm holidays", "kids", 0.5),
    ],
    "normal": [
        ("{n} booked a sitter for Friday date night", "plans", 0.4),
        ("{n} noticed and appreciated the dishes being done", "emotions", 0.35),
        ("{n} will take over Thursday school pickup", "kids", 0.35),
        ("{n} wants to save for a summer trip", "money", 0.45),
        ("{n} is proud of how the couple handles conflict now", "emotions", 0.55),
    ],
}


@dataclass
class GenStats:
    messages: int = 0
    facts: int = 0
    conversations: int = 0
    arg_fact_ids: list[int] = field(default_factory=list)
    intensity: dict[int, float] = field(default_factory=dict)
    label: dict[int, str] = field(default_factory=dict)
    week_of: dict[int, int] = field(default_factory=dict)


def _ts(d: date, minute_of_day: int) -> str:
    return (datetime(d.year, d.month, d.day) + timedelta(minutes=minute_of_day)).strftime("%Y-%m-%d %H:%M:%S")


async def generate_couple_data(db_path: str, months: int = 6, seed: int = 42) -> GenStats:
    """Create the DB and fill it with a deterministic realistic history."""
    rng = random.Random(seed)
    await init_db(db_path)
    await _bootstrap(db_path)
    weeks = round(months * 4)
    stats = GenStats()
    conn = sqlite3.connect(db_path)
    try:
        for w in range(1, weeks + 1):
            phase = phase_of(w)
            wk_start = START + timedelta(weeks=w - 1)
            if phase == "argument":
                lo, hi = ARG_MSGS[w]
                n_conv = ARG_CONVS[w]
            else:
                lo, hi = MSG_RANGE[phase]
                n_conv = rng.randint(*CONV_RANGE[phase])
            n_msgs = rng.randint(lo, hi)
            conv_days = sorted(rng.sample(range(7), n_conv)) if n_conv else []

            # messages: conversation days carry ~70% of the volume
            per_day = [0] * 7
            conv_share = int(n_msgs * 0.7) if conv_days else 0
            for d in conv_days:
                per_day[d] += conv_share // len(conv_days)
            for _ in range(n_msgs - sum(per_day)):
                per_day[rng.randrange(7)] += 1
            pool = MESSAGES["argument"] if phase == "argument" else MESSAGES[phase]
            for dow, cnt in enumerate(per_day):
                d = wk_start + timedelta(days=dow)
                for i in range(cnt):
                    uid = USERS[(i + rng.randint(0, 1)) % 2] if cnt > 1 else rng.choice(USERS)
                    conn.execute(
                        "INSERT INTO messages (user_id, relationship_id, role, content, created_at) "
                        "VALUES (?,?,?,?,?)",
                        (uid, REL, "user", rng.choice(pool), _ts(d, 8 * 60 + i * 7 + rng.randint(0, 5))))
                    stats.messages += 1

            # facts: 3-5 per conversation per partner
            for dow in conv_days:
                d = wk_start + timedelta(days=dow)
                stats.conversations += 1
                for uid in USERS:
                    key = phase
                    if phase == "argument" and w >= 11:
                        key = "resolution"
                    templ = FACTS[key]
                    for tpl, topic, base in rng.sample(templ, min(len(templ), rng.randint(3, 5))):
                        text = tpl.format(n=NAMES[uid])
                        inten = round(min(1.0, max(0.1, base + rng.uniform(-0.08, 0.08))), 2)
                        status = "resolved" if (key == "resolution" and rng.random() < 0.6) else "active"
                        vis = rng.choices(["private", "nudge_eligible", "shareable"], [3, 5, 2])[0]
                        cur = conn.execute(
                            "INSERT INTO facts (owner_id, relationship_id, text, visibility, kind, "
                            "status, intensity, msg_source, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                            (uid, REL, text, vis, "working", status, inten, "user",
                             _ts(d, 20 * 60 + rng.randint(0, 120))))
                        fid = cur.lastrowid
                        stats.facts += 1
                        stats.intensity[fid] = inten
                        stats.label[fid] = text
                        stats.week_of[fid] = w
                        if topic == "argument":
                            stats.arg_fact_ids.append(fid)
        conn.commit()
    finally:
        conn.close()
    return stats


async def _bootstrap(db_path: str) -> None:
    await create_user(db_path, ALICE, "Alice")
    await create_user(db_path, BOB, "Bob")
    await set_partner(db_path, ALICE, BOB)
    await migrate_to_v2(db_path)


# --------------------------------------------------------------------------
# Mock LLM
# --------------------------------------------------------------------------

def cap_for(period_type: str) -> int:
    """Items a mock summary can keep: longer periods keep a bit more, never all."""
    return 4 + int(1.2 * math.sqrt(summ._DAYS[period_type]))


_ITEM_RE = re.compile(r"^\[(\d+)\] \((\w+)\) (.*)$")
_REF_RE = re.compile(r"#(\d+)")


class MockLLM:
    """Deterministic stand-in for the summariser LLM. No network."""

    def __init__(self, intensity: dict[int, float]):
        self.intensity = intensity
        self.calls = 0
        self.calls_by_level: dict[str, int] = {}

    async def generate(self, user_content: str, system_prompt: str, model=None, api_key_override=None):
        self.calls += 1
        head, _, body = user_content.partition("\nItems:\n")
        _, ptype, pid = head.split()
        self.calls_by_level[ptype] = self.calls_by_level.get(ptype, 0) + 1

        refs: dict[int, str] = {}  # fact id -> status text
        n_items = 0
        for line in body.splitlines():
            m = _ITEM_RE.match(line)
            if not m:
                continue
            n_items += 1
            iid, status, text = int(m[1]), m[2], m[3]
            found = _REF_RE.findall(text)
            if found:  # lower-level summary: carry its references forward
                for r in found:
                    refs.setdefault(int(r), self._text_for(int(r), text))
            else:  # working fact (items are DB ids of working facts)
                refs.setdefault(iid, f"{text} #{iid}")
        kept = sorted(refs, key=lambda f: (-self.intensity.get(f, 0.5), f))[:cap_for(ptype)]
        kept.sort()
        parts = [refs[f] for f in kept]
        narrative = (f"Summary of {n_items} items from {ptype} {pid}. "
                     + ". ".join(parts) + ".")
        return {"resolved": [], "ongoing": [], "new": [], "narrative": narrative}

    @staticmethod
    def _text_for(fid: int, summary_text: str) -> str:
        # recover the sentence for a reference from the lower summary narrative
        for sent in summary_text.split(". "):
            if f"#{fid}" in sent:
                return sent.strip().rstrip(".")
        return f"item #{fid}"


# --------------------------------------------------------------------------
# Simulation + metrics
# --------------------------------------------------------------------------

def words(s: str) -> int:
    return len(s.split())


@dataclass
class RunResult:
    name: str
    levels: list[str]
    llm_calls: int = 0
    calls_by_level: dict[str, int] = field(default_factory=dict)
    summaries_by_level: dict[str, int] = field(default_factory=dict)
    summaries: int = 0
    working_archived: int = 0
    total_archived: int = 0
    avg_context_words: float = 0.0
    context_samples: list[tuple[str, int]] = field(default_factory=list)
    info_loss: float = 0.0           # intensity-weighted, facts absent from live summaries at end
    never_referenced: int = 0        # archived working facts never in any summary
    arg_recall_end: float = 0.0
    arg_recall_checkpoint: float = 0.0
    arg_words_end: int = 0
    arg_monthly_recall: float = 0.0
    arg_monthly_words: int = 0
    arg_density_end: float = 0.0


SUMMARY_KINDS = tuple(n for n, _ in summ.SUMMARY_INTERVALS)


def _context_words(conn: sqlite3.Connection, day: date) -> int:
    """Words the bot would load for *day*: live summaries + live working facts."""
    cutoff = (day + timedelta(days=1)).isoformat()
    marks = ",".join("?" * len(SUMMARY_KINDS))
    total = 0
    for (text,) in conn.execute(
            f"SELECT text FROM facts WHERE archived_at IS NULL AND owner_id IS NOT NULL "
            f"AND kind IN ({marks})", SUMMARY_KINDS):
        total += words(text)
    for (text,) in conn.execute(
            "SELECT text FROM facts WHERE archived_at IS NULL AND kind='working' "
            "AND status IN ('active','resolved') AND created_at < ?", (cutoff,)):
        total += words(text)
    return total


def _refs_in(conn: sqlite3.Connection, live_only: bool, kinds: tuple[str, ...] | None = None):
    """(set of referenced fact ids, words of summaries) over matching summaries."""
    kinds = kinds or SUMMARY_KINDS
    marks = ",".join("?" * len(kinds))
    sql = f"SELECT text FROM facts WHERE owner_id IS NOT NULL AND kind IN ({marks})"
    if live_only:
        sql += " AND archived_at IS NULL"
    refs: set[int] = set()
    texts: list[str] = []
    for (t,) in conn.execute(sql, kinds):
        texts.append(t)
        refs.update(int(x) for x in _REF_RE.findall(t))
    return refs, texts


async def run_config(name: str, levels: list[str], template: str, stats: GenStats,
                     workdir: Path, days: int) -> RunResult:
    db_path = str(workdir / f"{name}.db")
    shutil.copy(template, db_path)
    llm = MockLLM(stats.intensity)
    res = RunResult(name=name, levels=levels)
    checkpoint_day = START + timedelta(weeks=16)  # right after the quiet period
    ctx_samples: list[int] = []
    cp_done = False

    conn = sqlite3.connect(db_path)
    try:
        for i in range(days):
            day = START + timedelta(days=i)
            now = datetime(day.year, day.month, day.day, 23, 59, tzinfo=timezone.utc)
            for uid in USERS:
                created = await summ.run_periodic_summaries(
                    db_path, llm, uid, REL, now=now, intervals=levels)
                for k, v in created.items():
                    res.summaries_by_level[k] = res.summaries_by_level.get(k, 0) + v
            if i % 7 == 6:
                n = _context_words(conn, day)
                ctx_samples.append(n)
                res.context_samples.append((day.isoformat(), n))
            if not cp_done and day >= checkpoint_day:
                refs, _ = _refs_in(conn, live_only=True)
                res.arg_recall_checkpoint = _recall(refs, stats.arg_fact_ids)
                cp_done = True

        res.llm_calls = llm.calls
        res.calls_by_level = dict(llm.calls_by_level)
        res.summaries = sum(res.summaries_by_level.values())
        res.avg_context_words = sum(ctx_samples) / max(1, len(ctx_samples))
        res.working_archived = conn.execute(
            "SELECT COUNT(*) FROM facts WHERE kind='working' AND archived_at IS NOT NULL").fetchone()[0]
        res.total_archived = conn.execute(
            "SELECT COUNT(*) FROM facts WHERE archived_at IS NOT NULL").fetchone()[0]

        all_refs, _ = _refs_in(conn, live_only=False)
        live_refs, live_texts = _refs_in(conn, live_only=True)
        archived_ids = {r[0] for r in conn.execute(
            "SELECT id FROM facts WHERE kind='working' AND archived_at IS NOT NULL")}
        res.never_referenced = len(archived_ids - all_refs)
        lost_w = sum(stats.intensity[f] for f in archived_ids if f not in live_refs)
        tot_w = sum(stats.intensity[f] for f in archived_ids) or 1.0
        res.info_loss = lost_w / tot_w

        # retrieval: "what happened during the argument"
        res.arg_recall_end = _recall(live_refs, stats.arg_fact_ids)
        arg_set = {str(f) for f in stats.arg_fact_ids}
        rel_words = 0
        arg_tokens = 0
        for t in live_texts:
            hits = [x for x in _REF_RE.findall(t) if x in arg_set]
            if hits:
                rel_words += words(t)
                arg_tokens += len(hits)
        res.arg_words_end = rel_words
        res.arg_density_end = arg_tokens / max(1, rel_words)
        m_refs, m_texts = _refs_in(conn, live_only=False, kinds=("monthly",))
        res.arg_monthly_recall = _recall(m_refs, stats.arg_fact_ids)
        res.arg_monthly_words = sum(words(t) for t in m_texts)
    finally:
        conn.close()
    return res


def _recall(refs: set[int], targets: list[int]) -> float:
    return len(refs & set(targets)) / max(1, len(targets))


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def _diff(a: float, b: float, lower_is_better: bool | None = None, pct: bool = True) -> str:
    if a == b:
        return "same"
    if a == 0:
        return f"{b - a:+.2f}"
    d = (b - a) / a * 100
    tag = ""
    if lower_is_better is not None:
        tag = " (better)" if (d < 0) == lower_is_better else " (worse)"
    return f"{d:+.0f}%{tag}"


def report(a: RunResult, b: RunResult, stats: GenStats) -> None:
    rows = [
        ("Summaries created", a.summaries, b.summaries, None, "{:d}"),
        ("Working facts archived", a.working_archived, b.working_archived, None, "{:d}"),
        ("Total rows archived", a.total_archived, b.total_archived, None, "{:d}"),
        ("LLM calls", a.llm_calls, b.llm_calls, True, "{:d}"),
        ("Context words (avg)", a.avg_context_words, b.avg_context_words, None, "{:.0f}"),
        ("Info loss score", a.info_loss, b.info_loss, True, "{:.2f}"),
        ("Facts never in a summary", a.never_referenced, b.never_referenced, True, "{:d}"),
        ("Argument recall @wk16", a.arg_recall_checkpoint, b.arg_recall_checkpoint, False, "{:.2f}"),
        ("Argument recall @end", a.arg_recall_end, b.arg_recall_end, False, "{:.2f}"),
        ("Argument ctx words @end", a.arg_words_end, b.arg_words_end, None, "{:d}"),
        ("Argument density @end", a.arg_density_end, b.arg_density_end, False, "{:.3f}"),
        ("Argument recall (monthly)", a.arg_monthly_recall, b.arg_monthly_recall, False, "{:.2f}"),
    ]
    w = 26
    print()
    print(f"Data: {stats.messages} messages, {stats.facts} facts, {stats.conversations} "
          f"conversations, {len(stats.arg_fact_ids)} argument facts "
          f"({len(stats.arg_fact_ids) / max(1, stats.facts):.0%})")
    print(f"5-level: {', '.join(a.levels)}")
    print(f"9-level: {', '.join(b.levels)}")
    print()
    print(f"{'Metric':<{w}}| {'5-level':>9} | {'9-level':>9} | Difference")
    print("─" * (w + 40))
    for label, x, y, lib, fmt in rows:
        print(f"{label:<{w}}| {fmt.format(x):>9} | {fmt.format(y):>9} | {_diff(x, y, lib)}")
    print()
    print("Summaries / LLM calls by level")
    print(f"  {'level':<11}{'5-lvl sum':>10}{'9-lvl sum':>10}{'5-lvl calls':>13}{'9-lvl calls':>13}")
    for lv in LEVELS_9:
        print(f"  {lv:<11}{a.summaries_by_level.get(lv, 0):>10}{b.summaries_by_level.get(lv, 0):>10}"
              f"{a.calls_by_level.get(lv, 0):>13}{b.calls_by_level.get(lv, 0):>13}")
    print()
    print("Context words by weekly sample (every 4th week)")
    print(f"  {'date':<12}{'5-level':>9}{'9-level':>9}")
    for (d, x), (_, y) in list(zip(a.context_samples, b.context_samples))[::4]:
        print(f"  {d:<12}{x:>9}{y:>9}")

    # verdict
    print()
    print("7d -> 30d jump (argument weeks 9-12):")
    print(f"  monthly-level argument recall: 5-level {a.arg_monthly_recall:.0%} "
          f"vs 9-level {b.arg_monthly_recall:.0%}")
    print(f"  argument recall at week 16:    5-level {a.arg_recall_checkpoint:.0%} "
          f"vs 9-level {b.arg_recall_checkpoint:.0%}")
    better_loss = b.info_loss < a.info_loss - 0.02
    better_recall = b.arg_recall_end > a.arg_recall_end + 0.02
    cost = (b.llm_calls - a.llm_calls) / max(1, a.llm_calls) * 100
    print()
    print("VERDICT")
    if better_loss or better_recall:
        print(f"  9-level preserves more (info loss {a.info_loss:.2f} -> {b.info_loss:.2f}, "
              f"argument recall {a.arg_recall_end:.0%} -> {b.arg_recall_end:.0%}) "
              f"at {cost:+.0f}% LLM calls.")
        print("  Worth it only if context fidelity matters more than the extra calls.")
    else:
        print(f"  9-level gives no measurable benefit (info loss {a.info_loss:.2f} vs "
              f"{b.info_loss:.2f}, argument recall {a.arg_recall_end:.0%} vs "
              f"{b.arg_recall_end:.0%}) and costs {cost:+.0f}% LLM calls.")
        print("  Keep the 5-level default.")
    starved = [f"{r.name}:{lv}" for r in (a, b) for lv in r.levels
               if lv in ("monthly", "quarterly", "bimonthly", "biweekly")
               and r.summaries_by_level.get(lv, 0) == 0]
    if starved:
        print(f"  WARNING: no summaries at {', '.join(starved)}. run_periodic_summaries derives a "
              "level's start cursor from unarchived working facts when that level has no summary "
              "yet; lower levels archive them first, so higher levels can never bootstrap "
              "(summary.py behaviour, not a benchmark bug). Skews the comparison.")
    print("  Note: results depend on the mock's compression model (cap_for); "
          "real LLM summaries may differ.")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--months", type=int, default=6)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--keep", action="store_true", help="keep the temp DBs and print their path")
    args = ap.parse_args()

    tmp = tempfile.TemporaryDirectory(prefix="level-cmp-")
    workdir = Path(tmp.name)
    template = str(workdir / "template.db")
    print(f"Generating {args.months} months of couple data (seed={args.seed}) ...")
    stats = await generate_couple_data(template, months=args.months, seed=args.seed)
    days = args.months * 28 + 14  # run past the last week so final periods close

    print("Running 5-level consolidation ...")
    r5 = await run_config("five", LEVELS_5, template, stats, workdir, days)
    print("Running 9-level consolidation ...")
    r9 = await run_config("nine", LEVELS_9, template, stats, workdir, days)
    report(r5, r9, stats)
    if args.keep:
        print(f"\nDBs kept in {workdir}")
        tmp.cleanup = lambda: None  # type: ignore[method-assign]


if __name__ == "__main__":
    asyncio.run(main())
