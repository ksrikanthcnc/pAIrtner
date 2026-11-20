"""Auto-detect model capability at startup via a quick probe.

Sends a small structured-output test to the configured LLM and measures:
- Can it return valid JSON? (basic capability)
- How many list items does it produce from a 5-item input? (comprehensiveness)
- Response latency (speed tier)

Result: 'low', 'medium', or 'high' — stored in user_meta as 'model_capability'.
Re-probed when the model name changes.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_PROBE_SYSTEM = """Return ONLY valid JSON. No explanation.
{"items": ["list each topic from the input as one short sentence"]}"""

_PROBE_INPUT = """Topics:
1. Alice got a promotion at work
2. Bob forgot their anniversary
3. Kids need school supplies for Monday
4. The car needs an oil change
5. Alice wants to try the new Italian restaurant"""

_EXPECTED_TOPICS = 5


async def probe_model(llm: Any) -> dict[str, Any]:
    """Run a quick capability probe. Returns {capability, json_ok, topics_found, latency_ms}."""
    start = time.monotonic()
    try:
        result = await llm.generate(_PROBE_INPUT, _PROBE_SYSTEM)
        latency_ms = int((time.monotonic() - start) * 1000)
    except Exception as exc:
        logger.warning("Model probe failed: %s", exc)
        return {"capability": "low", "json_ok": False, "topics_found": 0,
                "latency_ms": 0, "error": str(exc)}

    reply = result.get("reply", "") if isinstance(result, dict) else str(result)

    # Check JSON validity
    json_ok = False
    topics_found = 0
    try:
        # Strip markdown fences if present
        text = reply.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        data = json.loads(text)
        json_ok = True
        items = data.get("items", [])
        if isinstance(items, list):
            topics_found = len([i for i in items if isinstance(i, str) and len(i) > 5])
    except (json.JSONDecodeError, TypeError, KeyError):
        # Try to find JSON in the reply
        try:
            start_idx = reply.index("{")
            end_idx = reply.rindex("}") + 1
            data = json.loads(reply[start_idx:end_idx])
            json_ok = True
            items = data.get("items", [])
            if isinstance(items, list):
                topics_found = len([i for i in items if isinstance(i, str) and len(i) > 5])
        except (ValueError, json.JSONDecodeError):
            pass

    # Classify
    if not json_ok:
        capability = "low"
    elif topics_found >= 4 and latency_ms < 5000:
        capability = "high"
    elif topics_found >= 3:
        capability = "medium"
    else:
        capability = "low"

    return {
        "capability": capability,
        "json_ok": json_ok,
        "topics_found": topics_found,
        "latency_ms": latency_ms,
    }


async def get_or_probe_capability(
    db_path: str, user_id: int, llm: Any,
    current_model: str = "",
) -> str:
    """Return cached capability or probe + cache it.

    Re-probes when the model name changes.
    """
    import aiosqlite

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT value FROM user_meta WHERE user_id = ? AND key = 'model_capability'",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        cached_cap = dict(row)["value"] if row else None

        async with db.execute(
            "SELECT value FROM user_meta WHERE user_id = ? AND key = 'model_capability_model'",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        cached_model = dict(row)["value"] if row else None

    # Use cache if model hasn't changed
    if cached_cap and cached_model == current_model and cached_cap in ("low", "medium", "high"):
        return cached_cap

    # Probe
    result = await probe_model(llm)
    capability = result["capability"]
    logger.info(
        "Model probe: capability=%s json_ok=%s topics=%d/%d latency=%dms model=%s",
        capability, result["json_ok"], result["topics_found"],
        _EXPECTED_TOPICS, result["latency_ms"], current_model,
    )

    # Cache
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO user_meta (user_id, key, value, updated_at) "
            "VALUES (?, 'model_capability', ?, CURRENT_TIMESTAMP)",
            (user_id, capability),
        )
        await db.execute(
            "INSERT OR REPLACE INTO user_meta (user_id, key, value, updated_at) "
            "VALUES (?, 'model_capability_model', ?, CURRENT_TIMESTAMP)",
            (user_id, current_model),
        )
        await db.commit()

    return capability
