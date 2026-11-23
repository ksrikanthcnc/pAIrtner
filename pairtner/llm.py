"""LLM client — protocol + Gemini and OpenAI-compatible implementations."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import sqlite3
from typing import Any, Callable, Protocol, runtime_checkable
from urllib.parse import urlparse

import aiosqlite
from google import genai
from google.genai import types as genai_types
from openai import AsyncOpenAI

from pairtner.config import Config
from pairtner import db as db_mod
from pairtner import pack

logger = logging.getLogger(__name__)
trace_logger = logging.getLogger("pairtner.trace")

# Shown to users when every model failed; also lets callers detect that case.
FALLBACK_REPLY = "I'm having trouble connecting right now. Please try again in a moment."

def _make_fallback(provider: str = "", model: str = "", last_code: int = 0) -> dict:
    """Build a fallback response with a more helpful error message."""
    if last_code == 503:
        msg = "⚡ The AI model is temporarily busy (503). Please try again in a moment."
    elif last_code == 429:
        msg = "⚡ Rate limit hit — too many requests. Please wait a minute and try again."
    elif last_code == 404:
        msg = f"⚠️ Model '{model}' not found (404). Check your MODEL_DEFAULT setting."
    elif provider == "openai_compat":
        msg = (
            "I couldn't reach the local LLM. "
            "Check that Ollama is running (`ollama serve`) "
            f"and the model is pulled{f' (`ollama pull {model}`)' if model else ''}."
        )
    elif provider == "gemini":
        msg = (
            "I couldn't reach Gemini. "
            "Check that GEMINI_API_KEY is set in your .env "
            "and you have API quota remaining."
        )
    else:
        msg = FALLBACK_REPLY
    return {
        "reply": msg,
        "memory_ops": [],
        "new_facts": [],
        "relay_draft": None,
    }

# ---------------------------------------------------------------------------
# Response type
# ---------------------------------------------------------------------------

LLMResponse = dict[str, Any]
"""Expected shape: {"reply": str, "memory_ops": list[dict], "new_facts": list[dict], "relay_draft": str|None}"""


def _strip_fences(raw: str) -> str:
    """Remove markdown code fences (```json ... ```) wrapping a JSON blob."""
    s = raw.strip()
    if s.startswith("```"):
        # Remove opening fence + optional language tag
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _extract_json_from_mixed(raw: str) -> tuple[str, str]:
    """Split a mixed text+JSON response into (reply_text, json_str).

    Handles: "reply text\\n```json\\n{...}\\n```" patterns where the LLM
    puts its conversational reply before the JSON block.
    Returns (reply_text, json_string) or (raw, "") if no JSON found.
    """
    import re
    # Match ```json ... ``` block anywhere in the text
    m = re.search(r'```(?:json)?\s*\n(\{.*?\})\s*```', raw, re.DOTALL)
    if m:
        json_str = m.group(1)
        reply_text = raw[:m.start()].strip()
        return reply_text, json_str
    # Match a bare JSON object at the end of the text
    m = re.search(r'\n(\{[^{]*"(?:memory_ops|reply)".*\})\s*$', raw, re.DOTALL)
    if m:
        json_str = m.group(1)
        reply_text = raw[:m.start()].strip()
        return reply_text, json_str
    return raw, ""


def parse_llm_response(raw: str) -> LLMResponse:
    """Parse LLM JSON output, with graceful fallback.

    Supports both contract 1 (new_facts) and contract 2 (memory_ops).
    Strips markdown code fences if present.
    """
    try:
        data = json.loads(_strip_fences(raw))
        if not isinstance(data, dict):
            raise TypeError(f"Expected JSON object, got {type(data).__name__}")
        reply = data.get("reply") or data.get("response") or ""
        # If LLM returned JSON with ops but forgot the reply field,
        # don't leak raw JSON to the user
        if not reply and (data.get("memory_ops") or data.get("settings_ops")
                          or data.get("new_facts")):
            logger.warning("LLM returned ops but no reply field — keys: %s",
                           list(data.keys()))
            reply = ""  # empty reply, ops still processed
        elif not reply:
            # No reply and no ops — unparseable content, but valid JSON
            logger.warning("LLM returned JSON with no reply — keys: %s",
                           list(data.keys()))
            reply = raw  # truly unparseable, show as-is
        return {
            "reply": reply,
            "memory_ops": data.get("memory_ops", []),
            "new_facts": data.get("new_facts", []),
            "relay_draft": data.get("relay_draft"),
            "relay_urgency": data.get("relay_urgency"),
            "detected_mood": data.get("detected_mood"),
            "patterns_observed": data.get("patterns_observed", []),
            "partner_themes": data.get("partner_themes", []),
            "articulation_note": data.get("articulation_note"),
            "horsemen_detected": data.get("horsemen_detected", []),
            "voice_traits": data.get("voice_traits", {}),
        }
    except (json.JSONDecodeError, TypeError) as exc:
        # Try mixed-content extraction: "reply text\n```json\n{...}\n```"
        reply_text, json_str = _extract_json_from_mixed(raw)
        if json_str:
            try:
                data = json.loads(json_str)
                if isinstance(data, dict):
                    # Use extracted reply text, merge with JSON ops
                    return {
                        "reply": reply_text or data.get("reply", ""),
                        "memory_ops": data.get("memory_ops", []),
                        "new_facts": data.get("new_facts", []),
                        "relay_draft": data.get("relay_draft"),
            "relay_urgency": data.get("relay_urgency"),
                        "detected_mood": data.get("detected_mood"),
                        "patterns_observed": data.get("patterns_observed", []),
                        "partner_themes": data.get("partner_themes", []),
                        "articulation_note": data.get("articulation_note"),
                        "horsemen_detected": data.get("horsemen_detected", []),
                        "voice_traits": data.get("voice_traits", {}),
                    }
            except (json.JSONDecodeError, TypeError):
                pass  # fall through to _parse_failed
        return {
            "_parse_failed": True,
            "_parse_error": str(exc),
            "reply": raw,
            "memory_ops": [],
            "new_facts": [],
            "relay_draft": None,
            "relay_urgency": None,
            "detected_mood": None,
            "patterns_observed": [],
            "partner_themes": [],
            "articulation_note": None,
            "horsemen_detected": [],
            "voice_traits": {},
        }


def _with_tool_calls(parsed: LLMResponse, tool_calls: list[dict[str, Any]] | None) -> LLMResponse:
    if tool_calls:
        parsed["tool_calls"] = tool_calls
    return parsed


def get_reply_text(response: LLMResponse, fallback: str = "") -> str:
    """Extract clean reply text from an LLM response.

    Use this everywhere instead of response.get("reply", "").
    Handles: empty reply, raw JSON leak, array wrapper.
    """
    reply = response.get("reply", "")
    if not reply:
        return fallback
    # Guard: if the reply is raw JSON (LLM forgot to put text), discard
    stripped = reply.strip()
    if stripped.startswith("{") and ("memory_ops" in stripped or "settings_ops" in stripped):
        logger.warning("get_reply_text: reply is raw JSON, discarding")
        return fallback
    # Guard: if reply is a JSON array (nudge edge case)
    if stripped.startswith("["):
        try:
            arr = json.loads(stripped)
            if isinstance(arr, list) and arr:
                return str(arr[0]).strip().strip('"')
        except (json.JSONDecodeError, TypeError):
            pass
    return reply


def _to_gemini_tools(tools: list[dict[str, Any]]) -> list[Any]:
    """Convert OpenAI-format tool definitions to a Gemini ``Tool`` list."""
    decls = []
    for t in tools:
        fn = t.get("function", t)
        decls.append(genai_types.FunctionDeclaration(
            name=fn["name"],
            description=fn.get("description", ""),
            parameters_json_schema=fn.get("parameters"),
        ))
    return [genai_types.Tool(function_declarations=decls)]


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
        tools: list[dict[str, Any]] | None = None,
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
        tools: list[dict[str, Any]] | None = None,
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
                    # Retry once on the same model
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

        # All models exhausted — return a safe fallback
        return _make_fallback("gemini", models_to_try[0] if models_to_try else "",
                              last_code=_last_code)

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
        raw, _ = await self._call_ex(
            model, user_content, system_prompt, api_key_override=api_key_override,
        )
        return raw

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
            # Gemini rejects function calling combined with a JSON mime type.
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
        tools: list[dict[str, Any]] | None = None,
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
                    # Retry once on the same model
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
        """Return a warning string if usage is approaching the cap, else None."""
        if model not in self._model_heavy:
            return None
        count = await db_mod.get_usage_count(self._db_path, model)
        cap = self._heavy_daily_cap
        if count >= cap - 2:  # warn at cap-2
            return f"⚠️ Heavy model usage: {count}/{cap} today."
        return None

    # ----- private -----

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
        # Only reached for attributes not defined here (check_usage_warning...).
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


def create_llm_client(config: Config, db_path: str) -> LLMClient:
    """Return the right LLM client (wrapped in call tracing)."""
    return TracingLLM(_create_inner_client(config, db_path), db_path)


def _create_inner_client(config: Config, db_path: str) -> LLMClient:
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
