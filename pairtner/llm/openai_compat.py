"""OpenAI-compatible LLM client (Ollama, Groq, Together, etc.)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from openai import AsyncOpenAI

from pairtner import db as db_mod
from pairtner.llm.base import (
    LLMResponse,
    _get_error_code,
    _make_fallback,
    _with_tool_calls,
    parse_llm_response,
    parse_retry_delay,
)

logger = logging.getLogger(__name__)


class OpenAICompatClient:
    """LLM client for any OpenAI-compatible API (Ollama, Groq, etc.)."""

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
        self._extra_body = (
            {"reasoning_effort": reasoning_effort} if reasoning_effort else None
        )
        self._default_model = default_model
        self._model_heavy = model_heavy or []
        self._db_path = db_path
        self._heavy_daily_cap = heavy_daily_cap

    async def generate(
        self,
        user_content: str,
        system_prompt: str,
        model: str | None = None,
        api_key_override: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
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
                    **self._extra_kwargs(tools),
                )
                raw, tcalls = self._unpack(response)
                await db_mod.increment_usage(self._db_path, candidate)
                return _with_tool_calls(parse_llm_response(raw), tcalls)
            except Exception as exc:
                code = _get_error_code(exc)
                if code == 429:
                    delay = parse_retry_delay(exc)
                    logger.warning("429 on %s — backing off %ss", candidate, delay)
                    await asyncio.sleep(delay)
                    try:
                        response = await self._client.chat.completions.create(
                            model=candidate,
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_content},
                            ],
                            **self._extra_kwargs(tools),
                        )
                        raw, tcalls = self._unpack(response)
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

        return _make_fallback("openai_compat", models_to_try[0] if models_to_try else "")

    async def check_usage_warning(self, model: str) -> str | None:
        if model not in self._model_heavy:
            return None
        count = await db_mod.get_usage_count(self._db_path, model)
        cap = self._heavy_daily_cap
        if count >= cap - 2:
            return f"⚠️ Heavy model usage: {count}/{cap} today."
        return None

    def _extra_kwargs(self, tools: list[dict[str, Any]] | None) -> dict[str, Any]:
        kw: dict[str, Any] = {"extra_body": self._extra_body}
        if tools:
            kw["tools"] = tools
        else:
            kw["response_format"] = {"type": "json_object"}
        return kw

    @staticmethod
    def _unpack(response: Any) -> tuple[str, list[dict[str, Any]]]:
        msg = response.choices[0].message
        tcalls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments or "{}",
                },
            }
            for tc in (getattr(msg, "tool_calls", None) or [])
        ]
        return (msg.content or ""), tcalls

    def _build_model_list(self, requested: str) -> list[str]:
        if requested in self._model_heavy:
            others = [m for m in self._model_heavy if m != requested]
            return [requested, *others]
        return [requested]
