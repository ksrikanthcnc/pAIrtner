"""search_memory exposed to the LLM as a callable tool in the v2 chat path."""

from __future__ import annotations

import json

import aiosqlite
import pytest_asyncio

from pairtner.core.llm_tools import execute_search_memory
from pairtner.db import ensure_user, init_db, set_partner
from pairtner.storage.migrate_v2 import migrate_to_v2
from tests.helpers import MockLLMClient
from tests.test_handler_v2_wiring import ALICE, BOB, REL, _add, _service


def _tool_call(query: str) -> dict:
    return {
        "reply": "",
        "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "search_memory",
                         "arguments": json.dumps({"query": query})},
        }],
    }


@pytest_asyncio.fixture
async def db(tmp_path) -> str:
    path = str(tmp_path / "t.db")
    await init_db(path)
    for uid, name in ((ALICE, "Alice"), (BOB, "Bob")):
        await ensure_user(path, uid, name)
    await set_partner(path, ALICE, BOB)
    await migrate_to_v2(path)
    return path


async def test_search_memory_tool_call_flow(db):
    await _add(db, BOB, "Bob proposed at the lighthouse in Maine", "private")
    llm = MockLLMClient([_tool_call("lighthouse"), {"reply": "The lighthouse!"}])
    svc = _service(db, llm)
    responses = await svc._handle_plain(BOB, "where did I propose?")
    assert len(llm.calls) == 2
    assert llm.calls[0]["tools"] and llm.calls[0]["tools"][0]["function"]["name"] == "search_memory"
    assert "proposed at the lighthouse in Maine" in llm.calls[1]["user_content"]
    assert "The lighthouse!" in responses[0].text


async def test_tool_call_max_rounds(db):
    llm = MockLLMClient([_tool_call("anything") for _ in range(10)])
    svc = _service(db, llm)
    await svc._handle_plain(BOB, "tell me about the past")
    # 2 tool rounds + 1 final call with tools withheld
    assert len(llm.calls) == 3
    assert llm.calls[2]["tools"] is None


async def test_no_tool_support(db):
    llm = MockLLMClient([{"reply": "plain answer"}])
    svc = _service(db, llm)
    responses = await svc._handle_plain(BOB, "hello")
    assert len(llm.calls) == 1
    assert "plain answer" in responses[0].text


async def test_client_without_tools_param_still_works(db):
    class Legacy:
        calls = 0

        async def generate(self, user_content, system_prompt, model=None, api_key_override=None):
            Legacy.calls += 1
            return {"reply": "legacy ok"}

    svc = _service(db, Legacy())
    responses = await svc._handle_plain(BOB, "hello")
    assert Legacy.calls == 1
    assert "legacy ok" in responses[0].text


async def test_search_memory_respects_privacy(db):
    await _add(db, ALICE, "ALICE_PRIVATE_ZEBRA secret", "private")
    await _add(db, ALICE, "Alice loves zebra stripes", "nudge_eligible")
    results = await execute_search_memory(db, BOB, "zebra", REL)
    texts = " ".join(r["text"] for r in results)
    assert "ALICE_PRIVATE_ZEBRA" not in texts
    assert "Alice loves zebra stripes" in texts

    # and end-to-end: the tool result fed back to the LLM has no private fact
    llm = MockLLMClient([_tool_call("zebra"), {"reply": "ok"}])
    svc = _service(db, llm)
    await svc._handle_plain(BOB, "what about zebra?")
    assert "ALICE_PRIVATE_ZEBRA" not in llm.calls[1]["user_content"]
