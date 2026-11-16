"""LLM client — protocol + Gemini implementation with fallback and backoff."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Protocol, runtime_checkable

from google import genai
from google.genai import types as genai_types

from couples_bot.config import Config
from couples_bot import db as db_mod

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Response type
# ---------------------------------------------------------------------------

LLMResponse = dict[str, Any]
"""Expected shape: {"reply": str, "new_facts": list[dict], "relay_draft": str|None}"""


def parse_llm_response(raw: str) -> LLMResponse:
    """Parse LLM JSON output, with graceful fallback."""
    try:
        data = json.loads(raw)
        return {
            "reply": data.get("reply", raw),
            "new_facts": data.get("new_facts", []),
            "relay_draft": data.get("relay_draft"),
        }
    except (json.JSONDecodeError, TypeError):
        return {"reply": raw, "new_facts": [], "relay_draft": None}


# ---------------------------------------------------------------------------
# Protocol (for testing / mocking)
# ---------------------------------------------------------------------------

@runtime_checkable
class LLMClient(Protocol):
    """Abstract interface for LLM calls."""

    async def generate(
        self,
        user_content: str,
        system_prompt: str,
        model: str | None = None,
    ) -> LLMResponse: ...


# ---------------------------------------------------------------------------
# Gemini implementation
# ---------------------------------------------------------------------------

def _get_error_code(exc: Exception) -> int:
    """Best-effort extraction of HTTP status code from an exception."""
    for attr in ("code", "status_code", "status"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val
    msg = str(exc)
    for code in (429, 404, 503):
        if str(code) in msg:
            return code
    return 0


class GeminiClient:
    """Concrete LLM client backed by the google-genai SDK."""

    def __init__(self, config: Config, db_path: str) -> None:
        self._client = genai.Client(api_key=config.gemini_api_key)
        self._config = config
        self._db_path = db_path

    # ----- public -----

    async def generate(
        self,
        user_content: str,
        system_prompt: str,
        model: str | None = None,
    ) -> LLMResponse:
        """Call Gemini and return parsed JSON response.

        If *model* is ``None`` the default model is used.  When a model
        in *config.model_heavy* is requested the daily cap is checked
        and fallback models are tried on 404/503.
        """
        model = model or self._config.model_default
        models_to_try = self._build_model_list(model)

        for candidate in models_to_try:
            try:
                raw = await self._call(candidate, user_content, system_prompt)
                await db_mod.increment_usage(self._db_path, candidate)
                return parse_llm_response(raw)
            except Exception as exc:
                code = _get_error_code(exc)
                if code == 429:
                    delay = self._parse_retry_delay(exc)
                    logger.warning("429 on %s — backing off %ss", candidate, delay)
                    await asyncio.sleep(delay)
                    # Retry once on the same model
                    try:
                        raw = await self._call(candidate, user_content, system_prompt)
                        await db_mod.increment_usage(self._db_path, candidate)
                        return parse_llm_response(raw)
                    except Exception:
                        logger.warning("Retry on %s also failed, trying next", candidate)
                        continue
                elif code in (404, 503):
                    logger.warning("%d on %s — trying next model", code, candidate)
                    continue
                else:
                    raise

        # All models exhausted — return a safe fallback
        return {
            "reply": "I'm having trouble connecting right now. Please try again in a moment.",
            "new_facts": [],
            "relay_draft": None,
        }

    async def check_usage_warning(self, model: str) -> str | None:
        """Return a warning string if usage is approaching the cap, else None."""
        if model not in self._config.model_heavy:
            return None
        count = await db_mod.get_usage_count(self._db_path, model)
        cap = self._config.heavy_daily_cap
        if count >= cap - 2:  # warn at cap-2
            return f"⚠️ Heavy model usage: {count}/{cap} today."
        return None

    # ----- private -----

    def _build_model_list(self, requested: str) -> list[str]:
        """Return the ordered list of models to try."""
        if requested in self._config.model_heavy:
            # Start with requested, then the rest of the heavy list
            others = [m for m in self._config.model_heavy if m != requested]
            return [requested, *others]
        return [requested]

    async def _call(
        self, model: str, user_content: str, system_prompt: str
    ) -> str:
        config = genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
            response_mime_type="application/json",
        )
        response = await self._client.aio.models.generate_content(
            model=model,
            contents=user_content,
            config=config,
        )
        return response.text or ""

    @staticmethod
    def _parse_retry_delay(exc: Exception) -> float:
        """Try to read a retry delay from the exception; default to 60s."""
        for attr in ("retry_delay", "retry_after"):
            val = getattr(exc, attr, None)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
        return 60.0
