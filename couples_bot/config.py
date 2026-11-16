"""Configuration loaded from YAML defaults + environment variable overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml
from dotenv import load_dotenv

_VALID_PROVIDERS = ("gemini", "openai_compat")


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------

def _load_yaml(path: str = "settings.yaml") -> dict[str, Any]:
    """Load tunables from a YAML config file.

    Returns an empty dict when the file does not exist.
    """
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


# ---------------------------------------------------------------------------
# Bool helper
# ---------------------------------------------------------------------------

def _parse_bool(env_val: str | None, default: bool) -> bool:
    """Parse a boolean from an env-var string, falling back to *default*."""
    if env_val is None:
        return default
    return env_val.lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Config:
    """Bot configuration — YAML defaults, env-var overrides, secrets from env."""

    telegram_token: str
    gemini_api_key: str = ""
    model_default: str = "gemini-3.1-flash-lite"
    model_heavy: list[str] = field(
        default_factory=lambda: ["gemini-3-flash-preview", "gemini-3.8-flash"]
    )
    cooldown_minutes: int = 15
    db_path: str = "couples_bot.db"
    heavy_daily_cap: int = 10
    allowed_user_ids: frozenset[int] = field(default_factory=frozenset)
    llm_provider: str = "gemini"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model_default: str = ""
    llm_model_heavy: list[str] = field(default_factory=list)

    # New tunables (FEAT-001)
    rate_limit_per_minute: int = 10
    rate_limit_per_day: int = 200
    delivery_check_interval_seconds: int = 30
    review_before_send: bool = True
    persona_file: str = "persona.md"

    # New tunables (FEAT-002)
    fact_categories: list[str] = field(
        default_factory=lambda: [
            "relationship", "household", "emotional", "work", "health", "other",
        ]
    )
    cooldown_behavior: str = "accumulate"

    # New tunables (FEAT-003) — background tasks
    backup_enabled: bool = True
    backup_directory: str = "backups"
    backup_daily_count: int = 7
    backup_weekly_count: int = 4
    digest_enabled: bool = True
    digest_time: str = "20:00"
    digest_timezone: str = "Asia/Kolkata"
    checkin_enabled: bool = True
    checkin_day: str = "sunday"
    checkin_time: str = "18:00"
    checkin_timezone: str = "Asia/Kolkata"
    checkin_questions: list[str] = field(
        default_factory=lambda: [
            "What went well this week?",
            "What could we do differently?",
            "What are you grateful for about us?",
        ]
    )


def is_user_allowed(config: Config, user_id: int) -> bool:
    """Check if a user is allowed.  Empty allowlist = open mode (everyone)."""
    if not config.allowed_user_ids:
        return True
    return user_id in config.allowed_user_ids


def load_config(yaml_path: str = "settings.yaml") -> Config:
    """Load configuration: YAML defaults → env-var overrides.

    Secrets (``TELEGRAM_BOT_TOKEN``, ``GEMINI_API_KEY``, ``LLM_API_KEY``,
    ``DB_ENCRYPTION_KEY``) come **only** from environment variables.
    """
    load_dotenv()

    yaml_cfg = _load_yaml(yaml_path)
    models = yaml_cfg.get("models", {})

    # --- Provider validation (early) ---
    llm_provider = os.getenv("LLM_PROVIDER", yaml_cfg.get("llm_provider", "gemini"))
    if llm_provider not in _VALID_PROVIDERS:
        raise ValueError(
            f"Invalid LLM_PROVIDER={llm_provider!r}. "
            f"Valid providers: {', '.join(repr(p) for p in _VALID_PROVIDERS)}"
        )

    # --- Secrets (env only) ---
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")

    gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
    if llm_provider == "gemini" and not gemini_api_key:
        raise ValueError("GEMINI_API_KEY is required when LLM_PROVIDER is 'gemini'")

    llm_base_url = os.getenv("LLM_BASE_URL", "")
    llm_api_key = os.getenv("LLM_API_KEY", "")
    if llm_provider == "openai_compat":
        if not llm_base_url:
            raise ValueError("LLM_BASE_URL is required when LLM_PROVIDER is 'openai_compat'")
        if not llm_api_key:
            raise ValueError("LLM_API_KEY is required when LLM_PROVIDER is 'openai_compat'")

    # --- Tunables: YAML defaults, env overrides ---
    heavy_raw = os.getenv("MODEL_HEAVY", "")
    if heavy_raw:
        model_heavy = [m.strip() for m in heavy_raw.split(",") if m.strip()]
    else:
        model_heavy = models.get(
            "heavy", ["gemini-3-flash-preview", "gemini-3.8-flash"]
        )

    llm_heavy_raw = os.getenv("LLM_MODEL_HEAVY", "")
    llm_model_heavy = [m.strip() for m in llm_heavy_raw.split(",") if m.strip()]

    allowed_raw = os.getenv("ALLOWED_USER_IDS", "")
    allowed_user_ids = frozenset(
        int(uid.strip()) for uid in allowed_raw.split(",") if uid.strip()
    )

    return Config(
        telegram_token=token,
        gemini_api_key=gemini_api_key,
        model_default=os.getenv(
            "MODEL_DEFAULT", models.get("default", "gemini-3.1-flash-lite")
        ),
        model_heavy=model_heavy,
        cooldown_minutes=int(
            os.getenv("COOLDOWN_MINUTES", str(yaml_cfg.get("cooldown_minutes", 15)))
        ),
        db_path=os.getenv("DB_PATH", "couples_bot.db"),
        heavy_daily_cap=int(
            os.getenv("HEAVY_DAILY_CAP", str(yaml_cfg.get("heavy_daily_cap", 10)))
        ),
        allowed_user_ids=allowed_user_ids,
        llm_provider=llm_provider,
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
        llm_model_default=os.getenv("LLM_MODEL_DEFAULT", ""),
        llm_model_heavy=llm_model_heavy,
        rate_limit_per_minute=int(
            os.getenv(
                "RATE_LIMIT_PER_MINUTE",
                str(yaml_cfg.get("rate_limit_per_minute", 10)),
            )
        ),
        rate_limit_per_day=int(
            os.getenv(
                "RATE_LIMIT_PER_DAY",
                str(yaml_cfg.get("rate_limit_per_day", 200)),
            )
        ),
        delivery_check_interval_seconds=int(
            os.getenv(
                "DELIVERY_CHECK_INTERVAL_SECONDS",
                str(yaml_cfg.get("delivery_check_interval_seconds", 30)),
            )
        ),
        review_before_send=_parse_bool(
            os.environ.get("REVIEW_BEFORE_SEND"),
            yaml_cfg.get("review_before_send", True),
        ),
        persona_file=os.getenv(
            "PERSONA_FILE", yaml_cfg.get("persona_file", "persona.md")
        ),
        fact_categories=yaml_cfg.get(
            "fact_categories",
            ["relationship", "household", "emotional", "work", "health", "other"],
        ),
        cooldown_behavior=os.getenv(
            "COOLDOWN_BEHAVIOR",
            yaml_cfg.get("cooldown", {}).get("behavior", "accumulate"),
        ),
        backup_enabled=_parse_bool(
            os.environ.get("BACKUP_ENABLED"),
            yaml_cfg.get("backup", {}).get("enabled", True),
        ),
        backup_directory=os.getenv(
            "BACKUP_DIRECTORY",
            yaml_cfg.get("backup", {}).get("directory", "backups"),
        ),
        backup_daily_count=int(
            os.getenv(
                "BACKUP_DAILY_COUNT",
                str(yaml_cfg.get("backup", {}).get("daily_count", 7)),
            )
        ),
        backup_weekly_count=int(
            os.getenv(
                "BACKUP_WEEKLY_COUNT",
                str(yaml_cfg.get("backup", {}).get("weekly_count", 4)),
            )
        ),
        digest_enabled=_parse_bool(
            os.environ.get("DIGEST_ENABLED"),
            yaml_cfg.get("digest", {}).get("enabled", True),
        ),
        digest_time=os.getenv(
            "DIGEST_TIME",
            yaml_cfg.get("digest", {}).get("time", "20:00"),
        ),
        digest_timezone=os.getenv(
            "DIGEST_TIMEZONE",
            yaml_cfg.get("digest", {}).get("timezone", "Asia/Kolkata"),
        ),
        checkin_enabled=_parse_bool(
            os.environ.get("CHECKIN_ENABLED"),
            yaml_cfg.get("checkin", {}).get("enabled", True),
        ),
        checkin_day=os.getenv(
            "CHECKIN_DAY",
            yaml_cfg.get("checkin", {}).get("day", "sunday"),
        ),
        checkin_time=os.getenv(
            "CHECKIN_TIME",
            yaml_cfg.get("checkin", {}).get("time", "18:00"),
        ),
        checkin_timezone=os.getenv(
            "CHECKIN_TIMEZONE",
            yaml_cfg.get("checkin", {}).get("timezone", "Asia/Kolkata"),
        ),
        checkin_questions=yaml_cfg.get("checkin", {}).get("questions", [
            "What went well this week?",
            "What could we do differently?",
            "What are you grateful for about us?",
        ]),
    )
