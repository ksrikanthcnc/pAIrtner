"""Configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    """Bot configuration, loaded from env vars."""

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


def is_user_allowed(config: Config, user_id: int) -> bool:
    """Check if a user is allowed.  Empty allowlist = open mode (everyone)."""
    if not config.allowed_user_ids:
        return True
    return user_id in config.allowed_user_ids


def load_config() -> Config:
    """Load configuration from .env file and environment variables."""
    load_dotenv()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")

    llm_provider = os.getenv("LLM_PROVIDER", "gemini")

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

    heavy_raw = os.getenv(
        "MODEL_HEAVY", "gemini-3-flash-preview,gemini-3.8-flash"
    )
    model_heavy = [m.strip() for m in heavy_raw.split(",") if m.strip()]

    llm_heavy_raw = os.getenv("LLM_MODEL_HEAVY", "")
    llm_model_heavy = [m.strip() for m in llm_heavy_raw.split(",") if m.strip()]

    allowed_raw = os.getenv("ALLOWED_USER_IDS", "")
    allowed_user_ids = frozenset(
        int(uid.strip()) for uid in allowed_raw.split(",") if uid.strip()
    )

    return Config(
        telegram_token=token,
        gemini_api_key=gemini_api_key,
        model_default=os.getenv("MODEL_DEFAULT", "gemini-3.1-flash-lite"),
        model_heavy=model_heavy,
        cooldown_minutes=int(os.getenv("COOLDOWN_MINUTES", "15")),
        db_path=os.getenv("DB_PATH", "couples_bot.db"),
        heavy_daily_cap=int(os.getenv("HEAVY_DAILY_CAP", "10")),
        allowed_user_ids=allowed_user_ids,
        llm_provider=llm_provider,
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
        llm_model_default=os.getenv("LLM_MODEL_DEFAULT", ""),
        llm_model_heavy=llm_model_heavy,
    )
