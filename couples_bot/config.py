"""Configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    """Bot configuration, loaded from env vars."""

    telegram_token: str
    gemini_api_key: str
    model_default: str = "gemini-3.1-flash-lite"
    model_heavy: list[str] = field(
        default_factory=lambda: ["gemini-3-flash-preview", "gemini-3.8-flash"]
    )
    cooldown_minutes: int = 15
    db_path: str = "couples_bot.db"
    heavy_daily_cap: int = 10


def load_config() -> Config:
    """Load configuration from .env file and environment variables."""
    load_dotenv()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is required")

    heavy_raw = os.getenv(
        "MODEL_HEAVY", "gemini-3-flash-preview,gemini-3.8-flash"
    )
    model_heavy = [m.strip() for m in heavy_raw.split(",") if m.strip()]

    return Config(
        telegram_token=token,
        gemini_api_key=api_key,
        model_default=os.getenv("MODEL_DEFAULT", "gemini-3.1-flash-lite"),
        model_heavy=model_heavy,
        cooldown_minutes=int(os.getenv("COOLDOWN_MINUTES", "15")),
        db_path=os.getenv("DB_PATH", "couples_bot.db"),
        heavy_daily_cap=int(os.getenv("HEAVY_DAILY_CAP", "10")),
    )
