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
    )
