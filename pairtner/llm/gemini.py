"""Gemini LLM client implementation."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from google import genai
from google.genai import types as genai_types

from pairtner import db as db_mod
from pairtner.config import Config
from pairtner.llm.base import (
    LLMResponse,
    _get_error_code,
    _make_fallback,
    _to_gemini_tools,
    _with_tool_calls,
    parse_llm_response,
    parse_retry_delay,
)

logger = logging.getLogger(__name__)


class GeminiClient:
    """Concrete LLM client backed by the google-genai SDK."""

    def __init__(self, config: Config, db_path: str) -> None:
        self._client = genai.Client(api_key=config.gemini_api_key)
        self._config = config
        self._db_path = db_path

    async def generate(
        self,
        user_content: str,
        system_prompt: str,
        model: str | None = None,
        api_key_override: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        model = model or self._config.model_default
        models_to_try = self._build_model_list(model)
        _last_code = 0

        for candidate in models_to_try:
            try:
                raw, tcalls = await self._call_ex(
                    candidate, user_content, system_prompt,
                    api_key_override=api_key_override, tools=tools,
                )
                await db_mod.increment_usage(self._db_path, candidate)
                return _with_tool_calls(parse_llm_response(raw), tcalls)
            except Exception as exc:
                code = _get_error_code(exc)
                if code == 429:
                    delay = parse_retry_delay(exc)
                    logger.warning("429 on %s — backing off %ss", candidate, delay)
                    await asyncio.sleep(delay)
                    try:
                        raw, tcalls = await self._call_ex(
                            candidate, user_content, system_prompt,
                            api_key_override=api_key_override, tools=tools,
                        )
                        await db_mod.increment_usage(self._db_path, candidate)
                        return _with_tool_calls(parse_llm_response(raw), tcalls)
                    except Exception:
                        logger.warning("Retry on %s also failed, trying next", candidate)
                        continue
                elif code in (404, 503):
                    logger.warning("%d on %s — trying next model", code, candidate)
                    continue
                else:
                    raise

        return _make_fallback("gemini", models_to_try[0] if models_to_try else "",
                              last_code=_last_code)

    async def check_usage_warning(self, model: str) -> str | None:
        if model not in self._config.model_heavy:
            return None
        count = await db_mod.get_usage_count(self._db_path, model)
        cap = self._config.heavy_daily_cap
        if count >= cap - 2:
            return f"⚠️ Heavy model usage: {count}/{cap} today."
        return None

    def _build_model_list(self, requested: str) -> list[str]:
        if requested in self._config.model_heavy:
            others = [m for m in self._config.model_heavy if m != requested]
            return [requested, *others]
        return [requested]

    async def _call_ex(
        self, model: str, user_content: str, system_prompt: str,
        *, api_key_override: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Call Gemini; return (text, tool_calls in OpenAI shape)."""
        client = self._client
        if api_key_override:
            client = genai.Client(api_key=api_key_override)

        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = _to_gemini_tools(tools)
        else:
            kwargs["response_mime_type"] = "application/json"
        config = genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
            **kwargs,
        )
        response = await client.aio.models.generate_content(
            model=model,
            contents=user_content,
            config=config,
        )
        tool_calls: list[dict[str, Any]] = []
        if tools:
            for i, fc in enumerate(getattr(response, "function_calls", None) or []):
                tool_calls.append({
                    "id": getattr(fc, "id", None) or f"call_{i}",
                    "type": "function",
                    "function": {
                        "name": fc.name,
                        "arguments": json.dumps(dict(fc.args or {})),
                    },
                })
        text = (response.text or "") if not tool_calls else ""
        if tools and text.strip().startswith("```"):
            text = text.strip().strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
            text = text.strip()
        return text, tool_calls
