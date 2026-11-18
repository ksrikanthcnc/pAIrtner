"""Tests for prompt building from the active prompt pack."""

from __future__ import annotations

from pathlib import Path

from pairtner import pack
from pairtner.core.prompts import (
    build_system_prompt,
    load_task_prompt,
    load_voice,
)

DEFAULT_PACK = Path(__file__).resolve().parent.parent / "packs" / "default"


class TestLoadVoice:
    def test_load_voice_file(self):
        result = load_voice("casual_en")
        assert result == (DEFAULT_PACK / "voices" / "casual_en.md").read_text().strip()

    def test_load_voice_other_voice(self):
        result = load_voice("telugu")
        assert result == (DEFAULT_PACK / "voices" / "telugu.md").read_text().strip()

    def test_load_voice_fallback(self):
        """Falls back to casual_en when requested voice is missing."""
        assert load_voice("nonexistent_voice") == load_voice("casual_en")


class TestLoadTaskPrompt:
    def test_load_task_prompt(self):
        result = load_task_prompt("nudge")
        assert result == (DEFAULT_PACK / "tasks" / "nudge.md").read_text().strip()

    def test_load_task_prompt_missing(self):
        assert load_task_prompt("nonexistent_task") is None

    def test_unloaded_tasks_are_not_loadable(self):
        """Tasks moved to unloaded/ are not served by the engine."""
        assert (DEFAULT_PACK / "unloaded" / "tasks" / "rewrite.md").is_file()
        assert load_task_prompt("rewrite") is None


class TestBuildSystemPrompt:
    def test_build_system_prompt_with_voice(self, tmp_path):
        voice_text = "Be very casual and friendly."
        result = build_system_prompt(voice_text=voice_text)
        assert "Be very casual and friendly." in result
        assert "## Communication Style" in result

    def test_build_system_prompt_with_task(self):
        task_text = "Rewrite the message gently."
        result = build_system_prompt(task_text=task_text)
        assert "Rewrite the message gently." in result
        assert "## Task Instructions" in result

    def test_build_system_prompt_backward_compat(self):
        """No voice/task = same structure as before (persona + role + tone + json)."""
        result = build_system_prompt()
        assert "## Voice" not in result
        assert "## Task Instructions" not in result
        # Core components are still present
        assert "compassionate couples mediator" in result
        assert "JSON" in result

    def test_build_records_trace(self):
        build_system_prompt()
        task, snapshot = pack.last_trace.get()
        assert task == "system"
        assert snapshot["name"] == "default"
        assert snapshot["pack_hash"]
