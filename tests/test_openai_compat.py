"""OpenAI-compatible client tests — mock the SDK, never call a real API."""

from __future__ import annotations

import pytest

from couples_bot.config import Config
from couples_bot.llm import (
    GeminiClient,
    OpenAICompatClient,
    create_llm_client,
    parse_llm_response,
)


# ---------------------------------------------------------------------------
# Fakes for the openai SDK
# ---------------------------------------------------------------------------

class FakeChoice:
    def __init__(self, content: str):
        self.message = type("Msg", (), {"content": content})()


class FakeCompletion:
    def __init__(self, content: str):
        self.choices = [FakeChoice(content)]


class FakeCompletions:
    """Mock for client.chat.completions with configurable failures."""

    def __init__(self, fail_models: dict[str, int] | None = None):
        self.fail_models = fail_models or {}
        self.calls: list[str] = []

    async def create(self, *, model, messages, **kwargs):
        self.calls.append(model)
        if model in self.fail_models:
            exc = Exception(f"Error {self.fail_models[model]}")
            exc.status_code = self.fail_models[model]  # type: ignore[attr-defined]
            raise exc
        return FakeCompletion(
            '{"reply":"ok","new_facts":[],"relay_draft":null}'
        )


class FakeChat:
    def __init__(self, completions: FakeCompletions):
        self.completions = completions


class FakeAsyncOpenAI:
    def __init__(self, completions: FakeCompletions):
        self.chat = FakeChat(completions)


# ---------------------------------------------------------------------------
# Helper to build a test client with fakes injected
# ---------------------------------------------------------------------------

def _make_client(
    db_path: str,
    fail_models: dict[str, int] | None = None,
    model_heavy: list[str] | None = None,
) -> tuple[OpenAICompatClient, FakeCompletions]:
    completions = FakeCompletions(fail_models=fail_models)
    client = OpenAICompatClient(
        base_url="https://api.example.com/v1",
        api_key="test-key",
        default_model="test-model",
        db_path=db_path,
        model_heavy=model_heavy,
    )
    client._client = FakeAsyncOpenAI(completions)  # type: ignore[assignment]
    return client, completions


# ---------------------------------------------------------------------------
# generate() basic behavior
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_returns_parsed_response(test_db):
    client, completions = _make_client(test_db)
    result = await client.generate("Hello", "System prompt")

    assert result["reply"] == "ok"
    assert result["new_facts"] == []
    assert result["relay_draft"] is None
    assert completions.calls == ["test-model"]


@pytest.mark.asyncio
async def test_generate_with_explicit_model(test_db):
    client, completions = _make_client(test_db)
    result = await client.generate("Hello", "System", model="custom-model")

    assert result["reply"] == "ok"
    assert completions.calls == ["custom-model"]


# ---------------------------------------------------------------------------
# Model fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fallback_on_404(test_db):
    client, completions = _make_client(
        test_db,
        fail_models={"heavy-1": 404},
        model_heavy=["heavy-1", "heavy-2"],
    )
    result = await client.generate("Hello", "System", model="heavy-1")

    assert result["reply"] == "ok"
    assert completions.calls == ["heavy-1", "heavy-2"]


@pytest.mark.asyncio
async def test_fallback_on_503(test_db):
    client, completions = _make_client(
        test_db,
        fail_models={"heavy-1": 503},
        model_heavy=["heavy-1", "heavy-2"],
    )
    result = await client.generate("Hello", "System", model="heavy-1")

    assert result["reply"] == "ok"
    assert completions.calls == ["heavy-1", "heavy-2"]


@pytest.mark.asyncio
async def test_fallback_on_429(test_db):
    client, completions = _make_client(
        test_db,
        fail_models={"heavy-1": 429},
        model_heavy=["heavy-1", "heavy-2"],
    )
    result = await client.generate("Hello", "System", model="heavy-1")

    assert result["reply"] == "ok"
    assert completions.calls == ["heavy-1", "heavy-2"]


@pytest.mark.asyncio
async def test_all_models_exhausted(test_db):
    client, completions = _make_client(
        test_db,
        fail_models={"heavy-1": 404, "heavy-2": 503},
        model_heavy=["heavy-1", "heavy-2"],
    )
    result = await client.generate("Hello", "System", model="heavy-1")

    assert "trouble" in result["reply"].lower() or "try again" in result["reply"].lower()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def test_factory_returns_gemini(config, test_db):
    # Default config has llm_provider="gemini"
    client = create_llm_client(config, test_db)
    assert isinstance(client, GeminiClient)


def test_factory_returns_openai_compat(test_db):
    cfg = Config(
        telegram_token="test-token",
        llm_provider="openai_compat",
        llm_base_url="https://api.example.com/v1",
        llm_api_key="test-key",
        db_path=test_db,
    )
    client = create_llm_client(cfg, test_db)
    assert isinstance(client, OpenAICompatClient)
