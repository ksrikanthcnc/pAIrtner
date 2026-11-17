"""LLM client — protocol + Gemini and OpenAI-compatible implementations."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

from google import genai
from google.genai import types as genai_types
from openai import AsyncOpenAI

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
            "detected_mood": data.get("detected_mood"),
            "patterns_observed": data.get("patterns_observed", []),
            "partner_themes": data.get("partner_themes", []),
            "articulation_note": data.get("articulation_note"),
            "horsemen_detected": data.get("horsemen_detected", []),
            "voice_traits": data.get("voice_traits", {}),
        }
    except (json.JSONDecodeError, TypeError):
        return {
            "reply": raw,
            "new_facts": [],
            "relay_draft": None,
            "detected_mood": None,
            "patterns_observed": [],
            "partner_themes": [],
            "articulation_note": None,
            "horsemen_detected": [],
            "voice_traits": {},
        }


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
        api_key_override: str | None = None,
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


def parse_retry_delay(exc: Exception) -> float:
    """Try to read a retry delay from the exception; default to 60s."""
    for attr in ("retry_delay", "retry_after"):
        val = getattr(exc, attr, None)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    return 60.0


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
        api_key_override: str | None = None,
    ) -> LLMResponse:
        """Call Gemini and return parsed JSON response.

        If *model* is ``None`` the default model is used.  When a model
        in *config.model_heavy* is requested the daily cap is checked
        and fallback models are tried on 404/503.

        *api_key_override*: when provided, a temporary genai Client is
        created with this key for the call (BYO key support).
        """
        model = model or self._config.model_default
        models_to_try = self._build_model_list(model)

        for candidate in models_to_try:
            try:
                raw = await self._call(
                    candidate, user_content, system_prompt,
                    api_key_override=api_key_override,
                )
                await db_mod.increment_usage(self._db_path, candidate)
                return parse_llm_response(raw)
            except Exception as exc:
                code = _get_error_code(exc)
                if code == 429:
                    delay = parse_retry_delay(exc)
                    logger.warning("429 on %s — backing off %ss", candidate, delay)
                    await asyncio.sleep(delay)
                    # Retry once on the same model
                    try:
                        raw = await self._call(
                            candidate, user_content, system_prompt,
                            api_key_override=api_key_override,
                        )
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
        self, model: str, user_content: str, system_prompt: str,
        *, api_key_override: str | None = None,
    ) -> str:
        client = self._client
        if api_key_override:
            client = genai.Client(api_key=api_key_override)

        config = genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
            response_mime_type="application/json",
        )
        response = await client.aio.models.generate_content(
            model=model,
            contents=user_content,
            config=config,
        )
        return response.text or ""


# ---------------------------------------------------------------------------
# OpenAI-compatible implementation
# ---------------------------------------------------------------------------

class OpenAICompatClient:
    """LLM client for any OpenAI-compatible API (Groq, Together, etc.)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        default_model: str,
        db_path: str,
        model_heavy: list[str] | None = None,
        heavy_daily_cap: int = 10,
        reasoning_effort: str = "",
    ) -> None:
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        # e.g. "none" turns off thinking on Qwen3 via Ollama; empty = don't send
        self._extra_body = (
            {"reasoning_effort": reasoning_effort} if reasoning_effort else None
        )
        self._default_model = default_model
        self._model_heavy = model_heavy or []
        self._db_path = db_path
        self._heavy_daily_cap = heavy_daily_cap

    # ----- public -----

    async def generate(
        self,
        user_content: str,
        system_prompt: str,
        model: str | None = None,
        api_key_override: str | None = None,
    ) -> LLMResponse:
        """Generate a response using an OpenAI-compatible API.

        *api_key_override* is accepted for Protocol compliance but unused;
        OpenAI-compatible clients use the key configured at construction.
        """
        model = model or self._default_model
        models_to_try = self._build_model_list(model)

        for candidate in models_to_try:
            try:
                response = await self._client.chat.completions.create(
                    model=candidate,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    response_format={"type": "json_object"},
                    extra_body=self._extra_body,
                )
                raw = response.choices[0].message.content or ""
                await db_mod.increment_usage(self._db_path, candidate)
                return parse_llm_response(raw)
            except Exception as exc:
                code = _get_error_code(exc)
                if code == 429:
                    delay = parse_retry_delay(exc)
                    logger.warning("429 on %s — backing off %ss", candidate, delay)
                    await asyncio.sleep(delay)
                    # Retry once on the same model
                    try:
                        response = await self._client.chat.completions.create(
                            model=candidate,
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_content},
                            ],
                            response_format={"type": "json_object"},
                            extra_body=self._extra_body,
                        )
                        raw = response.choices[0].message.content or ""
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

        return {
            "reply": "I'm having trouble connecting right now. Please try again in a moment.",
            "new_facts": [],
            "relay_draft": None,
        }

    async def check_usage_warning(self, model: str) -> str | None:
        """Return a warning string if usage is approaching the cap, else None."""
        if model not in self._model_heavy:
            return None
        count = await db_mod.get_usage_count(self._db_path, model)
        cap = self._heavy_daily_cap
        if count >= cap - 2:  # warn at cap-2
            return f"⚠️ Heavy model usage: {count}/{cap} today."
        return None

    # ----- private -----

    def _build_model_list(self, requested: str) -> list[str]:
        if requested in self._model_heavy:
            others = [m for m in self._model_heavy if m != requested]
            return [requested, *others]
        return [requested]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def describe_llm(config: Config) -> str:
    """Human-readable description of where LLM calls go. Never includes keys."""
    if config.llm_provider != "openai_compat":
        return "Google Gemini"
    host = (urlparse(config.llm_base_url).hostname or "").lower()
    model = config.llm_model_default or config.model_default
    if _is_local_host(host):
        return f"a local model ({model}) on your own network"
    return f"an external service at {host or 'unknown host'}"


def _is_local_host(host: str) -> bool:
    if host in ("localhost", "host.docker.internal"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def create_llm_client(config: Config, db_path: str) -> LLMClient:
    """Return the right LLM client based on config.llm_provider."""
    if config.llm_provider == "openai_compat":
        return OpenAICompatClient(
            base_url=config.llm_base_url,
            api_key=config.llm_api_key,
            default_model=config.llm_model_default or config.model_default,
            db_path=db_path,
            model_heavy=config.llm_model_heavy or config.model_heavy,
            heavy_daily_cap=config.heavy_daily_cap,
            reasoning_effort=config.llm_reasoning_effort,
        )
    return GeminiClient(config, db_path)
