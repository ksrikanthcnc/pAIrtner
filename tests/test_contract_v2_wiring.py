"""The v2 extraction contract is wired into the pack and v2 prompts."""

from __future__ import annotations

from pairtner import pack
from pairtner.core.context_composer import ComposedContext
from pairtner.core.prompts import build_chat_prompt_v2, build_system_prompt
from pairtner.pack import Pack
from tests.helpers import REPO_ROOT, copy_default_pack

MARK = "nudge_suggestion"
DEFAULT = REPO_ROOT / "packs" / "default"


def _activate(path):
    pack.activate(path)


def test_pack_loads_extract_v2():
    p = Pack(DEFAULT)
    assert MARK in p.contract_v2()
    assert p.contract_v2() == (DEFAULT / "extract_v2.md").read_text("utf-8").strip()
    assert MARK not in p.contract()


def test_system_prompt_v2_includes_v2_contract():
    _activate(DEFAULT)
    assert MARK in build_system_prompt(v2=True)


def test_system_prompt_v1_default_excludes_v2_contract():
    _activate(DEFAULT)
    assert MARK not in build_system_prompt()
    assert MARK not in build_system_prompt(v2=False)


def test_chat_prompt_v2_uses_v2_contract():
    _activate(DEFAULT)
    system, _ = build_chat_prompt_v2(ComposedContext(), "hi")
    assert MARK in system


def test_missing_extract_v2_falls_back_to_v1(tmp_path):
    root = copy_default_pack(tmp_path)
    (root / "extract_v2.md").unlink()
    p = Pack(root)
    assert p.contract_v2() == p.contract()
    _activate(root)
    system, _ = build_chat_prompt_v2(ComposedContext(), "hi")
    assert MARK not in system
    assert p.contract() in system
