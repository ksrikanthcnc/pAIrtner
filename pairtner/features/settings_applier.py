"""Apply LLM-proposed settings_ops (whitelisted) into user_meta as ``setting:<key>``."""

from __future__ import annotations

import logging
from typing import Any

from pairtner.core.memory import (
    ALLOWED_SETTINGS,
    SETTINGS_META_PREFIX,
    validate_setting,
)
from pairtner.storage import facts as facts_mod

logger = logging.getLogger(__name__)

SETTINGS_WHITELIST: tuple[str, ...] = tuple(ALLOWED_SETTINGS)


async def apply_settings_ops(db_path: str, user_id: int, settings_ops: list) -> list[dict]:
    """Validate and apply settings ops (dicts or SettingsOp-like objects).

    Returns one result per op: ``{"key", "value", "status": "applied"|"rejected"}``.
    """
    results: list[dict[str, Any]] = []
    for raw in settings_ops or []:
        if isinstance(raw, dict):
            key, value = raw.get("key"), raw.get("value")
        else:
            key, value = getattr(raw, "key", None), getattr(raw, "value", None)
        if key is None or value is None:
            results.append({"key": key, "value": value, "status": "rejected"})
            continue
        valid = validate_setting(key, value)
        if valid is None:
            logger.info("Rejected settings op %s=%r", key, value)
            results.append({"key": str(key), "value": str(value), "status": "rejected"})
            continue
        await facts_mod.set_user_meta(db_path, user_id, SETTINGS_META_PREFIX + valid.key, valid.value)
        results.append({"key": valid.key, "value": valid.value, "status": "applied"})
    return results
