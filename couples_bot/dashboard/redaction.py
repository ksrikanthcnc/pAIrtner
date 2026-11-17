"""Secret redaction and the raw-text privacy switch.

Three classes of column:

* **secret**: never shown, in any mode (auth tokens, encrypted API keys, and
  anything whose name looks like a credential).
* **raw text**: user content (messages, facts, drafts, names...). Shown only
  when ``show_raw_text`` is on.
* **metadata**: ids, counts, statuses, timestamps. Always shown.

Anything not classified is treated as raw text, so a new column fails closed.
Redaction happens in the SELECT itself so secret values never leave SQLite.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

REDACTED = "[redacted]"
HIDDEN = "[hidden]"

SECRET_COLUMN_PATTERN = re.compile(r"token|secret|password|api_?key", re.IGNORECASE)

SECRET_COLUMNS: dict[str, set[str]] = {
    "auth_tokens": {"token"},
    "couple_config": {"config_value"},
}

METADATA_COLUMNS: dict[str, set[str]] = {
    "users": {"tg_id", "partner_id", "lang", "paused", "created_at"},
    "facts": {
        "id", "owner_id", "visibility", "category", "fact_type", "kind", "status",
        "expires_at", "archived_at", "shared_at", "supersedes_id",
        "created_at", "updated_at",
    },
    "pending": {
        "id", "sender_id", "receiver_id", "status", "send_after", "created_at",
        "sent_at", "feedback", "acknowledged_at",
    },
    "messages": {"id", "user_id", "role", "created_at"},
    "usage": {"date", "model", "count"},
    "llm_calls": {"id", "created_at", "task", "model", "pack_hash", "prompt_sha256"},
    "pack_snapshots": {
        "pack_hash", "name", "version", "engine_contract", "files_json",
        "first_seen_at",
    },
    "couple_config": {"couple_id", "config_key", "created_at", "updated_at"},
    "pending_batch": {"id", "sender_id", "receiver_id", "status", "send_after", "created_at"},
    "pending_batch_items": {"id", "batch_id", "status", "created_at"},
    "checkins": {
        "id", "couple_id", "week_start", "partner_a_id", "partner_b_id",
        "status", "created_at",
    },
    "rate_limit": {"user_id", "window", "count", "reset_at"},
    "user_profiles": {
        "user_id", "default_voice", "voice_intensity", "dampening_base",
        "onboarding_done", "onboarding_step", "message_count", "created_at",
        "updated_at",
    },
    "user_mood_styles": {
        "id", "user_id", "voice_override", "dampening_override",
        "response_length", "set_at", "expires_at",
    },
    "partner_perceptions": {
        "id", "user_id", "about_id", "sentiment", "confidence", "created_at",
        "updated_at",
    },
    "user_patterns": {
        "id", "user_id", "pattern_type", "frequency", "first_seen", "last_seen",
    },
    "interaction_scores": {
        "id", "couple_id", "user_id", "interaction_type", "source", "created_at",
    },
    "user_goals": {"id", "user_id", "status", "created_at", "updated_at"},
    "shared_goals": {
        "id", "couple_id", "created_by", "status", "created_at", "updated_at",
    },
    "conflicts": {
        "id", "couple_id", "status", "opened_at", "resolved_at", "relay_ids",
    },
    "timeline_events": {"id", "couple_id", "event_type", "created_at"},
    "fact_summaries": {"id", "user_id", "year_month", "fact_count", "created_at"},
    "auth_tokens": {"user_id", "created_at"},
    "rest_outbox": {"id", "user_id", "kind", "created_at"},
    "memory_versions": {
        "id", "fact_id", "version", "status", "op", "source_msg_id", "created_at",
    },
    "memory_op_log": {
        "id", "user_id", "message_id", "outcome", "fact_id", "created_at",
    },
    "user_meta": {"user_id", "key", "updated_at"},
}

# Known free-text columns (shown only with show_raw_text). Used by the schema
# coverage test; unknown columns are treated the same way at runtime.
RAW_TEXT_COLUMNS: dict[str, set[str]] = {
    "users": {"name"},
    "facts": {"text", "source"},
    "pending": {"draft", "edited_draft"},
    "messages": {"content"},
    "pending_batch_items": {"draft", "edited_draft"},
    "checkins": {"partner_a_answers", "partner_b_answers", "summary"},
    "user_profiles": {
        "relationship_type", "duration_years", "has_kids", "age_range",
        "love_story_brief", "learned_voice",
    },
    "user_mood_styles": {"mood"},
    "partner_perceptions": {"theme"},
    "user_patterns": {"pattern"},
    "user_goals": {"text", "progress"},
    "shared_goals": {"text", "progress"},
    "conflicts": {"theme"},
    "timeline_events": {"summary", "metadata"},
    "fact_summaries": {"summary_text"},
    "rest_outbox": {"payload"},
    "memory_versions": {"text", "reason"},
    "memory_op_log": {"op_json", "reason"},
    "user_meta": {"value"},
}


def quote_ident(name: str) -> str:
    """Quote an SQL identifier (callers must still validate against the schema)."""
    return '"' + name.replace('"', '""') + '"'


def is_secret(table: str, column: str) -> bool:
    return column in SECRET_COLUMNS.get(table, ()) or bool(
        SECRET_COLUMN_PATTERN.search(column)
    )


def is_metadata(table: str, column: str) -> bool:
    return column in METADATA_COLUMNS.get(table, ())


def select_expr(table: str, columns: Iterable[str], show_raw: bool) -> str:
    """Return a SELECT column list that never exposes secrets.

    Secret columns become the literal ``'[redacted]'``; raw-text and unknown
    columns become ``'[hidden]'`` when *show_raw* is false.
    """
    parts = []
    for col in columns:
        q = quote_ident(col)
        if is_secret(table, col):
            parts.append(f"'{REDACTED}' AS {q}")
        elif show_raw or is_metadata(table, col):
            parts.append(q)
        else:
            parts.append(f"'{HIDDEN}' AS {q}")
    return ", ".join(parts)


def redact_row(table: str, row: dict[str, Any], show_raw: bool) -> dict[str, Any]:
    """Python-side equivalent of :func:`select_expr` for an already-fetched row."""
    out: dict[str, Any] = {}
    for col, val in row.items():
        if is_secret(table, col):
            out[col] = REDACTED
        elif show_raw or is_metadata(table, col):
            out[col] = val
        else:
            out[col] = HIDDEN
    return out
