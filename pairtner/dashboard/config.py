"""Dashboard settings: ``dashboard:`` block in settings.yaml + env overrides.

The dashboard never calls ``load_dotenv`` and never reads the bot's secrets
(Telegram token, LLM keys, DB encryption key). Its only secret is
``DASHBOARD_TOKEN``, read from the environment.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from pairtner.config import _load_yaml, _parse_bool

MIN_TOKEN_LENGTH = 16
MAX_PAGE_SIZE = 200


class DashboardConfigError(ValueError):
    """Raised when the dashboard configuration is invalid."""


@dataclass(frozen=True)
class DashboardConfig:
    token: str = ""
    db_path: str = "pairtner.db"
    backup_dir: str = "backups"
    host: str = "127.0.0.1"
    port: int = 8765
    show_raw_text: bool = True
    admin_actions_enabled: bool = True
    page_size: int = 50
    session_ttl_hours: float = 12.0

    def __repr__(self) -> str:  # never leak the token in logs / tracebacks
        return (
            f"DashboardConfig(host={self.host!r}, port={self.port}, "
            f"db_path={self.db_path!r}, show_raw_text={self.show_raw_text}, "
            f"admin_actions_enabled={self.admin_actions_enabled})"
        )


def load_dashboard_config(yaml_path: str = "settings.yaml") -> DashboardConfig:
    """Build a :class:`DashboardConfig` from YAML defaults and env overrides."""
    yaml_cfg = _load_yaml(yaml_path)
    dash = yaml_cfg.get("dashboard") or {}
    backup = yaml_cfg.get("backup") or {}

    token = os.environ.get("DASHBOARD_TOKEN", "")
    if len(token) < MIN_TOKEN_LENGTH:
        # Auto-generate a dev token when not set, for local testing convenience
        import secrets
        token = f"dev-{secrets.token_hex(12)}"
        os.environ["DASHBOARD_TOKEN"] = token
        import sys
        print(
            f"dashboard: No DASHBOARD_TOKEN set. Generated dev token: {token}",
            file=sys.stderr,
        )

    port = int(os.environ.get("DASHBOARD_PORT", str(dash.get("port", 8765))))
    if not 0 <= port <= 65535:
        raise DashboardConfigError(f"dashboard port out of range: {port}")

    page_size = int(
        os.environ.get("DASHBOARD_PAGE_SIZE", str(dash.get("page_size", 50)))
    )
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise DashboardConfigError(
            f"dashboard page_size must be 1..{MAX_PAGE_SIZE}, got {page_size}"
        )

    ttl = float(
        os.environ.get(
            "DASHBOARD_SESSION_TTL_HOURS",
            str(dash.get("session_ttl_hours", 12)),
        )
    )
    if ttl <= 0:
        raise DashboardConfigError("session_ttl_hours must be positive")

    return DashboardConfig(
        token=token,
        db_path=os.environ.get("DB_PATH", "pairtner.db"),
        backup_dir=os.environ.get(
            "BACKUP_DIRECTORY", backup.get("directory", "backups")
        ),
        host=os.environ.get("DASHBOARD_HOST", str(dash.get("host", "127.0.0.1"))),
        port=port,
        show_raw_text=_parse_bool(
            os.environ.get("DASHBOARD_SHOW_RAW_TEXT"),
            bool(dash.get("show_raw_text", True)),
        ),
        admin_actions_enabled=_parse_bool(
            os.environ.get("DASHBOARD_ADMIN_ACTIONS"),
            bool(dash.get("admin_actions_enabled", True)),
        ),
        page_size=page_size,
        session_ttl_hours=ttl,
    )
