"""Golden-prompt collector: every model-visible prompt the bot can assemble.

``collect()`` returns ``{case_id: {"system": ..., "user": ...}}``.  The JSON in
``tests/golden/prompts_v0.1.0.json`` was generated from untouched v0.1.0 code
and is the byte-identical oracle for the prompt-pack refactor.  It must never
be regenerated after that refactor starts.

    uv run python -m tests.golden_prompts --write

Only public prompt-building entry points are used and nothing is passed for
``prompts_dir`` / ``voices_dir`` / ``persona_file``: the Config and function
defaults are relied upon, so the same file runs before and after the refactor.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_PATH = ROOT / "tests" / "golden" / "prompts_v0.1.0.json"

# Fixed reference instant; all seeded timestamps are relative to it so that
# ordering is explicit and the prompt text never contains a wall-clock value.
NOW = datetime.now(timezone.utc).replace(microsecond=0)

A_ID, B_ID = 1001, 1002


def activate_default_prompts() -> None:
    """Make the default prompt set active (works before and after packs)."""
    try:
        from pairtner import pack
    except ImportError:
        from pairtner.core.prompts import load_persona

        load_persona("prompts/persona.md")
    else:
        pack.activate("packs/default")


@contextlib.contextmanager
def _in_root():
    prev = os.getcwd()
    os.chdir(ROOT)
    try:
        yield
    finally:
        os.chdir(prev)


# ---------------------------------------------------------------------------
# Direct matrix over pairtner/prompts.py
# ---------------------------------------------------------------------------

VOICE = "VOICE-TEXT: warm, brief, informal."
DAMP = "DAMPEN-TEXT: be gentle and indirect."
TASK = "TASK-TEXT: do the special task."


def _direct() -> dict[str, tuple[str, str]]:
    from pairtner.core import prompts as P
    from pairtner.core.dampening import get_dampening_instruction

    out: dict[str, tuple[str, str]] = {}

    short_hist = [("user", "I had a rough day"), ("assistant", "Sorry to hear that")]
    long_hist = [
        ("user", "m1 first"), ("assistant", "m2 reply"), ("user", "m3 again"),
        ("assistant", "m4 reply"), ("user", "m5 more"), ("assistant", "m6 reply"),
        ("user", "m7 later"), ("user", "m8 and then"), ("assistant", "m9 reply"),
    ]
    ctx = P.UserContext(
        user_name="Alice", partner_name="Bob",
        private_facts=["Alice likes tea", "Alice dislikes crowds"],
        shared_facts=["We love hiking", "We plan a trip in June"],
        recent_messages=short_hist,
    )
    ctx_long = P.UserContext(
        user_name="Alice", partner_name="Bob",
        private_facts=["Alice likes tea"], shared_facts=["We love hiking"],
        recent_messages=long_hist,
    )
    ctx_empty = P.UserContext()
    smart = P.SmartContext(
        recent_facts=["recent fact one", "recent fact two"],
        relevant_facts=["relevant fact one"],
        monthly_summaries=["2025-10: summary of October", "2025-09: summary of September"],
        total_fact_count=120,
    )
    smart_partial = P.SmartContext(relevant_facts=["only relevant fact"], total_fact_count=101)
    goals = P.build_goals_section(["Walk daily"], ["Save for a trip"])
    psum = P.build_partner_summary_block([
        {"theme": "feels unheard about chores", "sentiment": "negative"},
        {"theme": "enjoys quiet evenings", "sentiment": "positive"},
    ])
    msg = "Can we talk about the weekend?"

    # chat
    out["direct.chat.plain"] = P.build_chat_prompt(ctx, msg)
    out["direct.chat.style"] = P.build_chat_prompt(
        ctx, msg, voice_text=VOICE, dampening_instruction=DAMP)
    out["direct.chat.voice_only"] = P.build_chat_prompt(ctx, msg, voice_text=VOICE)
    out["direct.chat.damp_only"] = P.build_chat_prompt(
        ctx, msg, dampening_instruction=DAMP)
    out["direct.chat.goals"] = P.build_chat_prompt(ctx, msg, goals_text=goals)
    out["direct.chat.partner_summary"] = P.build_chat_prompt(
        ctx, msg, partner_summary=psum)
    out["direct.chat.smart_ctx"] = P.build_chat_prompt(ctx, msg, smart_ctx=smart)
    out["direct.chat.smart_ctx_partial"] = P.build_chat_prompt(
        ctx, msg, smart_ctx=smart_partial)
    out["direct.chat.history_long"] = P.build_chat_prompt(ctx_long, msg)
    out["direct.chat.empty_ctx"] = P.build_chat_prompt(ctx_empty, msg)
    out["direct.chat.everything"] = P.build_chat_prompt(
        ctx_long, msg, voice_text=VOICE, dampening_instruction=DAMP,
        partner_summary=psum, goals_text=goals, smart_ctx=smart)

    # tell
    out["direct.tell.plain"] = P.build_tell_prompt(ctx, msg)
    out["direct.tell.style"] = P.build_tell_prompt(
        ctx, msg, voice_text=VOICE, dampening_instruction=DAMP)
    out["direct.tell.damp_only"] = P.build_tell_prompt(
        ctx, msg, dampening_instruction=DAMP)
    out["direct.tell.goals"] = P.build_tell_prompt(ctx, msg, goals_text=goals)
    out["direct.tell.partner_summary"] = P.build_tell_prompt(
        ctx, msg, partner_summary=psum)
    out["direct.tell.smart_ctx"] = P.build_tell_prompt(ctx, msg, smart_ctx=smart)

    # mediate
    shared = ["We agreed to cook on weekends", "We want a quieter month"]
    pa = ["seems tired after shifts", "goes quiet when plans change"]
    pb = ["wants more help at home"]
    out["direct.mediate.facts_only"] = P.build_mediate_prompt(shared)
    out["direct.mediate.both"] = P.build_mediate_prompt(
        shared, perceptions_a=pa, perceptions_b=pb, name_a="Alice", name_b="Bob")
    out["direct.mediate.one_sided_a"] = P.build_mediate_prompt(
        shared, perceptions_a=pa, name_a="Alice", name_b="Bob")
    out["direct.mediate.one_sided_b"] = P.build_mediate_prompt(
        shared, perceptions_b=pb, name_a="Alice", name_b="Bob")
    out["direct.mediate.no_names"] = P.build_mediate_prompt(
        shared, perceptions_a=pa, perceptions_b=pb)
    out["direct.mediate.window"] = P.build_mediate_prompt(
        shared, perceptions_a=pa, perceptions_b=pb, name_a="Alice",
        name_b="Bob", window_label="the last 30 days")
    out["direct.mediate.no_facts_with_perceptions"] = P.build_mediate_prompt(
        [], perceptions_a=pa, perceptions_b=pb, name_a="Alice", name_b="Bob",
        window_label="the last day")

    # keep
    out["direct.keep.plain"] = P.build_keep_prompt(ctx, "Remember I like tea")
    out["direct.keep.style"] = P.build_keep_prompt(
        ctx, "Remember I like tea", voice_text=VOICE, dampening_instruction=DAMP)
    out["direct.keep.smart_ctx"] = P.build_keep_prompt(
        ctx, "Remember I like tea", smart_ctx=smart)

    # blunt
    out["direct.blunt.plain"] = P.build_blunt_prompt(ctx, msg)
    out["direct.blunt.style"] = P.build_blunt_prompt(
        ctx, msg, voice_text=VOICE, dampening_instruction=DAMP)
    out["direct.blunt.smart_ctx"] = P.build_blunt_prompt(ctx, msg, smart_ctx=smart)

    # digest / check-in summary
    out["direct.digest.facts_and_relays"] = P.build_digest_prompt(
        ["We booked tickets"], ["I love you, thanks for dinner"])
    out["direct.digest.relays_only"] = P.build_digest_prompt(
        [], ["Please call me later"])
    out["direct.digest.facts_only"] = P.build_digest_prompt(["We booked tickets"], [])
    out["direct.checkin.no_goals"] = P.build_checkin_summary_prompt(
        "A: went fine", "B: felt busy", ["We love hiking"])
    out["direct.checkin.goals"] = P.build_checkin_summary_prompt(
        "A: went fine", "B: felt busy", ["We love hiking"],
        goals="## Active Goals\n- Walk daily\n")
    out["direct.checkin.no_shared"] = P.build_checkin_summary_prompt(
        "A: went fine", "B: felt busy", [])

    # sections and blocks (strings are wrapped as (text, "x") only to share the dict shape)
    out["direct.goals.personal"] = (P.build_goals_section(["Walk daily", "Read more"], []), "x")
    out["direct.goals.shared"] = (P.build_goals_section([], ["Save for a trip"]), "x")
    out["direct.goals.both"] = (P.build_goals_section(["Walk daily"], ["Save for a trip"]), "x")
    out["direct.goals.empty"] = (P.build_goals_section([], []) or "<empty>", "x")
    out["direct.psummary.themes"] = (P.build_partner_summary_block([
        {"theme": "feels unheard", "sentiment": "negative"},
        {"theme": "likes surprises"},
    ]), "x")
    out["direct.psummary.empty"] = (P.build_partner_summary_block([]) or "<empty>", "x")
    out["direct.psummary.themeless"] = (P.build_partner_summary_block(
        [{"theme": "", "sentiment": "negative"}]) or "<empty>", "x")

    # system prompt
    out["direct.system.bare"] = (P.build_system_prompt(), "x")
    out["direct.system.all"] = (P.build_system_prompt(
        voice_text=VOICE, task_text=TASK, dampening_instruction=DAMP), "x")
    out["direct.system.task_only"] = (P.build_system_prompt(task_text=TASK), "x")
    out["direct.system.voice_only"] = (P.build_system_prompt(voice_text=VOICE), "x")

    # dampening levels (boundaries included)
    for level in (0.9, 0.8, 0.6, 0.5, 0.3, 0.2, 0.0):
        out[f"direct.dampening.{level}"] = (get_dampening_instruction(level), "x")
    return out


# ---------------------------------------------------------------------------
# Seeding helpers (explicit timestamps => deterministic ordering)
# ---------------------------------------------------------------------------


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class World:
    root: Path
    name: str
    db: str = ""
    llm: Any = None
    svc: Any = None
    config: Any = None
    _n: int = 0
    extra: dict = field(default_factory=dict)

    def stamp(self, age_days: float = 0.0) -> str:
        """Timestamp ``age_days`` ago, +1s per call so insertion order is kept."""
        self._n += 1
        return _fmt(NOW - timedelta(days=age_days) + timedelta(seconds=self._n))

    @property
    def couple_id(self) -> str:
        from pairtner import db as db_mod

        return db_mod.make_couple_id(A_ID, B_ID)

    async def x(self, sql: str, params: tuple = ()) -> int:
        from pairtner import db as db_mod

        return await db_mod._execute(self.db, sql, params)

    async def fact(
        self, owner: int, text: str, vis: str = "private", age: float = 1.0,
        category: str | None = None, fact_type: str = "stable",
    ) -> int:
        ts = self.stamp(age)
        return await self.x(
            "INSERT INTO facts (owner_id, text, visibility, source, category, "
            "fact_type, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (owner, text, vis, "seed", category, fact_type, ts, ts),
        )

    async def msg(self, uid: int, role: str, content: str, age: float = 0.5) -> None:
        await self.x(
            "INSERT INTO messages (user_id, role, content, created_at) VALUES (?,?,?,?)",
            (uid, role, content, self.stamp(age)),
        )

    async def perception(
        self, user_id: int, about_id: int, theme: str, sentiment: str = "neutral",
        conf: float = 0.8, age: float = 1.0,
    ) -> None:
        ts = self.stamp(age)
        await self.x(
            "INSERT INTO partner_perceptions (user_id, about_id, theme, sentiment, "
            "confidence, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (user_id, about_id, theme, sentiment, conf, ts, ts),
        )

    async def goal(self, uid: int, text: str, age: float = 3.0, status: str = "active") -> None:
        await self.x(
            "INSERT INTO user_goals (user_id, text, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?)", (uid, text, status, (ts := self.stamp(age)), ts),
        )

    async def shared_goal(self, text: str, age: float = 3.0, status: str = "active") -> None:
        await self.x(
            "INSERT INTO shared_goals (couple_id, text, created_by, status, created_at, "
            "updated_at) VALUES (?,?,?,?,?,?)",
            (self.couple_id, text, A_ID, status, (ts := self.stamp(age)), ts),
        )

    async def profile(self, uid: int, **fields: Any) -> None:
        from pairtner import db as db_mod

        await db_mod.update_user_profile(self.db, uid, **fields)

    async def pattern(self, uid: int, text: str, freq: int, ptype: str = "communication") -> None:
        from pairtner import db as db_mod

        await db_mod.record_pattern(self.db, uid, text, ptype)
        await self.x(
            "UPDATE user_patterns SET frequency = ? WHERE user_id = ? AND pattern = ?",
            (freq, uid, text),
        )

    async def interactions(self, pos: int, neg: int) -> None:
        from pairtner import db as db_mod

        for _ in range(pos):
            await db_mod.record_interaction(self.db, self.couple_id, A_ID, "positive")
        for _ in range(neg):
            await db_mod.record_interaction(self.db, self.couple_id, B_ID, "negative")

    async def event(self, when: str, etype: str, summary: str) -> None:
        await self.x(
            "INSERT INTO timeline_events (couple_id, event_type, summary, created_at) "
            "VALUES (?,?,?,?)", (self.couple_id, etype, summary, when),
        )

    async def relay(self, sender: int, receiver: int, draft: str, age: float,
                    edited: str | None = None) -> None:
        ts = self.stamp(age)
        await self.x(
            "INSERT INTO pending (sender_id, receiver_id, draft, edited_draft, status, "
            "created_at, sent_at) VALUES (?,?,?,?, 'sent', ?, ?)",
            (sender, receiver, draft, edited, ts, ts),
        )

    async def conflict(self, theme: str, status: str, age: float) -> None:
        ts = self.stamp(age)
        await self.x(
            "INSERT INTO conflicts (couple_id, theme, status, opened_at) VALUES (?,?,?,?)",
            (self.couple_id, theme, status, ts),
        )


async def _make_world(root: Path, name: str, cfg: dict, seed: bool) -> World:
    from pairtner import db as db_mod
    from pairtner.config import Config
    from pairtner.handlers import BotService
    from tests.helpers import MockLLMClient

    w = World(root=root, name=name)
    w.db = str(root / f"{name}.db")
    await db_mod.init_db(w.db)
    base: dict[str, Any] = dict(
        telegram_token="t", gemini_api_key="k", model_default="d",
        model_heavy=["h"], db_path=w.db, cooldown_minutes=0,
    )
    base.update(cfg)
    w.config = Config(**base)
    w.llm = MockLLMClient()
    w.svc = BotService(config=w.config, db_path=w.db, llm=w.llm)

    await db_mod.create_user(w.db, A_ID, "Alice")
    await db_mod.create_user(w.db, B_ID, "Bob")
    await db_mod.set_partner(w.db, A_ID, B_ID)
    for uid in (A_ID, B_ID):
        await db_mod.ensure_user_profile(w.db, uid)
        await db_mod.update_user_profile(w.db, uid, onboarding_done=1)

    if seed:
        await w.fact(A_ID, "Alice feels stressed about work", "private", 10)
        await w.fact(A_ID, "Alice likes morning walks", "private", 9)
        await w.fact(A_ID, "We agreed to cook together on weekends", "shared", 8)
        await w.fact(B_ID, "Bob is anxious about finances", "private", 7)
        await w.fact(B_ID, "We decided to try date nights", "shared", 6)
        await w.msg(A_ID, "user", "I had a tough day", 0.9)
        await w.msg(A_ID, "assistant", "I hear you. Want to talk about it?", 0.8)
        await w.msg(B_ID, "user", "Money is tight this month", 0.7)
        await w.msg(B_ID, "assistant", "That sounds stressful.", 0.6)
    return w


# ---------------------------------------------------------------------------
# Scenario registry
# ---------------------------------------------------------------------------

@dataclass
class Scenario:
    name: str
    fn: Callable[[World], Awaitable[None]]
    cfg: dict
    seed: bool
    calls: int


SCENARIOS: list[Scenario] = []


def scenario(name: str, *, calls: int = 1, seed: bool = True, **cfg: Any):
    def deco(fn: Callable[[World], Awaitable[None]]):
        SCENARIOS.append(Scenario(name, fn, cfg, seed, calls))
        return fn

    return deco


def _msgtext(w: World, uid: int, text: str):
    return w.svc.handle_message(uid, None, text)


# ── chat / tags ────────────────────────────────────────────────────────────

@scenario("cs.chat.plain")
async def _(w):
    await _msgtext(w, A_ID, "Can we plan something this weekend?")


@scenario("cs.chat.goals_and_perceptions")
async def _(w):
    await w.goal(A_ID, "Walk every morning")
    await w.goal(A_ID, "Read more books", 2)
    await w.shared_goal("Save for a trip")
    await w.perception(B_ID, A_ID, "feels unheard about chores", "negative", 0.8, 2)
    await w.perception(B_ID, A_ID, "enjoys quiet evenings together", "positive", 0.7, 1)
    await _msgtext(w, A_ID, "I want to talk about the chores")


@scenario("cs.chat.learned_voice")
async def _(w):
    await w.profile(A_ID, learned_voice=json.dumps(
        {"register": "casual", "sentence_length": "short", "emoji_use": "light"}),
        message_count=12)
    await _msgtext(w, A_ID, "hey what should we cook tonight")


@scenario("cs.chat.learned_voice_immature")
async def _(w):
    await w.profile(A_ID, learned_voice=json.dumps({"register": "casual"}),
                    message_count=3)
    await _msgtext(w, A_ID, "hey what should we cook tonight")


@scenario("cs.chat.voice_telugu")
async def _(w):
    await w.profile(A_ID, default_voice="telugu")
    await _msgtext(w, A_ID, "Can we plan something this weekend?")


@scenario("cs.chat.voice_formal")
async def _(w):
    await w.profile(A_ID, default_voice="formal_en")
    await _msgtext(w, A_ID, "Can we plan something this weekend?")


@scenario("cs.chat.voice_hindi")
async def _(w):
    await w.profile(A_ID, default_voice="hindi")
    await _msgtext(w, A_ID, "Can we plan something this weekend?")


@scenario("cs.chat.voice_unknown_fallback")
async def _(w):
    await w.profile(A_ID, default_voice="no_such_voice")
    await _msgtext(w, A_ID, "Can we plan something this weekend?")


@scenario("cs.chat.damp_high")
async def _(w):
    await w.profile(A_ID, dampening_base=3.5)
    await _msgtext(w, A_ID, "Can we plan something this weekend?")


@scenario("cs.chat.damp_mid")
async def _(w):
    await w.profile(A_ID, dampening_base=2.0)
    await _msgtext(w, A_ID, "Can we plan something this weekend?")


@scenario("cs.chat.damp_low")
async def _(w):
    await w.profile(A_ID, dampening_base=0.5)
    await _msgtext(w, A_ID, "Can we plan something this weekend?")


@scenario("cs.chat.damp_disabled", dampening_enabled=False)
async def _(w):
    await _msgtext(w, A_ID, "Can we plan something this weekend?")


@scenario("cs.chat.history_long")
async def _(w):
    for i in range(4):
        await w.msg(A_ID, "user", f"older user line {i}", 0.4 - i * 0.01)
        await w.msg(A_ID, "assistant", f"older assistant line {i}", 0.39 - i * 0.01)
    await _msgtext(w, A_ID, "Anything else we should plan?")


async def _seed_big(w: World) -> None:
    """Enough facts to trip the smart-context path (threshold 3)."""
    await w.fact(A_ID, "Alice enjoys weekend hiking trips in the hills", "private", 40)
    await w.fact(A_ID, "Alice booked a hiking trip permit", "private", 50)
    await w.fact(A_ID, "Alice reads novels", "private", 60)
    await w.fact(A_ID, "Alice recently started yoga", "private", 1)
    await w.fact(B_ID, "Bob suggested a hiking weekend", "shared", 45)
    await w.fact(B_ID, "Bob wants to repaint the hallway", "shared", 2)
    await w.fact(B_ID, "Bob is secretly planning a hiking surprise", "private", 3)
    from pairtner import db as db_mod

    await db_mod.add_fact_summary(w.db, A_ID, "2025-09", "September: mostly outdoor plans.", 4)
    await db_mod.add_fact_summary(w.db, A_ID, "2025-10", "October: work stress and reading.", 3)


@scenario("cs.chat.smart_ctx", search_fts_threshold=3)
async def _(w):
    await _seed_big(w)
    await _msgtext(w, A_ID, "planning a hiking trip soon")


@scenario("cs.keep.plain")
async def _(w):
    await _msgtext(w, A_ID, "#keep I really like tea in the evening")


@scenario("cs.keep.smart_ctx", search_fts_threshold=3)
async def _(w):
    await _seed_big(w)
    await _msgtext(w, A_ID, "#keep we should go hiking in spring")


@scenario("cs.deep", search_fts_threshold=3)
async def _(w):
    await _seed_big(w)
    await w.perception(B_ID, A_ID, "wants more shared plans", "neutral", 0.8, 1)
    await _msgtext(w, A_ID, "#deep help me think about hiking and work balance")


@scenario("cs.blunt.plain")
async def _(w):
    await _msgtext(w, A_ID, "#blunt am I being unfair about the dishes")


@scenario("cs.blunt.honest_smart_ctx", search_fts_threshold=3)
async def _(w):
    await _seed_big(w)
    await _msgtext(w, A_ID, "#honest should I cancel the hiking trip")


@scenario("cs.blunt.with_send", calls=2)
async def _(w):
    await _msgtext(w, A_ID, "#blunt #send you never help with the dishes")


@scenario("cs.send.ab", calls=2)
async def _(w):
    await w.profile(A_ID, dampening_base=2.0)
    await w.goal(A_ID, "Be calmer in arguments")
    await w.perception(B_ID, A_ID, "feels unheard about chores", "negative", 0.8, 2)
    await _msgtext(w, A_ID, "#send you left the dishes again")


@scenario("cs.send.no_ab", ab_rewrite_enabled=False)
async def _(w):
    await w.goal(A_ID, "Be calmer in arguments")
    await w.shared_goal("Save for a trip")
    await w.perception(B_ID, A_ID, "feels unheard about chores", "negative", 0.8, 2)
    await _msgtext(w, A_ID, "#send you left the dishes again")


@scenario("cs.send.smart_ctx", ab_rewrite_enabled=False, search_fts_threshold=3)
async def _(w):
    await _seed_big(w)
    await _msgtext(w, A_ID, "#send let's go hiking soon")


@scenario("cs.send.damp_high", ab_rewrite_enabled=False)
async def _(w):
    await w.profile(A_ID, dampening_base=3.5)
    await _msgtext(w, A_ID, "#send I am upset about yesterday")


@scenario("cs.tell.command")
async def _(w):
    await w.svc.handle_tell(A_ID, "please pick up milk on the way home")


# ── mediate ────────────────────────────────────────────────────────────────

async def _mediate_seed(w: World, a_side: bool = True, b_side: bool = True) -> None:
    await w.fact(A_ID, "We agreed to split the budget evenly", "shared", 2)
    await w.fact(B_ID, "We want a quieter month", "shared", 3)
    if a_side:
        await w.perception(A_ID, B_ID, "seems tired after late shifts", "negative", 0.8, 2)
        await w.perception(A_ID, B_ID, "goes quiet when plans change", "neutral", 0.7, 1)
    if b_side:
        await w.perception(B_ID, A_ID, "wants more help around the house", "negative", 0.9, 2)


@scenario("cs.mediate.both_7d")
async def _(w):
    await _mediate_seed(w)
    await w.svc.handle_mediate(A_ID, None)


@scenario("cs.mediate.month")
async def _(w):
    await _mediate_seed(w)
    await w.svc.handle_mediate(A_ID, "month")


@scenario("cs.mediate.one_day")
async def _(w):
    # Ages stay far from the 1-day boundary so wall-clock drift cannot flip rows.
    await w.fact(A_ID, "We agreed to split the budget evenly", "shared", 0.3)
    await w.fact(B_ID, "OLD shared fact outside the window", "shared", 3)
    await w.perception(A_ID, B_ID, "seems tired after late shifts", "negative", 0.8, 0.4)
    await w.perception(B_ID, A_ID, "wants more help around the house", "negative", 0.9, 0.35)
    await w.perception(B_ID, A_ID, "OLD impression outside the window", "neutral", 0.9, 4)
    await w.svc.handle_mediate(A_ID, "1d")


@scenario("cs.mediate.one_sided")
async def _(w):
    await _mediate_seed(w, b_side=False)
    await w.svc.handle_mediate(A_ID, "7d")


@scenario("cs.mediate.facts_only", mediate_include_perceptions=False)
async def _(w):
    await _mediate_seed(w)
    await w.svc.handle_mediate(A_ID, "2w")


@scenario("cs.mediate.perceptions_only")
async def _(w):
    await w.perception(A_ID, B_ID, "seems tired after late shifts", "negative", 0.8, 2)
    await w.perception(B_ID, A_ID, "wants more help around the house", "negative", 0.9, 2)
    await w.svc.handle_mediate(B_ID, "7d")


# ── export therapy, digest, check-in, nudges, summaries ───────────────────

async def _therapy_seed(w: World, *, events=True, patterns=True, facts=True,
                        goals=True, ratio=True) -> None:
    if events:
        await w.event("2025-11-02 10:00:00", "paired", "Alice and Bob paired up")
        await w.event("2025-11-20 18:30:00", "conflict", "Disagreement about chores")
        await w.event("2025-11-22 09:15:00", "resolution", "Agreed on a chore chart")
        await w.event("2025-12-01 20:00:00", "milestone", "First month together")
        await w.event("2025-12-07 18:00:00", "checkin", "Weekly check-in completed")
    if patterns:
        await w.pattern(A_ID, "tends to generalize", 5)
        await w.pattern(A_ID, "uses blame language", 3, "horsemen")
        await w.pattern(A_ID, "asks open questions", 2, "articulation")
        await w.pattern(B_ID, "BOB-PRIVATE-PATTERN-NEVER-SHOWN", 9)
    if facts:
        await w.fact(A_ID, "Alice feels overloaded at work", "private", 20)
        await w.fact(A_ID, "Alice values quiet mornings", "private", 19)
        await w.fact(A_ID, "We cook together on weekends", "shared", 18)
        await w.fact(B_ID, "We try date nights monthly", "shared", 17)
    if goals:
        await w.goal(A_ID, "Be calmer in arguments", 15)
        await w.shared_goal("Plan a holiday together", 14)
    if ratio:
        await w.interactions(8, 2)


@scenario("cs.therapy.full")
async def _(w):
    from pairtner.features.therapy_export import generate_therapy_report

    await _therapy_seed(w)
    await generate_therapy_report(w.db, w.llm, A_ID)


@scenario("cs.therapy.via_export")
async def _(w):
    await _therapy_seed(w)
    await w.svc.handle_export(A_ID, "therapy")


@scenario("cs.therapy.shared_only", seed=False)
async def _(w):
    from pairtner.features.therapy_export import generate_therapy_report

    await w.fact(A_ID, "We cook together on weekends", "shared", 18)
    await generate_therapy_report(w.db, w.llm, A_ID)


@scenario("cs.therapy.patterns_only", seed=False)
async def _(w):
    from pairtner.features.therapy_export import generate_therapy_report

    await _therapy_seed(w, events=False, facts=False, goals=False, ratio=False)
    await generate_therapy_report(w.db, w.llm, A_ID)


@scenario("cs.therapy.events_zero_negative", seed=False)
async def _(w):
    from pairtner.features.therapy_export import generate_therapy_report

    await _therapy_seed(w, patterns=False, facts=False, goals=False, ratio=False)
    await w.interactions(4, 0)
    await generate_therapy_report(w.db, w.llm, A_ID)


async def _digest(w: World, last_digest: str | None) -> None:
    from pairtner.features.digest import _send_digest_for_couple

    async def send(uid: int, text: str) -> None:
        return None

    await _send_digest_for_couple(
        w.db, w.llm, send, A_ID, B_ID, w.couple_id, last_digest)


@scenario("cs.digest.facts_and_relays", seed=False)
async def _(w):
    await w.fact(A_ID, "We booked tickets for the show", "shared", 0.2)
    await w.fact(B_ID, "We will try the new cafe", "shared", 0.1)
    await w.relay(A_ID, B_ID, "Thanks for dinner, I loved it", 0.15)
    await w.relay(B_ID, A_ID, "Original draft", 0.12, edited="Edited: see you at 7")
    await _digest(w, None)


@scenario("cs.digest.relays_only", seed=False)
async def _(w):
    await w.relay(A_ID, B_ID, "Please call me when you are free", 0.15)
    await _digest(w, None)


@scenario("cs.digest.facts_since_last", seed=False)
async def _(w):
    await w.fact(A_ID, "OLD shared fact before last digest", "shared", 5)
    await w.fact(B_ID, "NEW shared fact after last digest", "shared", 0.5)
    await _digest(w, (NOW - timedelta(days=2)).isoformat())


async def _checkin(w: World, *, goals: bool = False, horsemen: bool = False,
                   pos: int = 0, neg: int = 0, conflicts: bool = False) -> None:
    from pairtner import db as db_mod
    from pairtner.features.checkin import _generate_summary

    await w.fact(A_ID, "We love our Sunday walks", "shared", 4)
    if goals:
        await w.goal(A_ID, "Walk every morning", 6)
        await w.goal(B_ID, "Cook more at home", 5)
        await w.shared_goal("Save for a trip", 4)
    if horsemen:
        await w.pattern(B_ID, "dismissive tone", 2, "horsemen")
    if pos or neg:
        await w.interactions(pos, neg)
    if conflicts:
        await w.conflict("household chores", "resolved", 3)
        await w.conflict("holiday plans", "open", 2)
        await w.conflict("budget talk", "open", 1)
    cid = await db_mod.create_checkin(w.db, w.couple_id, "2025-12-07", A_ID, B_ID)
    await db_mod.update_checkin_answers(w.db, cid, "a", "A: it was a calm week")
    await db_mod.update_checkin_answers(w.db, cid, "b", "B: busy but good")
    checkin = await db_mod.get_active_checkin(w.db, w.couple_id)

    async def send(uid: int, text: str) -> None:
        return None

    await _generate_summary(w.db, w.llm, send, checkin, A_ID, B_ID)


@scenario("cs.checkin.no_trends", seed=False)
async def _(w):
    await _checkin(w)


@scenario("cs.checkin.strong_ratio", seed=False)
async def _(w):
    await _checkin(w, pos=10, neg=1)


@scenario("cs.checkin.strong_ratio_zero_neg", seed=False)
async def _(w):
    await _checkin(w, pos=6, neg=0)


@scenario("cs.checkin.tense_week", seed=False)
async def _(w):
    await _checkin(w, pos=1, neg=4)


@scenario("cs.checkin.mixed", seed=False)
async def _(w):
    await _checkin(w, pos=3, neg=2)


@scenario("cs.checkin.horsemen_goals", seed=False)
async def _(w):
    await _checkin(w, goals=True, horsemen=True, pos=3, neg=2)


@scenario("cs.checkin.conflicts_open_resolved", seed=False)
async def _(w):
    await _checkin(w, conflicts=True, pos=2, neg=3)


@scenario("cs.checkin.conflicts_all_resolved", seed=False)
async def _(w):
    await w.conflict("household chores", "resolved", 3)
    await _checkin(w)


async def _positive_facts(w: World) -> None:
    for i, (owner, text, cat) in enumerate([
        (A_ID, "Our first trip to the coast", "relationship"),
        (B_ID, "She laughed at my terrible joke", "emotional"),
        (A_ID, "We cooked pasta together", "relationship"),
        (B_ID, "He remembered our anniversary", "emotional"),
        (A_ID, "Walking in the rain was fun", "relationship"),
    ]):
        await w.fact(owner, text, "shared", 30 - i, category=cat)


@scenario("cs.nudge.positive_facts_en", seed=False)
async def _(w):
    from pairtner.features.nudges import generate_nudge

    await _positive_facts(w)
    random.seed(0)
    await generate_nudge(w.db, w.llm, A_ID, B_ID, lang="en")


@scenario("cs.nudge.positive_facts_te", seed=False)
async def _(w):
    from pairtner.features.nudges import generate_nudge

    await _positive_facts(w)
    random.seed(0)
    await generate_nudge(w.db, w.llm, B_ID, A_ID, lang="te")


@scenario("cs.appreciation.en", seed=False)
async def _(w):
    from pairtner.features.nudges import generate_appreciation_prompt

    await generate_appreciation_prompt(w.db, w.llm, A_ID, "Bob", lang="en")


@scenario("cs.appreciation.te", seed=False)
async def _(w):
    from pairtner.features.nudges import generate_appreciation_prompt

    await generate_appreciation_prompt(w.db, w.llm, B_ID, "Alice", lang="te")


@scenario("cs.monthly_summary.november", seed=False)
async def _(w):
    from pairtner.core.summarizer import generate_monthly_summary

    for when, text, vis, cat in [
        ("2025-10-31 23:00:00", "OUT-OF-RANGE october fact", "private", "other"),
        ("2025-11-02 09:00:00", "Alice started a new project", "private", "work"),
        ("2025-11-09 09:00:00", "We went hiking together", "shared", "relationship"),
        ("2025-11-15 09:00:00", "Alice felt tired a lot", "private", None),
        ("2025-11-28 09:00:00", "We painted the hallway", "shared", "household"),
        ("2025-12-01 00:00:00", "OUT-OF-RANGE december fact", "private", "other"),
    ]:
        await w.x(
            "INSERT INTO facts (owner_id, text, visibility, source, category, "
            "fact_type, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (A_ID, text, vis, "seed", cat, "stable", when, when),
        )
    await generate_monthly_summary(w.db, w.llm, A_ID, 2025, 11)


@scenario("cs.monthly_summary.december", seed=False)
async def _(w):
    from pairtner.core.summarizer import generate_monthly_summary

    await w.x(
        "INSERT INTO facts (owner_id, text, visibility, source, category, "
        "fact_type, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (A_ID, "We planned a new year trip", "shared", "seed", "relationship",
         "stable", "2025-12-30 09:00:00", "2025-12-30 09:00:00"),
    )
    await generate_monthly_summary(w.db, w.llm, A_ID, 2025, 12)


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

def _check(case_id: str, system: str, user: str) -> None:
    if not isinstance(system, str) or not system.strip():
        raise RuntimeError(f"golden case {case_id!r} produced an empty system string")
    if not isinstance(user, str) or not user.strip():
        raise RuntimeError(f"golden case {case_id!r} produced an empty user string")


async def collect() -> dict[str, dict[str, str]]:
    """Assemble every prompt and return ``{case_id: {"system", "user"}}``."""
    results: dict[str, dict[str, str]] = {}

    def put(case_id: str, system: str, user: str) -> None:
        _check(case_id, system, user)
        if case_id in results:
            raise RuntimeError(f"duplicate golden case id {case_id!r}")
        results[case_id] = {"system": system, "user": user}

    with _in_root():
        activate_default_prompts()
        for case_id, (system, user) in _direct().items():
            put(case_id, system, user)

        with tempfile.TemporaryDirectory() as td:
            for sc in SCENARIOS:
                world = await _make_world(Path(td), sc.name.replace(".", "_"), sc.cfg, sc.seed)
                await sc.fn(world)
                calls = world.llm.calls
                if len(calls) != sc.calls:
                    raise RuntimeError(
                        f"scenario {sc.name!r} made {len(calls)} LLM call(s), "
                        f"expected {sc.calls}")
                for i, call in enumerate(calls, 1):
                    case_id = sc.name if len(calls) == 1 else f"{sc.name}#{i}"
                    put(case_id, call["system_prompt"], call["user_content"])
    return results


def render(results: dict[str, dict[str, str]]) -> str:
    return json.dumps(results, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    results = asyncio.run(collect())
    text = render(results)
    if "--write" in argv:
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(text, encoding="utf-8")
        print(f"wrote {GOLDEN_PATH} ({len(results)} cases)")
    else:
        print(f"{len(results)} cases (use --write to save)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
