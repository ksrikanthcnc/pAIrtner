"""Tests for search v2: pagination, two-pass LLM rerank, /search, REST, composer."""

from __future__ import annotations

import aiosqlite
import pytest
import pytest_asyncio

from pairtner.config import Config
from pairtner.core.context_composer import ContextComposer
from pairtner.core.search_v2 import paginated_search, two_pass_search
from pairtner.db import create_user, init_db, set_partner
from pairtner.handlers import BotService
from pairtner.rest.api import Request, RestApi
from pairtner.storage.schema_v2 import make_relationship_id
from tests.helpers import MockLLMClient

ALICE, BOB = 1001, 1002
REL = make_relationship_id(ALICE, BOB)


class FailingLLM:
    async def generate(self, *a, **kw):
        raise RuntimeError("llm down")


@pytest_asyncio.fixture
async def db_path(tmp_path) -> str:
    path = str(tmp_path / "s.db")
    await init_db(path)
    await create_user(path, ALICE, "Alice")
    await create_user(path, BOB, "Bob")
    await set_partner(path, ALICE, BOB)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO relationships(id, user_a_id, user_b_id) VALUES (?,?,?)",
            (REL, ALICE, BOB),
        )
        await db.commit()
    return path


async def add(path, owner, text, visibility="private", kind="working", rel=REL) -> int:
    async with aiosqlite.connect(path) as db:
        cur = await db.execute(
            "INSERT INTO facts(owner_id, relationship_id, text, visibility, kind) "
            "VALUES (?,?,?,?,?)",
            (owner, rel, text, visibility, kind),
        )
        await db.commit()
        return cur.lastrowid


async def test_paginated_search_basic(db_path):
    for i in range(7):
        await add(db_path, ALICE, f"hiking trip number {i}")
    r1 = await paginated_search(db_path, ALICE, "hiking", REL, page=1, per_page=3)
    assert len(r1["results"]) == 3
    assert (r1["page"], r1["per_page"], r1["total"], r1["has_more"]) == (1, 3, 7, True)
    r3 = await paginated_search(db_path, ALICE, "hiking", REL, page=3, per_page=3)
    assert len(r3["results"]) == 1 and r3["has_more"] is False
    ids = {x["id"] for p in (1, 2, 3) for x in (await paginated_search(
        db_path, ALICE, "hiking", REL, page=p, per_page=3))["results"]}
    assert len(ids) == 7
    assert {"id", "text", "kind", "score"} <= set(r1["results"][0])


async def test_paginated_search_empty(db_path):
    await add(db_path, ALICE, "something else")
    r = await paginated_search(db_path, ALICE, "zebra", REL)
    assert r == {"results": [], "page": 1, "per_page": 10, "total": 0, "has_more": False}
    assert (await paginated_search(db_path, ALICE, "!!!", REL))["total"] == 0


async def test_paginated_search_kind_filter(db_path):
    await add(db_path, ALICE, "loves hiking", kind="core")
    await add(db_path, ALICE, "went hiking today", kind="working")
    core = await paginated_search(db_path, ALICE, "hiking", REL, kind_filter="core")
    assert [x["kind"] for x in core["results"]] == ["core"]
    working = await paginated_search(db_path, ALICE, "hiking", REL, kind_filter="working")
    assert [x["kind"] for x in working["results"]] == ["working"]
    assert (await paginated_search(db_path, ALICE, "hiking", REL))["total"] == 2
    with pytest.raises(ValueError):
        await paginated_search(db_path, ALICE, "hiking", REL, kind_filter="bogus")


async def test_two_pass_with_mock_llm(db_path):
    a = await add(db_path, ALICE, "dinner at the italian place")
    b = await add(db_path, ALICE, "dinner with parents on sunday")
    c = await add(db_path, ALICE, "dinner budget is tight")
    llm = MockLLMClient([{"reply": f"[{c}, {a}]"}])
    out = await two_pass_search(db_path, llm, ALICE, "dinner", REL, top_k=2)
    assert [x["id"] for x in out] == [c, a]
    assert all(x["relevance_reason"] == "llm_rerank" for x in out)
    assert b not in {x["id"] for x in out}
    call = llm.calls[0]
    assert "relevance ranker" in call["system_prompt"]
    assert call["user_content"].startswith("Query: dinner\n\nFacts:\n")
    assert f"[{a}] dinner at the italian place" in call["user_content"]


async def test_two_pass_ignores_unknown_ids_and_fenced_json(db_path):
    a = await add(db_path, ALICE, "dinner at home")
    await add(db_path, ALICE, "dinner out")
    llm = MockLLMClient([{"reply": f"```json\n[999, {a}, {a}]\n```"}])
    out = await two_pass_search(db_path, llm, ALICE, "dinner", REL)
    assert [x["id"] for x in out] == [a]


@pytest.mark.parametrize("llm", [FailingLLM(), MockLLMClient([{"reply": "no idea"}])])
async def test_two_pass_llm_fallback(db_path, llm):
    ids = [await add(db_path, ALICE, f"dinner plan {i}") for i in range(4)]
    expected = [x["id"] for x in (await paginated_search(
        db_path, ALICE, "dinner", REL))["results"]]
    out = await two_pass_search(db_path, llm, ALICE, "dinner", REL, top_k=3)
    assert [x["id"] for x in out] == expected[:3]
    assert set(x["id"] for x in out) <= set(ids)
    assert all(x["relevance_reason"] == "fts_fallback" for x in out)


@pytest_asyncio.fixture
async def service(db_path) -> BotService:
    return BotService(Config(telegram_token="t", gemini_api_key="k", db_path=db_path),
                      db_path, MockLLMClient())


async def test_search_command_pagination(db_path, service):
    for i in range(12):
        await add(db_path, ALICE, f"concert memory {i}")
    r = await service.handle_search(ALICE, "concert")
    text = r[0].text
    assert "12 found" in text
    assert text.count("#") == 5
    assert "Page 1 of 3 -- /search concert page 2" in text
    assert "private" in text and "working" in text
    r3 = await service.handle_search(ALICE, "concert page 3")
    assert "Page 3 of 3" in r3[0].text and "page 4" not in r3[0].text
    assert r3[0].text.count("#") == 2


async def test_search_relationship_scoped(db_path, service):
    mine = await add(db_path, ALICE, "picnic idea mine")
    shared = await add(db_path, BOB, "picnic idea shared by bob", "shared")
    secret = await add(db_path, BOB, "picnic idea bob secret", "private")
    other = await add(db_path, ALICE, "picnic idea other relationship", rel=None)
    r = await paginated_search(db_path, ALICE, "picnic", REL)
    ids = {x["id"] for x in r["results"]}
    assert ids == {mine, shared}
    assert secret not in ids and other not in ids
    with pytest.raises(PermissionError):
        await paginated_search(db_path, 555, "picnic", REL)
    text = (await service.handle_search(ALICE, "picnic"))[0].text
    assert f"#{mine}" in text and f"#{shared}" in text and f"#{secret}" not in text


async def test_composer_no_longer_uses_fts_or_two_pass(db_path):
    """FTS5 and two-pass were removed from the context composer hot path.
    The composer only returns core + working + partner + nudges + summaries."""
    for i in range(5):
        await add(db_path, ALICE, f"dinner note {i}")
    ctx = await ContextComposer(db_path).build_context(ALICE, REL, "dinner")
    assert ctx.working_facts
    assert isinstance(ctx.working_facts, list)
    assert not hasattr(ctx, "fts_matches")


async def _rest(db_path, monkeypatch, admin=False):
    monkeypatch.setenv("REST_INVITE_CODE", "x")
    llm = MockLLMClient()
    cfg = Config(telegram_token="", db_path=db_path, rest_registration="open",
                 cooldown_minutes=0, review_before_send=False)
    api = RestApi(BotService(cfg, db_path, llm), cfg)

    async def call(method, target, body=None, token=None):
        import json
        h = {"content-type": "application/json"}
        if token:
            h["authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode() if body is not None else b""
        r = await api.handle(Request(method, target, h, data, "10.0.0.1"))
        return r.status, json.loads(r.body)

    st, reg = await call("POST", "/api/v1/register", {"name": "Zed"})
    assert st == 201
    uid = reg["user_id"]
    if admin:
        object.__setattr__(cfg, "admin_user_ids", frozenset({uid}))
    return call, uid, reg["token"] if "token" in reg else f"{uid}:{reg['secret']}", llm


async def test_rest_search_endpoints(db_path, monkeypatch):
    call, uid, token, llm = await _rest(db_path, monkeypatch)
    token = token if ":" in token else f"{uid}:{token}"
    for i in range(3):
        await add(db_path, uid, f"kayak weekend {i}", rel=None, kind="working")
    await add(db_path, uid, "kayak core", rel=None, kind="core")
    st, body = await call("GET", "/api/search?q=kayak&page=1&per_page=2", token=token)
    assert st == 200 and body["total"] == 4 and body["has_more"] and len(body["results"]) == 2
    st, body = await call("GET", "/api/search?q=kayak&kind=core", token=token)
    assert body["total"] == 1
    assert (await call("GET", "/api/search?q=kayak&kind=zzz", token=token))[0] == 400
    assert (await call("GET", "/api/search?q=kayak&user_id=1001", token=token))[0] == 403
    assert (await call("GET", "/api/search?q=kayak"))[0] == 401
    # rerank is admin-only
    st, _ = await call("POST", "/api/search/rerank", {"query": "kayak"}, token=token)
    assert st == 403


async def test_rest_rerank_admin(db_path, monkeypatch):
    call, uid, token, llm = await _rest(db_path, monkeypatch, admin=True)
    a = await add(db_path, uid, "kayak alpha", rel=None)
    await add(db_path, uid, "kayak beta", rel=None)
    llm.responses.append({"reply": f"[{a}]"})
    st, body = await call("POST", "/api/search/rerank", {"query": "kayak"}, token=token)
    assert st == 200 and [r["id"] for r in body["results"]] == [a]
