"""The merged single contract is wired into the pack and chat prompts."""

from __future__ import annotations

from pairtner import pack
from pairtner.core.context_composer import ComposedContext
from pairtner.core.prompts import build_chat_prompt_v2, build_system_prompt
from pairtner.pack import Pack
from tests.helpers import REPO_ROOT

MARK = "nudge_suggestion"
DEFAULT = REPO_ROOT / "packs" / "default"


def _activate(path):
    pack.activate(path)


def test_pack_contract_has_all_task_sections():
    c = Pack(DEFAULT).contract()
    assert MARK in c
    for task in ("chat", "rewrite", "keep", "blunt", "mediate", "digest",
                 "checkin_summary", "analysis"):
        assert f"## Task: {task}" in c
    for key in ("nudge_quiet_start", "nudge_quiet_end", "nudge_frequency"):
        assert key in c


def test_no_separate_v2_contract_file():
    assert not (DEFAULT / "extract_v2.md").exists()
    assert not hasattr(Pack(DEFAULT), "contract_v2")


def test_system_prompt_uses_single_contract():
    _activate(DEFAULT)
    assert pack.current().contract() in build_system_prompt()


def test_chat_prompt_v2_uses_contract():
    _activate(DEFAULT)
    system, _ = build_chat_prompt_v2(ComposedContext(), "hi")
    assert MARK in system
    assert pack.current().contract() in system
