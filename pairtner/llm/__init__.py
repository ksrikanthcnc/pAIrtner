"""LLM client package — protocol, response parsing, Gemini + OpenAI-compat clients.

All public names are re-exported here so ``from pairtner.llm import X``
continues to work unchanged after the split from the monolithic llm.py.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import sqlite3
from typing import Any, Callable
from urllib.parse import urlparse

import aiosqlite

from pairtner import db as db_mod
from pairtner import pack
from pairtner.config import Config

# Re-export everything that callers import from ``pairtner.llm``.
from pairtner.llm.base import (  # noqa: F401 — re-export
    FALLBACK_REPLY,
    LLMClient,
    LLMResponse,
    _get_error_code,
    _make_fallback,
    _strip_fences,
    _with_tool_calls,
    get_reply_text,
    parse_llm_response,
    parse_retry_delay,
)
from pairtner.llm.gemini import GeminiClient  # noqa: F401
from pairtner.llm.openai_compat import OpenAICompatClient  # noqa: F401

logger = logging.getLogger(__name__)
trace_logger = logging.getLogger("pairtner.trace")


# ---------------------------------------------------------------------------
# Tracing wrapper
# ---------------------------------------------------------------------------

class TracingLLM:
    """Wrap an LLM client and trace which prompt pack built each call.

    Reads the ``(task, snapshot)`` that prompt assembly stored in
    ``pack.last_trace``, so the hash is the one active at assembly time even
    if a hot reload happened before ``generate``.  Logs one ``llm_call`` line
    and writes ``llm_calls`` / ``pack_snapshots`` rows (hashes only, no prompt
    text, no user id).  Database errors are logged and swallowed.
    """

    def __init__(
        self,
        inner: Any,
        db_path: str,
        on_call: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._inner = inner
        self._db_path = db_path
        self._on_call = on_call

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def generate(
        self,
        user_content: str,
        system_prompt: str,
        model: str | None = None,
        api_key_override: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        traced = pack.last_trace.get()
        if traced is None:
            task, snap = "unknown", pack.current().snapshot()
        else:
            task, snap = traced
        prompt_sha = hashlib.sha256(
            (system_prompt + "\x00" + user_content).encode("utf-8")
        ).hexdigest()
        model_name = model or getattr(
            getattr(self._inner, "_config", None), "model_default", None,
        ) or getattr(self._inner, "_default_model", None)
        trace_logger.info(
            "llm_call task=%s model=%s pack=%s@%s pack_hash=%s prompt_sha=%s",
            task, model_name, snap["name"], snap["version"],
            snap["pack_hash"][:12], prompt_sha[:12],
        )
        record = {
            "task": task, "model": model_name, "pack": snap,
            "prompt_sha256": prompt_sha,
        }
        try:
            await db_mod.record_pack_snapshot(
                self._db_path, snap["pack_hash"], snap["name"], snap["version"],
                pack.ENGINE_CONTRACT, snap["files"],
            )
            await db_mod.record_llm_call(
                self._db_path, task, model_name, snap["pack_hash"], prompt_sha,
            )
        except (sqlite3.Error, aiosqlite.Error) as exc:
            logger.warning("could not record llm_call trace: %s", exc)
        if tools:
            result = await self._inner.generate(
                user_content, system_prompt, model, api_key_override, tools=tools,
            )
        else:
            result = await self._inner.generate(
                user_content, system_prompt, model, api_key_override,
            )
        if self._on_call is not None:
            self._on_call({
                **record, "system": system_prompt,
                "user_content": user_content, "result": result,
            })
        return result


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def describe_llm(config: Config) -> str:
    """Human-readable description of where LLM calls go. Never includes keys."""
    if config.llm_provider != "openai_compat":
        if config.llm_base_url:
            # Both providers available
            host = (urlparse(config.llm_base_url).hostname or "").lower()
            model = config.llm_model_default or config.model_default
            local = f"a local model ({model})" if _is_local_host(host) else f"{host}"
            return f"{local} + Google Gemini (use /model to switch)"
        return "Google Gemini"
    host = (urlparse(config.llm_base_url).hostname or "").lower()
    model = config.llm_model_default or config.model_default
    if _is_local_host(host):
        base = f"a local model ({model}) on your own network"
    else:
        base = f"an external service at {host or 'unknown host'}"
    if config.gemini_api_key:
        return f"{base} + Google Gemini (use /model to switch)"
    return base


def _is_local_host(host: str) -> bool:
    if host in ("localhost", "host.docker.internal"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def create_llm_client(config: Config, db_path: str) -> LLMClient:
    """Return the right LLM client (wrapped in call tracing)."""
    return TracingLLM(_create_inner_client(config, db_path), db_path)


# ---------------------------------------------------------------------------
# Multi-provider router
# ---------------------------------------------------------------------------

# Gemini model prefixes (all Google models)
_GEMINI_PREFIXES = (
    "gemini-", "gemini_", "gemini/",
    "models/gemini", "gemma-",
)


class MultiProviderLLM:
    """Routes generate() calls to Ollama or Gemini based on model name.

    Holds both clients (if configured). Model routing:
    - ``gemini:flash-2`` → strips prefix, routes to GeminiClient
    - ``gemini-3.1-flash-lite`` → routes to GeminiClient (native name)
    - Anything else → OpenAICompatClient (Ollama)
    """

    def __init__(
        self,
        ollama: OpenAICompatClient | None,
        gemini: GeminiClient | None,
        default_provider: str,  # "ollama" or "gemini"
    ) -> None:
        self._ollama = ollama
        self._gemini = gemini
        self._default_provider = default_provider

    def _route(self, model: str | None) -> tuple[Any, str | None]:
        """Return (client, cleaned_model_name) for the given model."""
        if model:
            low = model.lower()
            # Explicit prefix: "gemini:model-name"
            if low.startswith("gemini:"):
                name = model[7:]  # strip "gemini:" prefix
                if self._gemini:
                    return self._gemini, name or None
                raise RuntimeError("Gemini not configured — set GEMINI_API_KEY")
            # Explicit prefix: "ollama:model-name"
            if low.startswith("ollama:"):
                name = model[7:]  # strip "ollama:" prefix
                if self._ollama:
                    return self._ollama, name or None
                raise RuntimeError("Ollama not configured — set LLM_BASE_URL")
            # Native Gemini model names
            if any(low.startswith(p) for p in _GEMINI_PREFIXES):
                if self._gemini:
                    return self._gemini, model
                raise RuntimeError("Gemini not configured — set GEMINI_API_KEY")
        # Default provider
        if self._default_provider == "gemini" and self._gemini:
            return self._gemini, model
        if self._ollama:
            return self._ollama, model
        if self._gemini:
            return self._gemini, model
        raise RuntimeError("No LLM provider configured")

    async def generate(
        self,
        user_content: str,
        system_prompt: str,
        model: str | None = None,
        api_key_override: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        client, clean_model = self._route(model)
        if tools:
            return await client.generate(
                user_content, system_prompt, clean_model, api_key_override, tools=tools)
        return await client.generate(
            user_content, system_prompt, clean_model, api_key_override)

    @property
    def providers(self) -> list[str]:
        """List available provider names."""
        out = []
        if self._ollama:
            out.append("ollama")
        if self._gemini:
            out.append("gemini")
        return out

    async def validate_model(self, model: str) -> str | None:
        """Test if a model is reachable. Returns None on success, error string on failure."""
        try:
            client, clean_model = self._route(model)
        except RuntimeError as e:
            return str(e)
        # Quick validation: try a minimal generate and catch connection errors
        try:
            await client.generate("test", "Reply with just 'ok'.", clean_model)
            return None
        except Exception as e:
            provider = "Gemini" if any(model.lower().startswith(p) for p in _GEMINI_PREFIXES + ("gemini:",)) else "Ollama"
            return f"{provider} error: {type(e).__name__}: {e}"


def _create_inner_client(config: Config, db_path: str) -> Any:
    """Return the right LLM client based on config.llm_provider.

    When BOTH Ollama and Gemini are configured, returns a MultiProviderLLM
    that routes by model name prefix. Otherwise returns the single configured client.
    """
    ollama_client = None
    gemini_client = None

    # Build Ollama client if configured
    if config.llm_base_url:
        ollama_client = OpenAICompatClient(
            base_url=config.llm_base_url,
            api_key=config.llm_api_key,
            default_model=config.llm_model_default or config.model_default,
            db_path=db_path,
            model_heavy=config.llm_model_heavy or config.model_heavy,
            heavy_daily_cap=config.heavy_daily_cap,
            reasoning_effort=config.llm_reasoning_effort,
        )

    # Build Gemini client if configured
    if config.gemini_api_key:
        gemini_client = GeminiClient(config, db_path)

    # If both are available, use the multi-provider router
    if ollama_client and gemini_client:
        default = "ollama" if config.llm_provider == "openai_compat" else "gemini"
        logger.info("Multi-provider LLM: ollama + gemini (default: %s)", default)
        return MultiProviderLLM(ollama_client, gemini_client, default)

    # Single provider
    if config.llm_provider == "openai_compat" and ollama_client:
        return ollama_client
    if gemini_client:
        return gemini_client

    # Fallback: create based on provider setting
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
