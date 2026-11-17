"""Prompt pack loading, validation, hashing and accessors."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml

from couples_bot import pack
from couples_bot.pack import STRING_KEYS, Pack, PackError
from tests.helpers import REPO_ROOT, copy_default_pack

DEFAULT = REPO_ROOT / "packs" / "default"


@pytest.fixture
def pack_dir(tmp_path) -> Path:
    return copy_default_pack(tmp_path)


def _edit_manifest(root: Path, **changes) -> None:
    path = root / "pack.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def _edit_strings(root: Path, **changes) -> None:
    path = root / "strings.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


class TestValidPack:
    def test_default_pack_loads(self):
        p = Pack(DEFAULT)
        assert p.name == "default"
        assert p.version == "0.1.0"
        assert p.persona().startswith("# pAIrtner")
        assert p.block("role").startswith("You are a compassionate")
        assert "valid JSON" in p.contract()
        assert p.voice("casual_en")
        assert p.task("nudge")

    def test_default_pack_has_every_string_key(self):
        strings = yaml.safe_load((DEFAULT / "strings.yaml").read_text(encoding="utf-8"))
        assert set(strings) == set(STRING_KEYS)

    def test_string_placeholders(self):
        p = Pack(DEFAULT)
        assert p.s("impressions_of", who="Ann", about="Bo") == (
            "Impressions Ann has noted about Bo:"
        )
        assert p.s("smart_recent", recent_days=7) == "Recent notes (last 7 days)"

    def test_literal_braces_are_doubled(self, pack_dir):
        _edit_strings(pack_dir, goals_closing="Use {{braces}} literally.")
        assert Pack(pack_dir).s("goals_closing") == "Use {braces} literally."

    def test_unknown_voice_falls_back_to_casual(self):
        p = Pack(DEFAULT)
        assert p.voice("does_not_exist") == p.voice("casual_en")

    def test_snapshot_shape(self):
        snap = Pack(DEFAULT).snapshot()
        assert snap["name"] == "default"
        assert snap["version"] == "0.1.0"
        assert len(snap["pack_hash"]) == 64
        assert "pack.yaml" in snap["files"]
        assert "persona.md" in snap["files"]
        assert "strings.yaml" in snap["files"]
        assert "voices/casual_en.md" in snap["files"]
        assert not any(k.startswith(("unloaded/", "eval/")) for k in snap["files"])


class TestValidationErrors:
    def test_missing_directory(self, tmp_path):
        with pytest.raises(PackError, match="directory not found"):
            Pack(tmp_path / "nope")

    def test_missing_manifest(self, pack_dir):
        (pack_dir / "pack.yaml").unlink()
        with pytest.raises(PackError, match="pack.yaml: file is missing"):
            Pack(pack_dir)

    def test_malformed_manifest_yaml(self, pack_dir):
        (pack_dir / "pack.yaml").write_text("name: [unclosed\n")
        with pytest.raises(PackError, match="pack.yaml: malformed YAML"):
            Pack(pack_dir)

    def test_wrong_engine_contract(self, pack_dir):
        _edit_manifest(pack_dir, engine_contract=99)
        with pytest.raises(PackError, match="engine_contract is 99"):
            Pack(pack_dir)

    def test_missing_files_entry(self, pack_dir):
        data = yaml.safe_load((pack_dir / "pack.yaml").read_text())
        del data["files"]["strings"]
        _edit_manifest(pack_dir, files=data["files"])
        with pytest.raises(PackError, match="files.strings is missing"):
            Pack(pack_dir)

    def test_missing_file(self, pack_dir):
        (pack_dir / "persona.md").unlink()
        with pytest.raises(PackError, match="persona.md: file is missing"):
            Pack(pack_dir)

    def test_empty_file(self, pack_dir):
        (pack_dir / "contract.md").write_text("  \n")
        with pytest.raises(PackError, match="contract.md: file is empty"):
            Pack(pack_dir)

    def test_empty_voice_file(self, pack_dir):
        (pack_dir / "voices" / "hindi.md").write_text("")
        with pytest.raises(PackError, match="voices/hindi.md: file is empty"):
            Pack(pack_dir)

    def test_missing_required_block(self, pack_dir):
        (pack_dir / "blocks" / "tone.md").unlink()
        with pytest.raises(PackError, match="blocks/tone.md: file is missing"):
            Pack(pack_dir)

    def test_missing_required_task(self, pack_dir):
        (pack_dir / "tasks" / "summarize.md").unlink()
        with pytest.raises(PackError, match="tasks/summarize.md: file is missing"):
            Pack(pack_dir)

    def test_missing_fallback_voice(self, pack_dir):
        (pack_dir / "voices" / "casual_en.md").unlink()
        with pytest.raises(PackError, match="voices/casual_en.md"):
            Pack(pack_dir)

    def test_missing_strings_key(self, pack_dir):
        _edit_strings(pack_dir, tell_tail=None)
        with pytest.raises(PackError, match="strings.yaml: key 'tell_tail' is missing"):
            Pack(pack_dir)

    def test_non_string_value(self, pack_dir):
        _edit_strings(pack_dir, tell_tail=5)
        with pytest.raises(PackError, match="key 'tell_tail' must be a string"):
            Pack(pack_dir)

    def test_bad_placeholder(self, pack_dir):
        _edit_strings(pack_dir, history_user="User {nope}")
        with pytest.raises(PackError, match=r"key 'history_user' uses unknown placeholder"):
            Pack(pack_dir)

    def test_placeholder_from_other_key_is_rejected(self, pack_dir):
        _edit_strings(pack_dir, nudge_language="Language: {lang} {partner_name}")
        with pytest.raises(PackError, match="partner_name"):
            Pack(pack_dir)

    def test_malformed_strings_yaml(self, pack_dir):
        (pack_dir / "strings.yaml").write_text("a: [unclosed\n")
        with pytest.raises(PackError, match="strings.yaml: malformed YAML"):
            Pack(pack_dir)

    def test_eval_path_must_exist(self, pack_dir):
        _edit_manifest(pack_dir, eval="eval/cases.yaml")
        with pytest.raises(PackError, match="eval path"):
            Pack(pack_dir)

    def test_files_entry_inside_unloaded_is_rejected(self, pack_dir):
        (pack_dir / "unloaded").mkdir()
        (pack_dir / "unloaded" / "persona.md").write_text("old persona")
        data = yaml.safe_load((pack_dir / "pack.yaml").read_text())
        data["files"]["persona"] = "unloaded/persona.md"
        _edit_manifest(pack_dir, files=data["files"], unloaded="unloaded/")
        with pytest.raises(PackError, match="files.persona points inside unloaded/"):
            Pack(pack_dir)

    def test_files_entry_outside_pack_is_rejected(self, pack_dir):
        data = yaml.safe_load((pack_dir / "pack.yaml").read_text())
        data["files"]["persona"] = "../persona.md"
        _edit_manifest(pack_dir, files=data["files"])
        with pytest.raises(PackError, match="outside the pack"):
            Pack(pack_dir)

    def test_error_names_the_pack(self, pack_dir):
        (pack_dir / "persona.md").unlink()
        with pytest.raises(PackError) as err:
            Pack(pack_dir)
        assert str(pack_dir.resolve()) in str(err.value)


class TestHash:
    def test_hash_changes_when_loaded_file_changes(self, pack_dir):
        before = Pack(pack_dir).snapshot()
        (pack_dir / "persona.md").write_text("A different persona")
        after = Pack(pack_dir).snapshot()
        assert before["pack_hash"] != after["pack_hash"]
        assert before["files"]["persona.md"] != after["files"]["persona.md"]
        assert before["files"]["contract.md"] == after["files"]["contract.md"]

    def test_hash_changes_when_strings_change(self, pack_dir):
        before = Pack(pack_dir).snapshot()["pack_hash"]
        _edit_strings(pack_dir, tell_tail="IMPORTANT: changed.")
        assert Pack(pack_dir).snapshot()["pack_hash"] != before

    def test_hash_is_stable(self, pack_dir):
        assert Pack(pack_dir).snapshot() == Pack(pack_dir).snapshot()

    def test_eval_and_unloaded_do_not_affect_hash(self, pack_dir):
        (pack_dir / "eval").mkdir()
        (pack_dir / "eval" / "cases.yaml").write_text("cases: []\n")
        (pack_dir / "unloaded").mkdir()
        (pack_dir / "unloaded" / "old.md").write_text("old")
        _edit_manifest(pack_dir, eval="eval/cases.yaml", unloaded="unloaded/")
        before = Pack(pack_dir).snapshot()
        (pack_dir / "eval" / "cases.yaml").write_text("cases: [changed]\n")
        (pack_dir / "unloaded" / "old.md").write_text("changed")
        (pack_dir / "unloaded" / "new.md").write_text("new")
        after = Pack(pack_dir).snapshot()
        assert before == after

    def test_unloaded_files_are_never_read(self, pack_dir, monkeypatch):
        unloaded = pack_dir / "unloaded"
        (unloaded / "tasks").mkdir(parents=True)
        (unloaded / "tasks" / "rewrite.md").write_text("never loaded")
        (unloaded / "binary.md").write_bytes(b"\xff\xfe not utf-8")
        _edit_manifest(pack_dir, unloaded="unloaded/")

        read: list[Path] = []
        real_read_bytes = Path.read_bytes

        def spy(self):
            read.append(self)
            return real_read_bytes(self)

        monkeypatch.setattr(Path, "read_bytes", spy)
        p = Pack(pack_dir)
        p.refresh()
        assert read, "spy did not see any read"
        assert not any(
            path.resolve().is_relative_to((pack_dir / "unloaded").resolve())
            for path in read
        )
        assert p.task("rewrite") is None


class TestActivate:
    def test_activate_sets_current(self, pack_dir):
        pack.activate(pack_dir)
        assert pack.current().name == "default"
        assert pack.current().root == pack_dir.resolve()

    def test_current_lazily_activates_default(self):
        assert pack._current is None
        assert pack.current().root == DEFAULT.resolve()

    def test_relative_path_resolves_against_cwd_then_repo_root(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.chdir(tmp_path)
        assert pack.activate("packs/default").root == DEFAULT.resolve()
        copy_default_pack(tmp_path, name="mine")
        assert pack.activate("mine").root == (tmp_path / "mine").resolve()

    def test_invalid_pack_raises_and_keeps_previous(self, pack_dir, tmp_path):
        pack.activate(pack_dir)
        with pytest.raises(PackError):
            pack.activate(tmp_path / "missing")
        assert pack.current().root == pack_dir.resolve()


class TestRefresh:
    def test_refresh_picks_up_edit(self, pack_dir):
        p = Pack(pack_dir)
        old_hash = p.snapshot()["pack_hash"]
        (pack_dir / "persona.md").write_text("Edited persona")
        assert p.refresh() is True
        assert p.persona() == "Edited persona"
        assert p.snapshot()["pack_hash"] != old_hash

    def test_refresh_without_change_is_noop(self, pack_dir):
        p = Pack(pack_dir)
        assert p.refresh() is False

    def test_refresh_keeps_last_good_and_logs_once(self, pack_dir, caplog):
        p = Pack(pack_dir)
        good = p.persona()
        (pack_dir / "persona.md").write_text("")
        with caplog.at_level(logging.ERROR, logger="couples_bot.pack"):
            assert p.refresh() is False
            assert p.refresh() is False
            assert p.refresh() is False
        assert p.persona() == good
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert "persona.md" in errors[0].getMessage()

    def test_refresh_recovers_after_fix(self, pack_dir):
        p = Pack(pack_dir)
        (pack_dir / "persona.md").write_text("")
        p.refresh()
        (pack_dir / "persona.md").write_text("Fixed persona")
        assert p.refresh() is True
        assert p.persona() == "Fixed persona"

    def test_new_voice_file_is_picked_up(self, pack_dir):
        p = Pack(pack_dir)
        (pack_dir / "voices" / "pirate.md").write_text("Speak like a pirate.")
        assert p.refresh() is True
        assert p.voice("pirate") == "Speak like a pirate."

    def test_deleted_file_keeps_last_good(self, pack_dir):
        p = Pack(pack_dir)
        good = p.persona()
        (pack_dir / "persona.md").unlink()
        p.refresh()
        assert p.persona() == good
