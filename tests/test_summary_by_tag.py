"""Tests for tag-grouped summarisation."""
from __future__ import annotations

import json

import pytest

from pairtner.core.summary import _summarise_by_tag
from tests.helpers import MockLLMClient


def _resp(text: str) -> dict:
    return {"reply": json.dumps({"resolved": [], "ongoing": [f"o-{text}"], "new": [],
                                 "narrative": text})}


def _item(i: int, tag: str | None) -> dict:
    return {"id": i, "status": "active", "text": f"fact {i}", "tag": tag}


@pytest.mark.asyncio
async def test_different_tags_produce_separate_sections():
    llm = MockLLMClient([_resp("Promotion news."), _resp("Chore dispute.")])
    out = await _summarise_by_tag(
        llm, [_item(1, "work"), _item(2, "household"), _item(3, "work")], "daily", "2026-11-18")
    assert len(llm.calls) == 2
    assert "ONE topic: work" in llm.calls[0]["system_prompt"]
    assert "ONE topic: household" in llm.calls[1]["system_prompt"]
    assert "[1]" in llm.calls[0]["user_content"] and "[3]" in llm.calls[0]["user_content"]
    assert "[2]" not in llm.calls[0]["user_content"]
    assert out["narrative"].splitlines() == ["- [work] Promotion news.",
                                             "- [household] Chore dispute."]


@pytest.mark.asyncio
async def test_null_and_empty_tags_go_to_other():
    llm = MockLLMClient([_resp("Misc A."), _resp("Kids B.")])
    out = await _summarise_by_tag(
        llm, [_item(1, None), _item(2, ""), _item(3, "kids")], "daily", "2026-11-18")
    assert len(llm.calls) == 2
    assert "ONE topic: other" in llm.calls[0]["system_prompt"]
    assert "[1]" in llm.calls[0]["user_content"] and "[2]" in llm.calls[0]["user_content"]
    assert "- [other] Misc A." in out["narrative"]
    assert "- [kids] Kids B." in out["narrative"]


@pytest.mark.asyncio
async def test_single_tag_makes_one_call():
    llm = MockLLMClient([_resp("Only work.")])
    out = await _summarise_by_tag(
        llm, [_item(1, "work"), _item(2, "work")], "daily", "2026-11-18")
    assert len(llm.calls) == 1
    assert out["narrative"] == "- [work] Only work."
    assert out["ongoing"] == ["o-Only work."]


@pytest.mark.asyncio
async def test_unparseable_group_aborts():
    llm = MockLLMClient([_resp("ok"), {"reply": "garbage"}])
    out = await _summarise_by_tag(
        llm, [_item(1, "a"), _item(2, "b")], "daily", "2026-11-18")
    assert out is None


@pytest.mark.asyncio
async def test_all_untagged_has_no_label():
    llm = MockLLMClient([_resp("Plain.")])
    out = await _summarise_by_tag(llm, [_item(1, None)], "daily", "2026-11-18")
    assert out["narrative"] == "Plain."
