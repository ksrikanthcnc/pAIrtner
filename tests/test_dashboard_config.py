"""Dashboard config: YAML defaults, env overrides, validation."""
from __future__ import annotations

import pytest

from pairtner.dashboard.config import (
    DashboardConfigError, load_dashboard_config,
)

TOKEN = "t" * 20
_ENV = [
    "DASHBOARD_TOKEN", "DASHBOARD_HOST", "DASHBOARD_PORT", "DASHBOARD_SHOW_RAW_TEXT",
    "DASHBOARD_ADMIN_ACTIONS", "DASHBOARD_PAGE_SIZE", "DASHBOARD_SESSION_TTL_HOURS",
    "DB_PATH", "BACKUP_DIRECTORY", "TELEGRAM_BOT_TOKEN",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DASHBOARD_TOKEN", TOKEN)


def test_defaults_without_yaml(tmp_path):
    cfg = load_dashboard_config(str(tmp_path / "missing.yaml"))
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8765
    assert cfg.show_raw_text is True
    assert cfg.admin_actions_enabled is True
    assert cfg.page_size == 50
    assert cfg.db_path == "pairtner.db"
    assert cfg.backup_dir == "backups"


def test_telegram_token_not_required():
    assert load_dashboard_config("nonexistent.yaml").token == TOKEN


def test_yaml_values(tmp_path):
    y = tmp_path / "s.yaml"
    y.write_text(
        "backup:\n  directory: /data/bk\n"
        "dashboard:\n  host: 10.0.0.5\n  port: 9000\n  show_raw_text: false\n"
        "  admin_actions_enabled: false\n  page_size: 20\n  session_ttl_hours: 1\n"
    )
    cfg = load_dashboard_config(str(y))
    assert (cfg.host, cfg.port, cfg.page_size) == ("10.0.0.5", 9000, 20)
    assert cfg.show_raw_text is False
    assert cfg.admin_actions_enabled is False
    assert cfg.session_ttl_hours == 1
    assert cfg.backup_dir == "/data/bk"


def test_env_overrides_yaml(tmp_path, monkeypatch):
    y = tmp_path / "s.yaml"
    y.write_text("dashboard:\n  port: 9000\n  show_raw_text: false\n")
    monkeypatch.setenv("DASHBOARD_PORT", "9100")
    monkeypatch.setenv("DASHBOARD_SHOW_RAW_TEXT", "true")
    monkeypatch.setenv("DASHBOARD_HOST", "0.0.0.0")
    monkeypatch.setenv("DASHBOARD_ADMIN_ACTIONS", "false")
    monkeypatch.setenv("DB_PATH", "/x/y.db")
    monkeypatch.setenv("BACKUP_DIRECTORY", "/x/bk")
    cfg = load_dashboard_config(str(y))
    assert cfg.port == 9100
    assert cfg.show_raw_text is True
    assert cfg.host == "0.0.0.0"
    assert cfg.admin_actions_enabled is False
    assert (cfg.db_path, cfg.backup_dir) == ("/x/y.db", "/x/bk")


@pytest.mark.parametrize("token", [None, "", "short"])
def test_missing_or_short_token_raises(monkeypatch, token):
    if token is None:
        monkeypatch.delenv("DASHBOARD_TOKEN")
    else:
        monkeypatch.setenv("DASHBOARD_TOKEN", token)
    with pytest.raises(DashboardConfigError):
        load_dashboard_config("nonexistent.yaml")


@pytest.mark.parametrize("env,val", [
    ("DASHBOARD_PORT", "70000"), ("DASHBOARD_PAGE_SIZE", "0"),
    ("DASHBOARD_PAGE_SIZE", "201"), ("DASHBOARD_SESSION_TTL_HOURS", "0"),
])
def test_invalid_values_raise(monkeypatch, env, val):
    monkeypatch.setenv(env, val)
    with pytest.raises(DashboardConfigError):
        load_dashboard_config("nonexistent.yaml")


def test_repr_hides_token():
    assert TOKEN not in repr(load_dashboard_config("nonexistent.yaml"))


def test_repo_settings_yaml_has_dashboard_block():
    cfg = load_dashboard_config("settings.yaml")
    assert cfg.port == 8765 and cfg.host == "127.0.0.1"
