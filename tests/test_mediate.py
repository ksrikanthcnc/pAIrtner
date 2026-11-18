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


@pytest.mark.parametrize("who", ["a", "b"])
async def test_prompt_privacy_both_requesters(service, mock_llm, test_db, paired_users, who):
    a, b = paired_users
    await _seed(test_db, a, b)
    requester = a if who == "a" else b
    resp = await service.handle_mediate(requester)
    assert resp and resp[0].text
    content = mock_llm.calls[-1]["user_content"]
    system = mock_llm.calls[-1]["system_prompt"]
    for secret in (RAW_A, RAW_B, PRIV_A, PRIV_B,
                   "Alice feels stressed about work", "Bob is anxious about finances"):
        assert secret not in content and secret not in system
    assert SHARED_A in content and SHARED_B in content
    assert "seems to feel unheard" in content
    assert "wants more date nights" in content
    assert "unverified" in content
    assert "last 7 days" in content


async def test_window_scoping_facts_and_perceptions(service, mock_llm, test_db, paired_users):
    a, b = paired_users
    old_id = await add_fact(test_db, a, "OLDSHARED-1", "shared")
    await _set_ts(test_db, "facts", old_id, "created_at", "2020-01-01 00:00:00")
    # Shared long ago, shared again today: appears via shared_at.
    reshared = await add_fact(test_db, b, "RESHARED-2", "shared")
    await _set_ts(test_db, "facts", reshared, "created_at", "2020-01-01 00:00:00")
    await _set_ts(test_db, "facts", reshared, "shared_at", "2999-01-01 00:00:00")
    pid = await add_partner_perception(
        test_db, user_id=a, about_id=b, theme="OLDTHEME-3", confidence=0.9,
    )
    await _set_ts(test_db, "partner_perceptions", pid, "updated_at", "2020-01-01 00:00:00")
    await add_partner_perception(
        test_db, user_id=a, about_id=b, theme="NEWTHEME-4", confidence=0.9,
    )
    await service.handle_mediate(a)
    content = mock_llm.calls[-1]["user_content"]
    assert "OLDSHARED-1" not in content
    assert "OLDTHEME-3" not in content
    assert "RESHARED-2" in content
    assert "NEWTHEME-4" in content
    # Even the maximum window (90 days) excludes rows from 2020.
    await service.handle_mediate(a, "90")
    assert "OLDSHARED-1" not in mock_llm.calls[-1]["user_content"]


async def test_month_and_clamp_labels(service, mock_llm, test_db, paired_users):
    a, b = paired_users
    await add_fact(test_db, a, SHARED_A, "shared")
    await service.handle_mediate(a, "month")
    assert "last 30 days" in mock_llm.calls[-1]["user_content"]
    await service.handle_mediate(a, "9999")
    assert "last 90 days" in mock_llm.calls[-1]["user_content"]


async def test_bad_arg_returns_usage_no_llm(service, mock_llm, test_db, paired_users):
    a, _ = paired_users
    resp = await service.handle_mediate(a, "banana")
    assert "/mediate" in resp[0].text
    assert mock_llm.calls == []


async def test_empty_input_no_llm_call(service, mock_llm, test_db, paired_users):
    a, b = paired_users
    # Fixture has one shared fact (Alice's); archive it by moving it out of window.
    facts = await _all_shared(test_db)
    for f in facts:
        await _set_ts(test_db, "facts", f, "created_at", "2020-01-01 00:00:00")
    resp = await service.handle_mediate(a)
    assert "last 7 days" in resp[0].text
    assert mock_llm.calls == []


async def _all_shared(db_path):
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT id FROM facts WHERE visibility='shared'") as cur:
            return [r[0] for r in await cur.fetchall()]


async def test_selfstatement_perception_excluded_but_stored(service, mock_llm, test_db, paired_users):
    a, b = paired_users
    await add_fact(test_db, a, SHARED_A, "shared")
    # Weak model recorded Alice's own words as a theme about Bob.
    await add_partner_perception(
        test_db, user_id=a, about_id=b, theme="I feel ignored", confidence=0.9,
    )
    await add_fact(test_db, a, "feels unheard at dinner every evening", "private")
    await add_partner_perception(
        test_db, user_id=a, about_id=b, theme="feels unheard at dinner every evening",
        confidence=0.9,
    )
    await service.handle_mediate(a)
    content = mock_llm.calls[-1]["user_content"]
    assert "I feel ignored" not in content
    assert "unheard at dinner" not in content
    stored = [p["theme"] for p in await get_partner_perceptions(test_db, b)]
    assert "I feel ignored" in stored
    assert "feels unheard at dinner every evening" in stored


async def test_include_perceptions_false(test_db, paired_users):
    a, b = paired_users
    cfg = Config(
        telegram_token="t", gemini_api_key="k", model_default="d",
        model_heavy=["h"], db_path=test_db, mediate_include_perceptions=False,
    )
    llm = MockLLMClient()
    svc = BotService(config=cfg, db_path=test_db, llm=llm)
    await add_fact(test_db, a, SHARED_A, "shared")
    await add_partner_perception(
        test_db, user_id=a, about_id=b, theme="PERCEPTION-OFF-1", confidence=0.9,
    )
    await svc.handle_mediate(a)
    content = llm.calls[-1]["user_content"]
    assert SHARED_A in content
    assert "PERCEPTION-OFF-1" not in content
    assert "unverified" not in content


async def test_one_sided_caveat(service, mock_llm, test_db, paired_users):
    a, b = paired_users
    await add_partner_perception(
        test_db, user_id=a, about_id=b, theme="seems to feel unheard", confidence=0.9,
    )
    await service.handle_mediate(a)
    assert "one-sided" in mock_llm.calls[-1]["user_content"]


async def test_no_partner_and_blocked(service, test_db):
    from pairtner.db import create_user
    await create_user(test_db, 5555, "Solo")
    resp = await service.handle_mediate(5555)
    assert resp and "partner" in resp[0].text.lower()


async def test_dispatch_passes_arg(service, mock_llm, test_db, paired_users):
    a, _ = paired_users
    resp = await service.dispatch(a, "Alice", "/mediate nonsense")
    assert "/mediate" in resp[0].text
    assert mock_llm.calls == []


def test_config_mediate_defaults():
    cfg = Config(telegram_token="t")
    assert (cfg.mediate_default_days, cfg.mediate_max_days) == (7, 90)
    assert cfg.mediate_include_perceptions is True
    assert cfg.mediate_min_confidence == 0.4
    assert cfg.mediate_max_themes_per_side == 8
    assert replace(cfg, mediate_max_days=10).mediate_max_days == 10
