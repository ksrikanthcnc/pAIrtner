"""LLM base types — protocol, response parsing, and shared helpers."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol, runtime_checkable

from google.genai import types as genai_types

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------

FALLBACK_REPLY = "I'm having trouble connecting right now. Please try again in a moment."


def _make_fallback(provider: str = "", model: str = "", last_code: int = 0) -> dict:
    """Build a fallback response with a helpful error message."""
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


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _strip_fences(raw: str) -> str:
    """Remove markdown code fences (```json ... ```) wrapping a JSON blob."""
    s = raw.strip()
    if s.startswith("```"):
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
    """
    m = re.search(r'```(?:json)?\s*\n(\{.*?\})\s*```', raw, re.DOTALL)
    if m:
        json_str = m.group(1)
        reply_text = raw[:m.start()].strip()
        return reply_text, json_str
    m = re.search(r'\n(\{[^{]*"(?:memory_ops|reply)".*\})\s*$', raw, re.DOTALL)
    if m:
        json_str = m.group(1)
        reply_text = raw[:m.start()].strip()
        return reply_text, json_str
    return raw, ""


_RESPONSE_FIELDS = {
    "relay_urgency", "detected_mood", "patterns_observed",
    "partner_themes", "articulation_note", "horsemen_detected",
    "voice_traits",
}


def _build_response(data: dict, reply: str) -> LLMResponse:
    """Build a normalized response dict from parsed JSON."""
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
        if not reply and (data.get("memory_ops") or data.get("settings_ops")
                          or data.get("new_facts")):
            logger.warning("LLM returned ops but no reply field — keys: %s",
                           list(data.keys()))
            reply = ""
        elif not reply:
            # Extraction tasks (onboarding, analysis) return JSON data without
            # a conversational reply — this is expected, not a warning.
            logger.debug("LLM returned JSON with no reply — keys: %s",
                         list(data.keys()))
            reply = raw
        return _build_response(data, reply)
    except (json.JSONDecodeError, TypeError) as exc:
        reply_text, json_str = _extract_json_from_mixed(raw)
        if json_str:
            try:
                data = json.loads(json_str)
                if isinstance(data, dict):
                    return _build_response(data, reply_text or data.get("reply", ""))
            except (json.JSONDecodeError, TypeError):
                pass
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


def _with_tool_calls(
    parsed: LLMResponse, tool_calls: list[dict[str, Any]] | None,
) -> LLMResponse:
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
    stripped = reply.strip()
    if stripped.startswith("{") and ("memory_ops" in stripped or "settings_ops" in stripped):
        logger.warning("get_reply_text: reply is raw JSON, discarding")
        return fallback
    if stripped.startswith("["):
        try:
            arr = json.loads(stripped)
            if isinstance(arr, list) and arr:
                return str(arr[0]).strip().strip('"')
        except (json.JSONDecodeError, TypeError):
            pass
    return reply


# ---------------------------------------------------------------------------
# Shared helpers
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


def _to_gemini_tools(tools: list[dict[str, Any]]) -> list[Any]:
    """Convert OpenAI-format tool definitions to Gemini ``Tool`` list."""
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
# Protocol
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
