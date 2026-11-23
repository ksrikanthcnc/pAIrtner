"""Tests for /mediate: window parsing, filtering, time scoping, privacy."""
from __future__ import annotations

from dataclasses import replace

import aiosqlite
import pytest

from pairtner.config import Config
from pairtner.db import (
    add_fact,
    add_partner_perception,
    get_partner_perceptions,
    log_message,
)
from pairtner.handlers import BotService
from pairtner.core.mediate import filter_perceptions, is_first_person, parse_window
from tests.helpers import MockLLMClient

RAW_A = "RAWSENTINEL-ALICE-MESSAGE-7731"
RAW_B = "RAWSENTINEL-BOB-MESSAGE-5519"
PRIV_A = "PRIVSENTINEL-ALICE-FACT-1182"
PRIV_B = "PRIVSENTINEL-BOB-FACT-9024"
SHARED_A = "SHAREDSENTINEL-ALICE-4410"
SHARED_B = "SHAREDSENTINEL-BOB-6633"


# ── parse_window ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("arg,days", [
    (None, 7), ("", 7), ("week", 7), ("month", 30), ("30d", 30),
    ("2w", 14), ("5", 5), (" 14 ", 14), ("MONTH", 30),
])
def test_parse_window_valid(arg, days):
    assert parse_window(arg, 7, 90)[0] == days


def test_parse_window_clamps_to_max():
    days, label = parse_window("365", 7, 90)
    assert days == 90
    assert "90" in label
    assert parse_window("20w", 7, 90)[0] == 90


@pytest.mark.parametrize("arg", ["banana", "0", "-3", "3x", "1.5", "0w"])
def test_parse_window_bad(arg):
    assert parse_window(arg, 7, 90) is None


# ── filter_perceptions ────────────────────────────────────────────────────

def _row(theme, conf=0.8):
    return {"theme": theme, "confidence": conf}


def test_first_person_dropped_en_and_te():
    assert is_first_person("I feel ignored")
    assert is_first_person("she says my work is ignored")
    assert is_first_person("I'm tired all the time")
    assert is_first_person("నాకు ఒంటరిగా అనిపిస్తోంది")
    assert not is_first_person("seems to feel unheard")
    out = filter_perceptions(
        [_row("I feel ignored"), _row("నేను అలసిపోయాను"), _row("seems unheard")],
        [], 0.4, 8,
    )
    assert out == ["seems unheard"]


def test_own_fact_duplicate_dropped():
    own = ["feels ignored when working late"]
    out = filter_perceptions(
        [_row("feels ignored when working late"),
         _row("Feels ignored when working late!"),
         _row("enjoys quiet mornings together")],
        own, 0.4, 8,
    )
    assert out == ["enjoys quiet mornings together"]


def test_containment_with_four_words_dropped():
    own = ["often worries about money and the future"]
    out = filter_perceptions([_row("worries about money and the")], own, 0.4, 8)
    assert out == []


def test_legit_theme_kept_low_confidence_dropped():
    out = filter_perceptions(
        [_row("wants more date nights", 0.9), _row("maybe tired", 0.1)],
        ["likes hiking"], 0.4, 8,
    )
    assert out == ["wants more date nights"]


def test_dedupe_cap_truncate():
    rows = [_row("wants more date nights"), _row("Wants MORE date nights.")]
    rows += [_row(f"theme number {i} unique") for i in range(10)]
    out = filter_perceptions(rows, [], 0.4, 3)
    assert len(out) == 3
    assert out[0] == "wants more date nights"
    long = filter_perceptions([_row("x" * 300)], [], 0.4, 8)
    assert len(long[0]) == 160


# ── handle_mediate ────────────────────────────────────────────────────────

async def _set_ts(db_path, table, row_id, column, value):
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            f"UPDATE {table} SET {column} = ? WHERE id = ?", (value, row_id)
        )
        await db.commit()


async def _seed(test_db, a, b):
    await log_message(test_db, a, "user", RAW_A)
    await log_message(test_db, b, "user", RAW_B)
    await add_fact(test_db, a, PRIV_A, "private")
    await add_fact(test_db, b, PRIV_B, "private")
    await add_fact(test_db, a, SHARED_A, "shared")
    await add_fact(test_db, b, SHARED_B, "shared")
    await add_partner_perception(
        test_db, user_id=a, about_id=b, theme="seems to feel unheard", confidence=0.8,
    )
    await add_partner_perception(
        test_db, user_id=b, about_id=a, theme="wants more date nights", confidence=0.8,
    )












async def _all_shared(db_path):
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT id FROM facts WHERE visibility='shared'") as cur:
            return [r[0] for r in await cur.fetchall()]












def test_config_mediate_defaults():
    cfg = Config(telegram_token="t")
    assert (cfg.mediate_default_days, cfg.mediate_max_days) == (7, 90)
    assert cfg.mediate_include_perceptions is True
    assert cfg.mediate_min_confidence == 0.4
    assert cfg.mediate_max_themes_per_side == 8
    assert replace(cfg, mediate_max_days=10).mediate_max_days == 10
