"""Tests for the prompt-pack validation CLI."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from pairtner import pack_cli

REPO = Path(__file__).resolve().parent.parent
DEFAULT = REPO / "packs" / "default"


@pytest.fixture
def pack_copy(tmp_path) -> Path:
    dst = tmp_path / "pack"
    shutil.copytree(DEFAULT, dst)
    return dst


def test_default_pack_is_ok(capsys):
    assert pack_cli.main(["validate", str(DEFAULT)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("OK:")
    assert "blocks=3" in out and "contract_json_examples=" in out


def test_missing_directory(tmp_path, capsys):
    assert pack_cli.main(["validate", str(tmp_path / "nope")]) == 1
    assert "directory not found" in capsys.readouterr().out


def test_missing_required_block(pack_copy):
    (pack_copy / "blocks" / "role.md").unlink()
    rep = pack_cli.validate(pack_copy)
    assert not rep.ok
    assert any("role.md" in e for e in rep.errors)


def test_missing_required_task(pack_copy):
    (pack_copy / "tasks" / "nudge.md").unlink()
    rep = pack_cli.validate(pack_copy)
    assert not rep.ok
    assert any("nudge.md" in e for e in rep.errors)


def test_undefined_block_reference(pack_copy):
    task = pack_copy / "tasks" / "chat.md"
    task.write_text(task.read_text() + "\n{{ghost_block}}\n")
    rep = pack_cli.validate(pack_copy)
    assert any("ghost_block" in e for e in rep.errors)


def test_defined_block_reference_ok(pack_copy):
    task = pack_copy / "tasks" / "chat.md"
    task.write_text(task.read_text() + "\n{{tone}}\n")
    assert pack_cli.validate(pack_copy).ok


def test_invalid_contract_json(pack_copy):
    c = pack_copy / "contract.md"
    c.write_text(c.read_text() + '\n```json\n{"reply": oops}\n```\n')
    rep = pack_cli.validate(pack_copy)
    assert any("JSON example" in e and "invalid" in e for e in rep.errors)


def test_extra_root_file_warns(pack_copy):
    (pack_copy / "stray.txt").write_text("x")
    rep = pack_cli.validate(pack_copy)
    assert rep.ok
    assert any("stray.txt" in w for w in rep.warnings)


def test_non_md_file_in_blocks_warns(pack_copy):
    (pack_copy / "blocks" / "notes.txt").write_text("x")
    rep = pack_cli.validate(pack_copy)
    assert any("notes.txt" in w for w in rep.warnings)


def test_broken_manifest_exit_code(pack_copy, capsys):
    (pack_copy / "pack.yaml").write_text("name: [unclosed")
    assert pack_cli.main(["validate", str(pack_copy)]) == 1
    assert "FAILED" in capsys.readouterr().out


# ---------------------------------------------------------------- diff

def test_diff_added_removed_changed():
    old = {"a.md": b"one\ntwo\n", "gone.md": b"x", "pack.yaml": b"name: a\nbudgets:\n  core: 1\n"}
    new = {"a.md": b"one\nTWO\n", "new.md": b"y", "pack.yaml": b"name: a\nbudgets:\n  core: 2\n  x: 3\n"}
    res = pack_cli.diff_trees(old, new)
    assert res.summary == "2 files changed, 1 added, 1 removed"
    text = "\n".join(res.lines)
    assert "-two" in text and "+TWO" in text
    assert "~ budgets.core: 1 -> 2" in text and "+ budgets.x: 3" in text


def test_diff_cli_two_packs(capsys):
    assert pack_cli.main(["diff", "packs/default", "packs/simple"]) == 0
    out = capsys.readouterr().out
    assert "removed: persona.md" in out and "files changed" in out


def test_diff_cli_git_head_clean(capsys):
    assert pack_cli.main(["diff", "packs/default", "--git", "HEAD"]) == 0
    assert "files changed" in capsys.readouterr().out


def test_diff_cli_errors(capsys):
    assert pack_cli.main(["diff", "packs/default"]) == 2
    assert pack_cli.main(["diff", "packs/nope", "packs/simple"]) == 2
    assert pack_cli.main(["diff", "packs/default", "--git", "no-such-rev"]) == 2
