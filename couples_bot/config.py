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
    llm_reasoning_effort: str = ""
    llm_model_heavy: list[str] = field(default_factory=list)

    # New tunables (FEAT-001)
    rate_limit_per_minute: int = 10
    rate_limit_per_day: int = 200
    delivery_check_interval_seconds: int = 30
    review_before_send: bool = True
    persona_file: str = "persona.md"
    prompts_dir: str = "prompts"
    voices_dir: str = "prompts/voices"
    default_voice: str = "casual_en"
    default_voice_intensity: float = 0.5

    # New tunables (FEAT-002)
    fact_categories: list[str] = field(
        default_factory=lambda: [
            "relationship", "household", "emotional", "work", "health", "other",
        ]
    )
    cooldown_behavior: str = "accumulate"

    # User intelligence (FEAT-002)
    mood_default_expiry_hours: int = 2
    mood_styles: dict = field(default_factory=lambda: {
        "stressed": {"dampening_override": 0.9, "response_length": "short"},
        "happy": {"dampening_override": 0.3, "response_length": "normal"},
        "angry": {"dampening_override": 0.95, "response_length": "short"},
        "reflective": {"dampening_override": 0.5, "response_length": "long"},
    })
    dampening_enabled: bool = True
    dampening_default_base: float = 0.7
    dampening_max_intensity: float = 10.0
    dampening_blunt_bypass: bool = True
    articulation_enabled: bool = True

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

    # Proactive engagement (FEAT-001 batch4)
    nudges_enabled: bool = True
    nudges_interval_hours: int = 24
    nudges_quiet_start: str = "09:00"
    nudges_quiet_end: str = "21:00"
    # /mediate window and perception inputs
    mediate_default_days: int = 7
    mediate_max_days: int = 90
    mediate_include_perceptions: bool = True
    mediate_min_confidence: float = 0.4
    mediate_max_themes_per_side: int = 8
    appreciation_enabled: bool = True
    appreciation_target_ratio: float = 5.0
    appreciation_check_days: int = 7
    horsemen_enabled: bool = True

    # Conflict resolution tracking (FEAT-002 batch4)
    conflicts_enabled: bool = True
    conflicts_auto_detect: bool = True
    conflicts_resolution_timeout_hours: int = 48

    # Relay feedback loop (FEAT-003 batch4)
    feedback_enabled: bool = True
    feedback_timeout_hours: int = 24

    # A/B rewrite (FEAT-003 batch4)
    ab_rewrite_enabled: bool = True
    ab_rewrite_dampening_range: list[float] = field(
        default_factory=lambda: [0.3, 0.7]
    )

    # Timeline (FEAT-004 batch4)
    timeline_enabled: bool = True
    timeline_max_events: int = 500

    # Read receipts (FEAT-004 batch4)
    read_receipts_enabled: bool = True

    # Therapy export (FEAT-004 batch4)
    therapy_export_enabled: bool = True

    # FTS5 search + smart context (FEAT-001 batch5)
    search_fts_threshold: int = 100
    context_recent_days: int = 7
    context_max_relevant_facts: int = 20

    # Voice auto-adaptation (FEAT-002 batch5)
    voice_auto_learn: bool = True
    voice_learn_after_messages: int = 10

    # Adapter architecture (FEAT-003 batch5)
    adapter: str = "telegram"
    ws_port: int = 8080
    # REST adapter (the invite code is read from REST_INVITE_CODE at use time
    # and is deliberately not a Config field, so it never shows in a repr).
    rest_host: str = "127.0.0.1"
    rest_port: int = 8780
    rest_registration: str = "invite"  # closed | invite | open
    rest_max_text_chars: int = 4000
    rest_event_retention_days: int = 14
    rest_long_poll_max_seconds: int = 25
    # Background loop intervals (defaults equal the old hard-coded values)
    loops_digest_poll_seconds: int = 60
    loops_checkin_poll_seconds: int = 60
    loops_nudge_interval_seconds: int = 0  # 0 = nudges.interval_hours * 3600
    loops_backup_interval_seconds: int = 3600


def is_user_allowed(config: Config, user_id: int) -> bool:
    """Check if a user is allowed.  Empty allowlist = open mode (everyone)."""
    if not config.allowed_user_ids:
        return True
    return user_id in config.allowed_user_ids


def _needs_telegram(adapter_name: str) -> bool:
    """True when the adapter selection includes the Telegram transport."""
    return "telegram" in adapter_name or adapter_name == "both"


_VALID_REGISTRATION = ("closed", "invite", "open")


def _parse_registration(value: str) -> str:
    value = value.strip().lower()
    if value not in _VALID_REGISTRATION:
        raise ValueError(
            f"Invalid REST_REGISTRATION={value!r}. "
            f"Valid: {', '.join(_VALID_REGISTRATION)}"
        )
    return value


def load_config(yaml_path: str = "settings.yaml") -> Config:
    """Load configuration: YAML defaults → env-var overrides.

    Secrets (``TELEGRAM_BOT_TOKEN``, ``GEMINI_API_KEY``, ``LLM_API_KEY``,
    ``DB_ENCRYPTION_KEY``) come **only** from environment variables.
    """
    load_dotenv()

    yaml_cfg = _load_yaml(yaml_path)
    models = yaml_cfg.get("models", {})
    rest_cfg = yaml_cfg.get("rest") or {}
    loops_cfg = yaml_cfg.get("loops") or {}

    # --- Provider validation (early) ---
    llm_provider = os.getenv("LLM_PROVIDER", yaml_cfg.get("llm_provider", "gemini"))
    if llm_provider not in _VALID_PROVIDERS:
        raise ValueError(
            f"Invalid LLM_PROVIDER={llm_provider!r}. "
            f"Valid providers: {', '.join(repr(p) for p in _VALID_PROVIDERS)}"
        )

    # --- Secrets (env only) ---
    adapter_name = os.getenv("ADAPTER", yaml_cfg.get("adapter", "telegram"))
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token and _needs_telegram(adapter_name):
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
        llm_reasoning_effort=os.getenv("LLM_REASONING_EFFORT", ""),
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
        prompts_dir=os.getenv(
            "PROMPTS_DIR", yaml_cfg.get("prompts_dir", "prompts")
        ),
        voices_dir=os.getenv(
            "VOICES_DIR", yaml_cfg.get("voices_dir", "prompts/voices")
        ),
        default_voice=os.getenv(
            "DEFAULT_VOICE", yaml_cfg.get("default_voice", "casual_en")
        ),
        default_voice_intensity=float(
            os.getenv(
                "DEFAULT_VOICE_INTENSITY",
                str(yaml_cfg.get("default_voice_intensity", 0.5)),
            )
        ),
        fact_categories=yaml_cfg.get(
            "fact_categories",
            ["relationship", "household", "emotional", "work", "health", "other"],
        ),
        cooldown_behavior=os.getenv(
            "COOLDOWN_BEHAVIOR",
            yaml_cfg.get("cooldown", {}).get("behavior", "accumulate"),
        ),
        mood_default_expiry_hours=int(
            os.getenv(
                "MOOD_DEFAULT_EXPIRY_HOURS",
                str(yaml_cfg.get("mood", {}).get("default_expiry_hours", 2)),
            )
        ),
        mood_styles=yaml_cfg.get("mood", {}).get("styles", {
            "stressed": {"dampening_override": 0.9, "response_length": "short"},
            "happy": {"dampening_override": 0.3, "response_length": "normal"},
            "angry": {"dampening_override": 0.95, "response_length": "short"},
            "reflective": {"dampening_override": 0.5, "response_length": "long"},
        }),
        dampening_enabled=_parse_bool(
            os.environ.get("DAMPENING_ENABLED"),
            yaml_cfg.get("dampening", {}).get("enabled", True),
        ),
        dampening_default_base=float(
            os.getenv(
                "DAMPENING_DEFAULT_BASE",
                str(yaml_cfg.get("dampening", {}).get("default_base", 0.7)),
            )
        ),
        dampening_max_intensity=float(
            os.getenv(
                "DAMPENING_MAX_INTENSITY",
                str(yaml_cfg.get("dampening", {}).get("max_intensity", 10.0)),
            )
        ),
        dampening_blunt_bypass=_parse_bool(
            os.environ.get("DAMPENING_BLUNT_BYPASS"),
            yaml_cfg.get("dampening", {}).get("blunt_bypass", True),
        ),
        articulation_enabled=_parse_bool(
            os.environ.get("ARTICULATION_ENABLED"),
            yaml_cfg.get("articulation", {}).get("enabled", True),
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
        nudges_enabled=_parse_bool(
            os.environ.get("NUDGES_ENABLED"),
            yaml_cfg.get("nudges", {}).get("enabled", True),
        ),
        nudges_interval_hours=int(
            os.getenv(
                "NUDGES_INTERVAL_HOURS",
                str(yaml_cfg.get("nudges", {}).get("interval_hours", 24)),
            )
        ),
        nudges_quiet_start=os.getenv(
            "NUDGES_QUIET_START",
            yaml_cfg.get("nudges", {}).get("quiet_start", "09:00"),
        ),
        nudges_quiet_end=os.getenv(
            "NUDGES_QUIET_END",
            yaml_cfg.get("nudges", {}).get("quiet_end", "21:00"),
        ),
        mediate_default_days=int(
            os.getenv(
                "MEDIATE_DEFAULT_DAYS",
                str(yaml_cfg.get("mediate", {}).get("default_days", 7)),
            )
        ),
        mediate_max_days=int(
            os.getenv(
                "MEDIATE_MAX_DAYS",
                str(yaml_cfg.get("mediate", {}).get("max_days", 90)),
            )
        ),
        mediate_include_perceptions=_parse_bool(
            os.environ.get("MEDIATE_INCLUDE_PERCEPTIONS"),
            yaml_cfg.get("mediate", {}).get("include_perceptions", True),
        ),
        mediate_min_confidence=float(
            os.getenv(
                "MEDIATE_MIN_CONFIDENCE",
                str(yaml_cfg.get("mediate", {}).get("min_confidence", 0.4)),
            )
        ),
        mediate_max_themes_per_side=int(
            os.getenv(
                "MEDIATE_MAX_THEMES_PER_SIDE",
                str(yaml_cfg.get("mediate", {}).get("max_themes_per_side", 8)),
            )
        ),
        appreciation_enabled=_parse_bool(
            os.environ.get("APPRECIATION_ENABLED"),
            yaml_cfg.get("appreciation", {}).get("enabled", True),
        ),
        appreciation_target_ratio=float(
            os.getenv(
                "APPRECIATION_TARGET_RATIO",
                str(yaml_cfg.get("appreciation", {}).get("target_ratio", 5.0)),
            )
        ),
        appreciation_check_days=int(
            os.getenv(
                "APPRECIATION_CHECK_DAYS",
                str(yaml_cfg.get("appreciation", {}).get("check_days", 7)),
            )
        ),
        horsemen_enabled=_parse_bool(
            os.environ.get("HORSEMEN_ENABLED"),
            yaml_cfg.get("horsemen", {}).get("enabled", True),
        ),
        conflicts_enabled=_parse_bool(
            os.environ.get("CONFLICTS_ENABLED"),
            yaml_cfg.get("conflicts", {}).get("enabled", True),
        ),
        conflicts_auto_detect=_parse_bool(
            os.environ.get("CONFLICTS_AUTO_DETECT"),
            yaml_cfg.get("conflicts", {}).get("auto_detect", True),
        ),
        conflicts_resolution_timeout_hours=int(
            os.getenv(
                "CONFLICTS_RESOLUTION_TIMEOUT_HOURS",
                str(yaml_cfg.get("conflicts", {}).get("resolution_timeout_hours", 48)),
            )
        ),
        feedback_enabled=_parse_bool(
            os.environ.get("FEEDBACK_ENABLED"),
            yaml_cfg.get("feedback", {}).get("enabled", True),
        ),
        feedback_timeout_hours=int(
            os.getenv(
                "FEEDBACK_TIMEOUT_HOURS",
                str(yaml_cfg.get("feedback", {}).get("timeout_hours", 24)),
            )
        ),
        ab_rewrite_enabled=_parse_bool(
            os.environ.get("AB_REWRITE_ENABLED"),
            yaml_cfg.get("ab_rewrite", {}).get("enabled", True),
        ),
        ab_rewrite_dampening_range=yaml_cfg.get("ab_rewrite", {}).get(
            "dampening_range", [0.3, 0.7]
        ),
        timeline_enabled=_parse_bool(
            os.environ.get("TIMELINE_ENABLED"),
            yaml_cfg.get("timeline", {}).get("enabled", True),
        ),
        timeline_max_events=int(
            os.getenv(
                "TIMELINE_MAX_EVENTS",
                str(yaml_cfg.get("timeline", {}).get("max_events", 500)),
            )
        ),
        read_receipts_enabled=_parse_bool(
            os.environ.get("READ_RECEIPTS_ENABLED"),
            yaml_cfg.get("read_receipts", {}).get("enabled", True),
        ),
        therapy_export_enabled=_parse_bool(
            os.environ.get("THERAPY_EXPORT_ENABLED"),
            yaml_cfg.get("therapy_export", {}).get("enabled", True),
        ),
        search_fts_threshold=int(
            os.getenv(
                "SEARCH_FTS_THRESHOLD",
                str(yaml_cfg.get("search", {}).get("fts_threshold", 100)),
            )
        ),
        context_recent_days=int(
            os.getenv(
                "CONTEXT_RECENT_DAYS",
                str(yaml_cfg.get("context", {}).get("recent_days", 7)),
            )
        ),
        context_max_relevant_facts=int(
            os.getenv(
                "CONTEXT_MAX_RELEVANT_FACTS",
                str(yaml_cfg.get("context", {}).get("max_relevant_facts", 20)),
            )
        ),
        voice_auto_learn=_parse_bool(
            os.environ.get("VOICE_AUTO_LEARN"),
            yaml_cfg.get("voice", {}).get("auto_learn", True),
        ),
        voice_learn_after_messages=int(
            os.getenv(
                "VOICE_LEARN_AFTER_MESSAGES",
                str(yaml_cfg.get("voice", {}).get("learn_after_messages", 10)),
            )
        ),
        adapter=os.getenv(
            "ADAPTER",
            yaml_cfg.get("adapter", "telegram"),
        ),
        ws_port=int(
            os.getenv(
                "WS_PORT",
                str(yaml_cfg.get("ws_port", 8080)),
            )
        ),
        rest_host=os.getenv("REST_HOST", str(rest_cfg.get("host", "127.0.0.1"))),
        rest_port=int(os.getenv("REST_PORT", str(rest_cfg.get("port", 8780)))),
        rest_registration=_parse_registration(os.getenv(
            "REST_REGISTRATION", str(rest_cfg.get("registration", "invite")))),
        rest_max_text_chars=int(os.getenv(
            "REST_MAX_TEXT_CHARS", str(rest_cfg.get("max_text_chars", 4000)))),
        rest_event_retention_days=int(os.getenv(
            "REST_EVENT_RETENTION_DAYS",
            str(rest_cfg.get("event_retention_days", 14)))),
        rest_long_poll_max_seconds=int(os.getenv(
            "REST_LONG_POLL_MAX_SECONDS",
            str(rest_cfg.get("long_poll_max_seconds", 25)))),
        loops_digest_poll_seconds=int(os.getenv(
            "LOOPS_DIGEST_POLL_SECONDS",
            str(loops_cfg.get("digest_poll_seconds", 60)))),
        loops_checkin_poll_seconds=int(os.getenv(
            "LOOPS_CHECKIN_POLL_SECONDS",
            str(loops_cfg.get("checkin_poll_seconds", 60)))),
        loops_nudge_interval_seconds=int(os.getenv(
            "LOOPS_NUDGE_INTERVAL_SECONDS",
            str(loops_cfg.get("nudge_interval_seconds", 0)))),
        loops_backup_interval_seconds=int(os.getenv(
            "LOOPS_BACKUP_INTERVAL_SECONDS",
            str(loops_cfg.get("backup_interval_seconds", 3600)))),
    )
