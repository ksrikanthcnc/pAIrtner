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


async def probe_model(llm: Any, provider: str = "", model: str = "") -> dict[str, Any]:
    """Run a quick capability probe. Returns {capability, json_ok, topics_found, latency_ms, context_length}."""
    # Try to detect context window size from the provider API
    context_length = await _detect_context_length(provider, model)

    start = time.monotonic()
    try:
        result = await llm.generate(_PROBE_INPUT, _PROBE_SYSTEM)
        latency_ms = int((time.monotonic() - start) * 1000)
    except Exception as exc:
        logger.warning("Model probe failed: %s", exc)
        return {"capability": "low", "json_ok": False, "topics_found": 0,
                "latency_ms": 0, "context_length": context_length, "error": str(exc)}

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
        "context_length": context_length,
    }


async def _detect_context_length(provider: str, model: str) -> int:
    """Try to detect the model's context window size from the provider API.

    Returns 0 if unknown. Known sources:
    - Ollama: GET /api/show {name: model} -> model_info.context_length
    - OpenAI-compat: typically not exposed
    - Gemini: models.get -> inputTokenLimit
    """
    import urllib.request

    if not model:
        return 0

    # Ollama
    if provider in ("ollama", "") and not provider.startswith("gemini"):
        try:
            import json as _json
            req = urllib.request.Request(
                "http://localhost:11434/api/show",
                data=_json.dumps({"name": model}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = _json.loads(resp.read())
            # Ollama returns context length in model_info or parameters
            ctx = data.get("model_info", {}).get("context_length")
            if not ctx:
                # Also check parameters string for num_ctx
                params = data.get("parameters", "")
                if "num_ctx" in params:
                    for line in params.split("\n"):
                        if "num_ctx" in line:
                            ctx = int(line.split()[-1])
            if ctx and isinstance(ctx, int):
                return ctx
        except Exception:
            pass

    return 0


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

        async with db.execute(
            "SELECT updated_at FROM user_meta WHERE user_id = ? AND key = 'model_capability'",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        cached_ts = dict(row)["updated_at"] if row else None

    # Use cache if model hasn't changed and probe is <30 days old
    if cached_cap and cached_model == current_model and cached_cap in ("low", "medium", "high"):
        if cached_ts:
            from datetime import datetime, timezone
            try:
                ts = datetime.fromisoformat(cached_ts)
                age_days = (datetime.now(timezone.utc) - ts.replace(tzinfo=timezone.utc)).days
                if age_days > 10:
                    logger.info("Model probe cache expired (%d days), re-probing", age_days)
                else:
                    return cached_cap
            except (ValueError, TypeError):
                return cached_cap
        else:
            return cached_cap

    # Probe
    result = await probe_model(llm, provider="", model=current_model)
    capability = result["capability"]
    context_length = result.get("context_length", 0)
    logger.info(
        "Model probe: capability=%s json_ok=%s topics=%d/%d latency=%dms ctx=%d model=%s",
        capability, result["json_ok"], result["topics_found"],
        _EXPECTED_TOPICS, result["latency_ms"], context_length, current_model,
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
        if context_length > 0:
            await db.execute(
                "INSERT OR REPLACE INTO user_meta (user_id, key, value, updated_at) "
                "VALUES (?, 'model_context_length', ?, CURRENT_TIMESTAMP)",
                (user_id, str(context_length)),
            )
        await db.commit()

    return capability
