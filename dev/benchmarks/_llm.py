"""Shared provider plumbing for the benchmark scripts (ollama + gemini)."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")


def _gemini_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "")
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("GEMINI_API_KEY="):
                key = line.split("=", 1)[1].strip().strip("\"'")
    if not key:
        raise SystemExit("GEMINI_API_KEY not set (env or .env)")
    return key


def strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


async def call_llm(
    provider: str, model: str, system: str, user: str, *,
    json_mode: bool = False, temperature: float = 0.3, reasoning_effort: str = "",
) -> str:
    """Return the raw text completion from the chosen provider."""
    if provider == "ollama":
        from openai import AsyncOpenAI

        client = AsyncOpenAI(base_url=OLLAMA_URL, api_key="ollama", timeout=180)
        kwargs: dict = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if reasoning_effort:
            kwargs["extra_body"] = {"reasoning_effort": reasoning_effort}
        resp = await client.chat.completions.create(
            model=model, temperature=temperature,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            **kwargs,
        )
        return strip_think(resp.choices[0].message.content or "")
    if provider == "gemini":
        from google import genai

        client = genai.Client(api_key=_gemini_key())
        config: dict = {"system_instruction": system, "temperature": temperature}
        if json_mode:
            config["response_mime_type"] = "application/json"
        resp = await client.aio.models.generate_content(
            model=model, contents=user, config=config,
        )
        return strip_think(resp.text or "")
    raise SystemExit(f"unknown provider {provider!r} (use ollama or gemini)")
