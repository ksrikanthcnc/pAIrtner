"""Tests for the pack evaluation runner (dev/prompt-eval/run_eval.py).

Everything runs against the fake LLM or in-process stubs: no network, no
Telegram, no API key.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import shutil
from pathlib import Path

import pytest
import yaml

from couples_bot import pack as pack_mod
from couples_bot.llm import FALLBACK_REPLY

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "run_eval_under_test", ROOT / "dev" / "prompt-eval" / "run_eval.py",
)
run_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_eval)

CASES = {"cases": [
    {"id": "t-chat", "area": "chat", "message": "I had a long day",
     "expect": {"must": ["is short"], "must_not": ["lectures"]}},
    {"id": "t-send", "area": "rewrite", "tag": "#send",
     "message": "you never help",
     "context": {"partner_view": ["feels overwhelmed lately"], "unknown": ["kids"]},
     "expect": {"must": ["keeps the ask"], "memory": ['add working "x"']}},
    {"id": "t-mediate", "area": "mediate", "context": {"a_view": [], "b_view": [], "shared": []},
     "expect": {"memory": []}},
]}


@pytest.fixture
def tmp_pack(tmp_path):
    root = tmp_path / "pack"
    shutil.copytree(ROOT / "packs" / "default", root)
    (root / "eval" / "cases.yaml").write_text(yaml.safe_dump(CASES), encoding="utf-8")
    yield root
    pack_mod.activate(ROOT / "packs" / "default")  # the runner activates globally


def _args(pack: Path, out: Path, *extra: str):
    return run_eval.parse_args(["--llm", "fake", "--pack", str(pack), "--out", str(out), *extra])


def _run_dir(out: Path) -> Path:
    dirs = list(out.glob("default/0.1.0/*_fake-*"))
    assert len(dirs) == 1
    return dirs[0]


class _Stub:
    """Inner client that answers with the engine's fallback reply."""

    def __init__(self, log_429: bool) -> None:
        self.log_429 = log_429

    async def generate(self, user_content, system_prompt, model=None, api_key_override=None):
        if self.log_429:
            logging.getLogger("couples_bot.llm").warning("429 on %s — backing off 1s", "m")
        return {"reply": FALLBACK_REPLY, "new_facts": [], "relay_draft": None}


async def test_fake_run_writes_layout_and_records_without_verdicts(tmp_pack, tmp_path):
    out = tmp_path / "out"
    assert await run_eval.run(_args(tmp_pack, out)) == 0
    d = _run_dir(out)
    assert (d / "summary.md").is_file()
    assert {p.name for p in (d / "cases").iterdir()} == {"t-chat.json", "t-send.json", "t-mediate.json"}
    results = json.loads((d / "results.json").read_text())
    assert results["meta"]["pack_name"] == "default"
    assert results["meta"]["stop_note"] == ""
    send = json.loads((d / "cases" / "t-send.json").read_text())
    assert send["status"] == "run"
    assert "YOU NEVER HELP" in send["relay_draft"]
    call = send["llm_calls"][0]
    assert call["parsed"]["relay_draft"] == "YOU NEVER HELP"
    assert len(call["pack"]["pack_hash"]) == 64 and call["pack"]["files"]
    assert send["memory_expectation"]["status"] == "not_yet_supported"
    assert any("unknown" in n for n in send["seeding_notes"])
    assert "verdict" not in send and "passed" not in send
    chat = json.loads((d / "cases" / "t-chat.json").read_text())
    assert chat["memory_expectation"]["status"] == "none_specified"
    assert chat["must"] == ["is short"] and chat["must_not"] == ["lectures"]
    mediate = json.loads((d / "cases" / "t-mediate.json").read_text())
    assert mediate["llm_calls"] == []  # thin data: answered without the model
    assert mediate["memory_expectation"]["status"] == "expects_no_change"
    summary = (d / "summary.md").read_text()
    assert "cases run: 3 | skipped: 0 | LLM calls: 2" in summary
    assert "no LLM call was needed" in summary
    assert summary.index("## t-chat") < summary.index("## t-send") < summary.index("## t-mediate")


async def test_seeded_partner_view_appears_only_in_the_prompt_that_has_it(tmp_pack, tmp_path):
    out = tmp_path / "out"
    await run_eval.run(_args(tmp_pack, out))
    d = _run_dir(out)
    send = json.loads((d / "cases" / "t-send.json").read_text())
    chat = json.loads((d / "cases" / "t-chat.json").read_text())
    seen = send["llm_calls"][0]
    assert "feels overwhelmed lately" in seen["system"] + seen["user_content"]
    other = chat["llm_calls"][0]
    assert "feels overwhelmed lately" not in other["system"] + other["user_content"]


@pytest.mark.parametrize("log_429, word", [(True, "quota exhausted"), (False, "model unavailable")])
async def test_fallback_reply_stops_cleanly_with_exit_3(tmp_pack, tmp_path, capsys, log_429, word):
    out = tmp_path / "out"
    code = await run_eval.run(
        _args(tmp_pack, out), inner_factory=lambda cfg, db: _Stub(log_429),
    )
    assert code == run_eval.EXIT_STOPPED
    printed = capsys.readouterr().out
    assert "STOPPED EARLY" in printed and "--resume" in printed
    d = _run_dir(out)
    results = json.loads((d / "results.json").read_text())
    assert word in results["meta"]["stop_note"]
    assert [c["status"] for c in results["cases"]] == ["skipped"] * 3
    assert f"STOPPED EARLY: {results['meta']['stop_note']}" in (d / "summary.md").read_text()


async def test_max_calls_stops_before_the_next_case(tmp_pack, tmp_path):
    out = tmp_path / "out"
    code = await run_eval.run(_args(tmp_pack, out, "--max-calls", "1"))
    assert code == run_eval.EXIT_STOPPED
    results = json.loads((_run_dir(out) / "results.json").read_text())
    assert [c["status"] for c in results["cases"]] == ["run", "skipped", "skipped"]
    assert "--max-calls" in results["meta"]["stop_note"]


async def test_resume_runs_only_the_skipped_cases(tmp_pack, tmp_path):
    out = tmp_path / "out"
    assert await run_eval.run(_args(tmp_pack, out, "--max-calls", "1")) == run_eval.EXIT_STOPPED
    d = _run_dir(out)
    assert await run_eval.run(_args(tmp_pack, out, "--resume", str(d))) == 0
    results = json.loads((d / "results.json").read_text())
    assert [c["status"] for c in results["cases"]] == ["run"] * 3
    assert results["meta"]["stop_note"] == ""


async def test_only_filters_cases(tmp_pack, tmp_path):
    out = tmp_path / "out"
    assert await run_eval.run(_args(tmp_pack, out, "--only", "t-chat")) == 0
    assert [p.name for p in (_run_dir(out) / "cases").iterdir()] == ["t-chat.json"]


async def test_throttle_spaces_calls_by_rpm():
    now = [0.0]
    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)
        now[0] += s

    class Inner:
        async def generate(self, *a, **k):
            return {}

    th = run_eval.Throttle(Inner(), 12, clock=lambda: now[0], sleep=fake_sleep)
    for _ in range(3):
        await th.generate("u", "s")
    assert slept == [5.0, 5.0]
    assert th.count == 3
    off = run_eval.Throttle(Inner(), 0, clock=lambda: now[0], sleep=fake_sleep)
    await off.generate("u", "s")
    assert slept == [5.0, 5.0]


def test_unknown_case_id_is_an_error():
    with pytest.raises(ValueError, match="unknown case id"):
        run_eval.eval_lib.select_cases(CASES["cases"], "nope", None)


def test_packs_doc_lists_every_string_key_with_its_placeholders():
    doc = (ROOT / "docs" / "PACKS.md").read_text(encoding="utf-8")
    for key, placeholders in pack_mod.STRING_KEYS.items():
        row = next((ln for ln in doc.splitlines() if ln.startswith(f"| `{key}` |")), None)
        assert row, f"docs/PACKS.md is missing string key {key}"
        for name in placeholders:
            assert "`{%s}`" % name in row, f"{key}: placeholder {name} not documented"


async def test_http_504_from_the_engine_stops_as_unavailable(tmp_pack, tmp_path, monkeypatch):
    real_say = run_eval.Api.say

    def say(self, text):
        if text.startswith("I had"):
            raise run_eval.ProviderTimeout("timed out")
        return real_say(self, text)

    monkeypatch.setattr(run_eval.Api, "say", say)
    out = tmp_path / "out"
    assert await run_eval.run(_args(tmp_pack, out)) == run_eval.EXIT_STOPPED
    results = json.loads((_run_dir(out) / "results.json").read_text())
    assert "model unavailable" in results["meta"]["stop_note"]
    assert [c["status"] for c in results["cases"]] == ["skipped"] * 3


async def test_resume_with_only_keeps_earlier_results_in_the_summary(tmp_pack, tmp_path):
    out = tmp_path / "out"
    assert await run_eval.run(_args(tmp_pack, out, "--only", "t-chat")) == 0
    d = _run_dir(out)
    assert await run_eval.run(_args(tmp_pack, out, "--resume", str(d), "--only", "t-send")) == 0
    results = json.loads((d / "results.json").read_text())
    assert [c["id"] for c in results["cases"]] == ["t-chat", "t-send"]
