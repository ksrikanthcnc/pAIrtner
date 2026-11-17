"""Tests for the deterministic fake LLM used by the dev rig."""
from __future__ import annotations

import importlib.util
import json
import urllib.request
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "dev" / "localtest" / "fake_llm.py"
_spec = importlib.util.spec_from_file_location("fake_llm_under_test", _PATH)
fake_llm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fake_llm)


def _chat(msg: str, heading: str = "Current message") -> str:
    return f"## Context\nsome context\n\n## {heading}\n{msg}"


def test_reply_is_upper_of_message():
    out = fake_llm.make_reply(_chat("hello there"))
    assert out["reply"] == "HELLO THERE"
    assert out["relay_draft"] is None
    assert out["new_facts"] == []
    assert out["partner_themes"] == []


def test_relay_prompt_sets_draft():
    out = fake_llm.make_reply(_chat("you never help", "Message to relay to partner"))
    assert out["relay_draft"] == "YOU NEVER HELP"


def test_keep_prompt_labels_fact_shared_on_purpose():
    out = fake_llm.make_reply(_chat("I like tea", "Message to save"))
    assert out["new_facts"][0]["text"] == "I LIKE TEA"
    assert out["new_facts"][0]["visibility"] == "shared"


def test_theme_marker_gives_fixed_theme_not_upper_message():
    out = fake_llm.make_reply(_chat("RAWSENTINEL he is distant [theme]"))
    assert out["partner_themes"] == ["THEME-OTHER-FEELS-UNHEARD"]


def test_selfstmt_marker_gives_first_person_theme():
    out = fake_llm.make_reply(_chat("RAWSENTINEL [selfstmt]"))
    assert out["partner_themes"] == ["I FEEL IGNORED"]
    assert "RAWSENTINEL" not in " ".join(out["partner_themes"])


def test_both_markers_and_case_insensitive():
    out = fake_llm.make_reply(_chat("x [THEME] [SelfStmt]"))
    assert out["partner_themes"] == ["THEME-OTHER-FEELS-UNHEARD", "I FEEL IGNORED"]


def test_marker_outside_the_message_block_is_ignored():
    content = "## Context\n[theme] in a fact\n\n## Current message\nplain"
    assert fake_llm.make_reply(content)["partner_themes"] == []


def test_mediate_prompt_falls_back_to_last_line():
    content = "## Period covered\nthe last 7 days\n\n## Request\nPlease provide a view."
    assert fake_llm.make_reply(content)["reply"] == "PLEASE PROVIDE A VIEW."


@pytest.fixture
def server():
    srv = fake_llm.start(0)
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _call(url: str, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def test_calls_log_records_and_clears(server):
    _call(f"{server}/_calls", "DELETE")
    out = _call(f"{server}/v1/chat/completions", "POST", {"messages": [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": _chat("ping")},
    ]})
    assert json.loads(out["choices"][0]["message"]["content"])["reply"] == "PING"
    calls = _call(f"{server}/_calls")["calls"]
    assert len(calls) == 1
    assert calls[0]["system"] == "SYS" and "ping" in calls[0]["user_content"]
    assert _call(f"{server}/_calls", "DELETE") == {"calls": []}
    assert _call(f"{server}/_calls")["calls"] == []
