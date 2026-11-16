"""Tests for prompt loading and building — directory-based prompts."""

from __future__ import annotations

import couples_bot.prompts as prompts_mod
from couples_bot.prompts import (
    _FALLBACK_PERSONA,
    build_system_prompt,
    load_persona,
    load_prompt_file,
    load_task_prompt,
    load_voice,
)


class TestLoadPromptFile:
    def test_reads_existing_file(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("hello world")
        assert load_prompt_file(str(f)) == "hello world"

    def test_returns_none_for_missing_file(self, tmp_path):
        assert load_prompt_file(str(tmp_path / "nope.md")) is None

    def test_returns_none_for_empty_file(self, tmp_path):
        f = tmp_path / "empty.md"
        f.write_text("   ")
        assert load_prompt_file(str(f)) is None


class TestLoadPersona:
    def test_load_persona_from_prompts_dir(self, tmp_path):
        persona = tmp_path / "persona.md"
        persona.write_text("Custom persona text here")
        result = load_persona(str(persona))
        assert result == "Custom persona text here"

    def test_load_persona_fallback(self, tmp_path):
        # Reset the cached persona
        prompts_mod._persona_text = None
        result = load_persona(str(tmp_path / "missing_persona.md"))
        assert result == _FALLBACK_PERSONA


class TestLoadVoice:
    def test_load_voice_file(self, tmp_path):
        voices = tmp_path / "voices"
        voices.mkdir()
        (voices / "casual_en.md").write_text("Casual voice content")
        result = load_voice(str(voices), "casual_en")
        assert result == "Casual voice content"

    def test_load_voice_fallback(self, tmp_path):
        """Falls back to casual_en when requested voice is missing."""
        voices = tmp_path / "voices"
        voices.mkdir()
        (voices / "casual_en.md").write_text("Fallback casual voice")
        result = load_voice(str(voices), "nonexistent_voice")
        assert result == "Fallback casual voice"

    def test_load_voice_builtin_default(self, tmp_path):
        """Falls back to built-in default when no voice files exist."""
        voices = tmp_path / "empty_voices"
        voices.mkdir()
        result = load_voice(str(voices), "casual_en")
        assert "friendly" in result.lower() or "warm" in result.lower()


class TestLoadTaskPrompt:
    def test_load_task_prompt(self, tmp_path):
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        (tasks / "rewrite.md").write_text("Rewrite instructions here")
        result = load_task_prompt(str(tmp_path), "rewrite")
        assert result == "Rewrite instructions here"

    def test_load_task_prompt_missing(self, tmp_path):
        result = load_task_prompt(str(tmp_path), "nonexistent_task")
        assert result is None


class TestBuildSystemPrompt:
    def test_build_system_prompt_with_voice(self, tmp_path):
        voice_text = "Be very casual and friendly."
        result = build_system_prompt(voice_text=voice_text)
        assert "Be very casual and friendly." in result
        assert "## Voice" in result

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
