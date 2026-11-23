"""Tests for rolling pre-computed summaries."""
from __future__ import annotations

import json
import sqlite3

import pytest

from pairtner.config import Config
from pairtner.core.context_composer import ContextComposer
from pairtner.core.summary import (
    compute_rolling_summaries,
    run_rolling_summaries,
)
from pairtner.storage.migrate_v2 import migrate_to_v2
from pairtner.storage.schema_v2 import SCHEMA_V2_SQL, make_relationship_id
from tests.helpers import MockLLMClient

ALICE, BOB = 2001, 2002
REL = make_relationship_id(ALICE, BOB)
LEVELS = ["daily", "weekly"]


@pytest.fixture
def db_path(tmp_path) -> str:
    path = str(tmp_path / "r.db")
    con = sqlite3.connect(path)
    con.executescript(SCHEMA_V2_SQL)
    con.execute("ALTER TABLE facts ADD COLUMN tag TEXT")  # v1 legacy column, present in prod
    con.execute("INSERT INTO users (tg_id, name) VALUES (?, 'Alice')", (ALICE,))
    con.execute("INSERT INTO users (tg_id, name) VALUES (?, 'Bob')", (BOB,))
    con.execute("INSERT INTO relationships (id, user_a_id, user_b_id) VALUES (?,?,?)",
                (REL, ALICE, BOB))
    con.commit()
    con.close()
    return path


def sql(db_path, q, params=()):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in con.execute(q, params).fetchall()]
        con.commit()
    finally:
        con.close()
    return rows


def fact(db_path, text, owner=ALICE, vis="private", tag="work", kind="working", meta=None,
         rel=REL, archived=None):
    sql(db_path,
        "INSERT INTO facts (owner_id, relationship_id, text, visibility, kind, tag, metadata,"
        " archived_at, created_at) VALUES (?,?,?,?,?,?,?,?, '2026-11-18T10:00:00')",
        (owner, rel, text, vis, kind, tag, json.dumps(meta) if meta else None, archived))


def resp(narr: str) -> dict:
    return {"reply": json.dumps({"resolved": [], "ongoing": [narr], "new": [],
                                 "narrative": narr})}


def rolling(db_path, **where):
    rows = sql(db_path, "SELECT * FROM facts WHERE kind='rolling' ORDER BY id")
    for r in rows:
        r["meta"] = json.loads(r["metadata"])
    return [r for r in rows if all(r["meta"].get(k) == v or r.get(k) == v
                                   for k, v in where.items())]


@pytest.mark.asyncio
async def test_first_level_compresses_working_facts(db_path):
    fact(db_path, "promotion news", tag="work")
    fact(db_path, "chore dispute", tag="home")
    llm = MockLLMClient([resp("Promotion."), resp("Chores.")])
    out = await compute_rolling_summaries(db_path, llm, ALICE, REL, intervals=LEVELS)
    assert list(out) == ["private/daily"]
    (row,) = rolling(db_path)
    assert row["owner_id"] == ALICE and row["visibility"] == "private"
    assert row["meta"]["period_type"] == "rolling_daily"
    assert row["meta"]["tag_counts"] == {"work": 1, "home": 1}
    assert row["text"].splitlines() == ["- [work] Promotion.", "- [home] Chores."]
    # sources stay unarchived: rolling is a view, not a consumer
    assert sql(db_path, "SELECT COUNT(*) n FROM facts WHERE kind='working' "
                        "AND archived_at IS NULL")[0]["n"] == 2


@pytest.mark.asyncio
async def test_level_n_consumes_level_n_minus_1(db_path):
    fact(db_path, "- [kids] pickup conflict Nov 8\n- [home] dishes", kind="daily",
         meta={"period": "2026-11-08", "start": "2026-11-08"})
    llm = MockLLMClient([resp("Weekly kids."), resp("Weekly home.")])
    out = await compute_rolling_summaries(db_path, llm, ALICE, REL, intervals=LEVELS)
    assert list(out) == ["private/weekly"]
    assert len(llm.calls) == 2  # one call per tag group
    assert "pickup conflict Nov 8" in llm.calls[0]["user_content"]
    (row,) = rolling(db_path)
    assert row["meta"]["tag_counts"] == {"kids": 1, "home": 1}
    assert json.loads(row["source_ids"]) != []


@pytest.mark.asyncio
async def test_archived_sources_excluded(db_path):
    fact(db_path, "old", archived="2026-11-19T00:00:00")
    llm = MockLLMClient()
    assert await compute_rolling_summaries(db_path, llm, ALICE, REL, intervals=LEVELS) == {}
    assert llm.calls == []


@pytest.mark.asyncio
async def test_rerun_replaces_in_place_and_skips_unchanged(db_path):
    fact(db_path, "a")
    llm = MockLLMClient([resp("One.")])
    await compute_rolling_summaries(db_path, llm, ALICE, REL, intervals=LEVELS)
    assert await compute_rolling_summaries(db_path, llm, ALICE, REL, intervals=LEVELS) == {}
    assert len(llm.calls) == 1  # unchanged sources: no LLM call
    fact(db_path, "b")
    llm.responses.append(resp("Two."))
    await compute_rolling_summaries(db_path, llm, ALICE, REL, intervals=LEVELS)
    (row,) = rolling(db_path)  # replaced, not appended
    assert "Two." in row["text"]


@pytest.mark.asyncio
async def test_stale_rolling_removed_when_sources_gone(db_path):
    fact(db_path, "a")
    llm = MockLLMClient([resp("One.")])
    await compute_rolling_summaries(db_path, llm, ALICE, REL, intervals=LEVELS)
    sql(db_path, "UPDATE facts SET archived_at='2026-11-20' WHERE kind='working'")
    await compute_rolling_summaries(db_path, llm, ALICE, REL, intervals=LEVELS)
    assert rolling(db_path) == []


@pytest.mark.asyncio
async def test_llm_failure_keeps_previous_rolling(db_path):
    fact(db_path, "a")
    llm = MockLLMClient([resp("One.")])
    await compute_rolling_summaries(db_path, llm, ALICE, REL, intervals=LEVELS)
    fact(db_path, "b")
    llm.responses.append({"reply": "garbage"})
    assert await compute_rolling_summaries(db_path, llm, ALICE, REL, intervals=LEVELS) == {}
    (row,) = rolling(db_path)
    assert "One." in row["text"]


@pytest.mark.asyncio
async def test_three_stream_isolation(db_path):
    fact(db_path, "alice secret", vis="private", tag="secret")
    fact(db_path, "alice open", vis="shared", tag="open")
    fact(db_path, "bob secret", owner=BOB, vis="private", tag="bobsecret")
    llm = MockLLMClient([resp("Priv."), resp("Shared.")])
    out = await compute_rolling_summaries(db_path, llm, ALICE, REL, intervals=LEVELS)
    assert set(out) == {"private/daily", "shared/daily"}
    by_stream = {r["meta"]["stream"]: r for r in rolling(db_path)}
    assert by_stream["private"]["visibility"] == "private"
    assert by_stream["shared"]["visibility"] == "shared"
    sent = [(c["system_prompt"], c["user_content"]) for c in llm.calls]
    assert "alice secret" in sent[0][1] and "alice open" not in sent[0][1]
    assert "alice open" in sent[1][1] and "alice secret" not in sent[1][1]
    assert all("bob secret" not in u for _, u in sent)
    assert all(r["owner_id"] == ALICE for r in rolling(db_path))


@pytest.mark.asyncio
async def test_couple_stream_from_couple_summaries_only(db_path):
    fact(db_path, "- [home] joint budget talk", owner=None, vis="private", kind="daily",
         meta={"period": "2026-11-18", "start": "2026-11-18", "end": "2026-11-19"})
    fact(db_path, "- [secret] alice private daily", kind="daily", vis="private",
         meta={"period": "2026-11-18", "start": "2026-11-18"})
    llm = MockLLMClient([resp("Priv."), resp("Couple.")])
    out = await compute_rolling_summaries(db_path, llm, ALICE, REL, intervals=["daily"])
    assert "couple/daily" in out
    couple = [r for r in rolling(db_path) if r["meta"]["stream"] == "couple"][0]
    assert couple["owner_id"] is None and couple["relationship_id"] == REL
    couple_calls = [c for c in llm.calls if "joint budget" in c["user_content"]]
    assert couple_calls and all("alice private" not in c["user_content"] for c in couple_calls)


@pytest.mark.asyncio
async def test_run_hook_wraps_compute(db_path):
    fact(db_path, "a")
    out = await run_rolling_summaries(db_path, MockLLMClient([resp("X.")]), ALICE, REL,
                                      intervals=LEVELS)
    assert "private/daily" in out


# --- prompt composition -----------------------------------------------------

def add_rolling(db_path, level, text, owner=ALICE, vis="private", stream="private"):
    sql(db_path,
        "INSERT INTO facts (owner_id, relationship_id, text, visibility, kind, metadata) "
        "VALUES (?,?,?,?, 'rolling', ?)",
        (owner, REL, text, vis, json.dumps({"rolling": True, "level": level,
                                            "stream": stream})))


@pytest.mark.asyncio
async def test_prompt_prefers_rolling_over_regular(db_path):
    fact(db_path, "regular daily text", kind="daily", meta={"period": "2026-11-18"})
    for lv in ("daily", "weekly", "monthly", "quarterly", "yearly"):
        add_rolling(db_path, lv, f"- [x] roll {lv}")
    ctx = await ContextComposer(db_path).build_context(ALICE, REL)
    assert len(ctx.summaries) == 5
    assert ctx.summaries[0].startswith("(rolling daily)")
    assert ctx.summaries[-1].startswith("(rolling yearly)")
    assert not any("regular daily text" in s for s in ctx.summaries)


@pytest.mark.asyncio
async def test_prompt_falls_back_without_rolling(db_path):
    fact(db_path, "regular daily text", kind="daily", meta={"period": "2026-11-18"})
    ctx = await ContextComposer(db_path).build_context(ALICE, REL)
    assert ctx.summaries == ["(daily) regular daily text"]


@pytest.mark.asyncio
async def test_prompt_rolling_disabled_uses_regular(db_path):
    fact(db_path, "regular daily text", kind="daily", meta={"period": "2026-11-18"})
    add_rolling(db_path, "daily", "- [x] roll")
    ctx = await ContextComposer(db_path, rolling_summary_enabled=False).build_context(
        ALICE, REL)
    assert ctx.summaries == ["(daily) regular daily text"]


@pytest.mark.asyncio
async def test_prompt_never_shows_partner_rolling(db_path):
    add_rolling(db_path, "daily", "- [x] alice private roll")
    add_rolling(db_path, "daily", "- [x] bob private roll", owner=BOB)
    add_rolling(db_path, "daily", "- [x] bob shared roll", owner=BOB, vis="shared",
                stream="shared")
    ctx = await ContextComposer(db_path).build_context(ALICE, REL)
    joined = "\n".join(ctx.summaries)
    assert "alice private roll" in joined
    assert "bob" not in joined


@pytest.mark.asyncio
async def test_prompt_merges_streams_per_level(db_path):
    add_rolling(db_path, "daily", "- [a] priv")
    add_rolling(db_path, "daily", "- [b] shared", vis="shared", stream="shared")
    add_rolling(db_path, "daily", "- [c] couple", owner=None, stream="couple")
    ctx = await ContextComposer(db_path).build_context(ALICE, REL)
    assert len(ctx.summaries) == 1
    assert all(t in ctx.summaries[0] for t in ("priv", "shared", "couple"))


# --- config + schema --------------------------------------------------------

def test_config_default_enabled():
    assert Config.__dataclass_fields__["rolling_summary_enabled"].default is True


@pytest.mark.asyncio
async def test_migration_widens_old_kind_check(tmp_path):
    path = str(tmp_path / "old.db")
    from pairtner.storage.schema import SCHEMA_SQL

    mem = sqlite3.connect(":memory:")
    mem.executescript(SCHEMA_V2_SQL)
    ddl = mem.execute("SELECT sql FROM sqlite_master WHERE name='facts'").fetchone()[0]
    mem.close()
    old = ddl.replace(",\n                                   'rolling'", "")
    assert old != ddl
    con = sqlite3.connect(path)
    con.executescript(SCHEMA_SQL)
    con.execute("ALTER TABLE facts RENAME TO facts_v1_unused")
    con.execute(old)
    con.commit()
    con.close()
    with pytest.raises(sqlite3.IntegrityError):
        sql(path, "INSERT INTO facts (text, kind) VALUES ('x','rolling')")
    await migrate_to_v2(path)
    sql(path, "INSERT INTO facts (text, kind) VALUES ('x','rolling')")
    assert sql(path, "SELECT COUNT(*) n FROM facts WHERE kind='rolling'")[0]["n"] == 1
