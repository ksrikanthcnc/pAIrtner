"""Shared test helpers."""

from __future__ import annotations

from couples_bot.llm import LLMResponse


class MockLLMClient:
    """Deterministic LLM double for testing."""

    def __init__(self, responses: list[LLMResponse] | None = None) -> None:
        self.responses: list[LLMResponse] = responses or []
        self.calls: list[dict] = []

    async def generate(
        self,
        user_content: str,
        system_prompt: str,
        model: str | None = None,
        api_key_override: str | None = None,
    ) -> LLMResponse:
        self.calls.append({
            "user_content": user_content,
            "system_prompt": system_prompt,
            "model": model,
            "api_key_override": api_key_override,
        })
        if self.responses:
            return self.responses.pop(0)
        return {"reply": "Mock reply", "new_facts": [], "relay_draft": None}
