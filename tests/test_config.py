"""Config tests — YAML loading, env var overrides, provider validation."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from couples_bot.config import Config, _load_yaml, load_config


# ---------------------------------------------------------------------------
# _load_yaml
# ---------------------------------------------------------------------------

class TestLoadYaml:
    def test_loads_yaml_file(self, tmp_path):
        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text(
            "cooldown_minutes: 20\n"
            "rate_limit_per_minute: 5\n"
            "models:\n"
            "  default: my-model\n"
            "  heavy:\n"
            "    - heavy-a\n"
            "    - heavy-b\n"
        )
        data = _load_yaml(str(yaml_file))
        assert data["cooldown_minutes"] == 20
        assert data["rate_limit_per_minute"] == 5
        assert data["models"]["default"] == "my-model"
        assert data["models"]["heavy"] == ["heavy-a", "heavy-b"]

    def test_missing_file_returns_empty(self, tmp_path):
        data = _load_yaml(str(tmp_path / "nonexistent.yaml"))
        assert data == {}

    def test_empty_file_returns_empty(self, tmp_path):
        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text("")
        data = _load_yaml(str(yaml_file))
        assert data == {}


# ---------------------------------------------------------------------------
# load_config — YAML + env integration
# ---------------------------------------------------------------------------

class TestLoadConfig:
    """Test load_config with temp YAML files and mocked env vars."""

    def test_yaml_values_used_as_defaults(self, tmp_path, monkeypatch):
        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text(
            "cooldown_minutes: 25\n"
            "rate_limit_per_minute: 7\n"
            "rate_limit_per_day: 150\n"
            "delivery_check_interval_seconds: 45\n"
            "review_before_send: false\n"
            "heavy_daily_cap: 5\n"
            "models:\n"
            "  default: yaml-model\n"
            "  heavy:\n"
            "    - yaml-heavy\n"
        )
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        # Clear any override env vars
        for var in (
            "LLM_PROVIDER", "COOLDOWN_MINUTES", "RATE_LIMIT_PER_MINUTE",
            "RATE_LIMIT_PER_DAY", "DELIVERY_CHECK_INTERVAL_SECONDS",
            "REVIEW_BEFORE_SEND", "HEAVY_DAILY_CAP", "MODEL_DEFAULT",
            "MODEL_HEAVY",
        ):
            monkeypatch.delenv(var, raising=False)

        with patch("couples_bot.config.load_dotenv"):
            cfg = load_config(yaml_path=str(yaml_file))

        assert cfg.cooldown_minutes == 25
        assert cfg.rate_limit_per_minute == 7
        assert cfg.rate_limit_per_day == 150
        assert cfg.delivery_check_interval_seconds == 45
        assert cfg.review_before_send is False
        assert cfg.heavy_daily_cap == 5
        assert cfg.model_default == "yaml-model"
        assert cfg.model_heavy == ["yaml-heavy"]

    def test_env_vars_override_yaml(self, tmp_path, monkeypatch):
        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text(
            "cooldown_minutes: 25\n"
            "rate_limit_per_minute: 7\n"
        )
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.setenv("COOLDOWN_MINUTES", "99")
        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "42")
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("MODEL_HEAVY", raising=False)

        with patch("couples_bot.config.load_dotenv"):
            cfg = load_config(yaml_path=str(yaml_file))

        assert cfg.cooldown_minutes == 99
        assert cfg.rate_limit_per_minute == 42

    def test_prompt_pack_default_and_yaml(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.delenv("PROMPT_PACK", raising=False)
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("MODEL_HEAVY", raising=False)
        empty = tmp_path / "empty.yaml"
        empty.write_text("")
        with patch("couples_bot.config.load_dotenv"):
            assert load_config(yaml_path=str(empty)).prompt_pack == "packs/default"
        custom = tmp_path / "custom.yaml"
        custom.write_text("prompt_pack: packs/mine\n")
        with patch("couples_bot.config.load_dotenv"):
            assert load_config(yaml_path=str(custom)).prompt_pack == "packs/mine"

    def test_prompt_pack_env_override(self, tmp_path, monkeypatch):
        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text("prompt_pack: packs/from_yaml\n")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.setenv("PROMPT_PACK", "/somewhere/else")
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("MODEL_HEAVY", raising=False)
        with patch("couples_bot.config.load_dotenv"):
            cfg = load_config(yaml_path=str(yaml_file))
        assert cfg.prompt_pack == "/somewhere/else"

    def test_old_prompt_keys_ignored(self, tmp_path, monkeypatch):
        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text(
            "persona_file: x.md\nprompts_dir: old\nvoices_dir: old/voices\n"
        )
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.delenv("PROMPT_PACK", raising=False)
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("MODEL_HEAVY", raising=False)
        with patch("couples_bot.config.load_dotenv"):
            cfg = load_config(yaml_path=str(yaml_file))
        assert cfg.prompt_pack == "packs/default"
        assert not hasattr(cfg, "prompts_dir")

    def test_review_before_send_env_true(self, tmp_path, monkeypatch):
        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text("review_before_send: false\n")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.setenv("REVIEW_BEFORE_SEND", "true")
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("MODEL_HEAVY", raising=False)

        with patch("couples_bot.config.load_dotenv"):
            cfg = load_config(yaml_path=str(yaml_file))

        assert cfg.review_before_send is True

    def test_invalid_provider_raises(self, tmp_path, monkeypatch):
        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text("")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.setenv("LLM_PROVIDER", "bad-provider")

        with patch("couples_bot.config.load_dotenv"), \
             pytest.raises(ValueError, match="Invalid LLM_PROVIDER"):
            load_config(yaml_path=str(yaml_file))

    def test_missing_yaml_uses_hardcoded_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
        monkeypatch.setenv("GEMINI_API_KEY", "key")
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("MODEL_HEAVY", raising=False)
        monkeypatch.delenv("COOLDOWN_MINUTES", raising=False)
        monkeypatch.delenv("RATE_LIMIT_PER_MINUTE", raising=False)
        monkeypatch.delenv("RATE_LIMIT_PER_DAY", raising=False)

        with patch("couples_bot.config.load_dotenv"):
            cfg = load_config(yaml_path=str(tmp_path / "nope.yaml"))

        assert cfg.cooldown_minutes == 15
        assert cfg.rate_limit_per_minute == 10
        assert cfg.rate_limit_per_day == 200
        assert cfg.delivery_check_interval_seconds == 30
        assert cfg.review_before_send is True


# ---------------------------------------------------------------------------
# Config dataclass direct construction
# ---------------------------------------------------------------------------

class TestConfigDefaults:
    def test_new_fields_have_defaults(self):
        cfg = Config(telegram_token="t")
        assert cfg.rate_limit_per_minute == 10
        assert cfg.rate_limit_per_day == 200
        assert cfg.delivery_check_interval_seconds == 30
        assert cfg.review_before_send is True

    def test_new_config_fields_defaults(self):
        """prompt_pack, default_voice, default_voice_intensity defaults."""
        cfg = Config(telegram_token="t")
        assert cfg.prompt_pack == "packs/default"
        for gone in ("persona_file", "prompts_dir", "voices_dir"):
            assert not hasattr(cfg, gone)
        assert cfg.default_voice == "casual_en"
        assert cfg.default_voice_intensity == 0.5

    def test_feat002_config_fields_defaults(self):
        """mood, dampening, articulation config defaults."""
        cfg = Config(telegram_token="t")
        assert cfg.mood_default_expiry_hours == 2
        assert isinstance(cfg.mood_styles, dict)
        assert "stressed" in cfg.mood_styles
        assert cfg.dampening_enabled is True
        assert cfg.dampening_default_base == 0.7
        assert cfg.dampening_max_intensity == 10.0
        assert cfg.dampening_blunt_bypass is True
        assert cfg.articulation_enabled is True
