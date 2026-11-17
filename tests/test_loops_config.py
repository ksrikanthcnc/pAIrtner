"""Tests for the loops: / rest: settings and the shared loop starter."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from couples_bot.adapters.loops import start_background_loops
from couples_bot.config import Config, load_config


def _load(tmp_path, monkeypatch, yaml_text="", env=None, token=True):
    f = tmp_path / "settings.yaml"
    f.write_text(yaml_text)
    for key in (
        "LOOPS_DIGEST_POLL_SECONDS", "LOOPS_CHECKIN_POLL_SECONDS",
        "LOOPS_NUDGE_INTERVAL_SECONDS", "LOOPS_BACKUP_INTERVAL_SECONDS",
        "REST_HOST", "REST_PORT", "REST_REGISTRATION", "REST_MAX_TEXT_CHARS",
        "REST_EVENT_RETENTION_DAYS", "REST_LONG_POLL_MAX_SECONDS",
        "ADAPTER", "LLM_PROVIDER", "TELEGRAM_BOT_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "key")
    if token:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    with patch("couples_bot.config.load_dotenv"):
        return load_config(yaml_path=str(f))


def test_loops_defaults_equal_old_hard_coded_values():
    cfg = Config(telegram_token="t")
    assert cfg.loops_digest_poll_seconds == 60
    assert cfg.loops_checkin_poll_seconds == 60
    assert cfg.loops_nudge_interval_seconds == 0
    assert cfg.loops_backup_interval_seconds == 3600


def test_loops_defaults_from_empty_yaml(tmp_path, monkeypatch):
    cfg = _load(tmp_path, monkeypatch)
    assert (cfg.loops_digest_poll_seconds, cfg.loops_checkin_poll_seconds,
            cfg.loops_nudge_interval_seconds, cfg.loops_backup_interval_seconds
            ) == (60, 60, 0, 3600)


def test_loops_yaml_and_env_override(tmp_path, monkeypatch):
    yaml_text = "loops:\n  digest_poll_seconds: 5\n  backup_interval_seconds: 99\n"
    cfg = _load(tmp_path, monkeypatch, yaml_text,
                {"LOOPS_CHECKIN_POLL_SECONDS": "7", "LOOPS_DIGEST_POLL_SECONDS": "2"})
    assert cfg.loops_digest_poll_seconds == 2  # env beats yaml
    assert cfg.loops_checkin_poll_seconds == 7
    assert cfg.loops_backup_interval_seconds == 99


def test_rest_defaults_and_env(tmp_path, monkeypatch):
    cfg = _load(tmp_path, monkeypatch)
    assert (cfg.rest_host, cfg.rest_port, cfg.rest_registration) == (
        "127.0.0.1", 8780, "invite")
    assert cfg.rest_max_text_chars == 4000
    assert cfg.rest_event_retention_days == 14
    assert cfg.rest_long_poll_max_seconds == 25
    cfg = _load(tmp_path, monkeypatch, env={
        "REST_PORT": "9000", "REST_REGISTRATION": "OPEN",
        "REST_MAX_TEXT_CHARS": "10", "REST_EVENT_RETENTION_DAYS": "3",
        "REST_LONG_POLL_MAX_SECONDS": "2", "REST_HOST": "0.0.0.0",
    })
    assert cfg.rest_port == 9000 and cfg.rest_registration == "open"
    assert cfg.rest_max_text_chars == 10 and cfg.rest_event_retention_days == 3
    assert cfg.rest_long_poll_max_seconds == 2 and cfg.rest_host == "0.0.0.0"


def test_invalid_registration_rejected(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="REST_REGISTRATION"):
        _load(tmp_path, monkeypatch, env={"REST_REGISTRATION": "anyone"})


def test_invite_code_never_in_config_repr(tmp_path, monkeypatch):
    cfg = _load(tmp_path, monkeypatch, env={"REST_INVITE_CODE": "SECRET-INVITE-XYZ"})
    assert "SECRET-INVITE-XYZ" not in repr(cfg)


def test_token_required_only_for_telegram(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        _load(tmp_path, monkeypatch, token=False)
    for name in ("telegram+rest", "both"):
        with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
            _load(tmp_path, monkeypatch, env={"ADAPTER": name}, token=False)
    cfg = _load(tmp_path, monkeypatch, env={"ADAPTER": "rest"}, token=False)
    assert cfg.adapter == "rest" and cfg.telegram_token == ""


class _Svc:
    def __init__(self, db_path):
        self.db_path = db_path
        self.llm = object()


async def test_loop_starter_passes_intervals(tmp_path):
    cfg = Config(
        telegram_token="t", db_path=str(tmp_path / "d.db"),
        loops_digest_poll_seconds=11, loops_checkin_poll_seconds=12,
        loops_nudge_interval_seconds=13, loops_backup_interval_seconds=14,
    )
    seen: dict[str, dict] = {}

    def fake(name):
        async def _loop(*args, **kwargs):
            seen[name] = kwargs
        return _loop

    spawned = []
    with patch("couples_bot.adapters.loops.delivery_loop", fake("delivery")), \
         patch("couples_bot.adapters.loops.backup_loop", fake("backup")), \
         patch("couples_bot.adapters.loops.digest_loop", fake("digest")), \
         patch("couples_bot.adapters.loops.checkin_loop", fake("checkin")), \
         patch("couples_bot.adapters.loops.nudge_loop", fake("nudge")):
        tasks = start_background_loops(
            _Svc(cfg.db_path), cfg, None, None,
            lambda coro: spawned.append(asyncio.ensure_future(coro)) or spawned[-1],
        )
        await asyncio.gather(*tasks)
    assert set(seen) == {"delivery", "backup", "digest", "checkin", "nudge"}
    assert seen["digest"]["poll_interval"] == 11
    assert seen["checkin"]["poll_interval"] == 12
    assert seen["nudge"]["poll_interval"] == 13
    assert seen["backup"]["interval_seconds"] == 14


async def test_nudge_interval_falls_back_to_hours(tmp_path):
    cfg = Config(telegram_token="t", db_path=str(tmp_path / "d.db"),
                 nudges_interval_hours=2)
    seen: dict = {}

    async def fake_nudge(*args, **kwargs):
        seen.update(kwargs)

    async def noop(*a, **k):
        return None

    with patch("couples_bot.adapters.loops.delivery_loop", noop), \
         patch("couples_bot.adapters.loops.backup_loop", noop), \
         patch("couples_bot.adapters.loops.digest_loop", noop), \
         patch("couples_bot.adapters.loops.checkin_loop", noop), \
         patch("couples_bot.adapters.loops.nudge_loop", fake_nudge):
        tasks = start_background_loops(
            _Svc(cfg.db_path), cfg, None, None, asyncio.ensure_future)
        await asyncio.gather(*tasks)
    assert seen["poll_interval"] == 7200
