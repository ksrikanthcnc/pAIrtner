"""OpenAI-compatible client tests — mock the SDK, never call a real API."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from couples_bot.config import Config
from couples_bot.db import increment_usage
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
    heavy_daily_cap: int = 10,
) -> tuple[OpenAICompatClient, FakeCompletions]:
    completions = FakeCompletions(fail_models=fail_models)
    client = OpenAICompatClient(
        base_url="https://api.example.com/v1",
        api_key="test-key",
        default_model="test-model",
        db_path=db_path,
        model_heavy=model_heavy,
        heavy_daily_cap=heavy_daily_cap,
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
    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await client.generate("Hello", "System", model="heavy-1")

    assert result["reply"] == "ok"
    # Retries once on heavy-1 (backoff), then falls through to heavy-2
    assert completions.calls == ["heavy-1", "heavy-1", "heavy-2"]


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


# ---------------------------------------------------------------------------
# check_usage_warning
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_usage_warning_under_cap(test_db):
    """No warning when usage is well below the cap."""
    client, _ = _make_client(
        test_db, model_heavy=["heavy-model"], heavy_daily_cap=10,
    )
    result = await client.check_usage_warning("heavy-model")
    assert result is None


@pytest.mark.asyncio
async def test_check_usage_warning_near_cap(test_db):
    """Warning appears when usage reaches cap - 2."""
    client, _ = _make_client(
        test_db, model_heavy=["heavy-model"], heavy_daily_cap=10,
    )
    for _ in range(8):  # cap-2 = 8
        await increment_usage(test_db, "heavy-model")

    result = await client.check_usage_warning("heavy-model")
    assert result is not None
    assert "8/10" in result


@pytest.mark.asyncio
async def test_check_usage_warning_non_heavy_model(test_db):
    """Non-heavy models should never trigger a warning."""
    client, _ = _make_client(
        test_db, model_heavy=["heavy-model"], heavy_daily_cap=10,
    )
    result = await client.check_usage_warning("some-other-model")
    assert result is None


# ---------------------------------------------------------------------------
# 429 backoff
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_429_backoff_sleeps_and_retries(test_db):
    """On 429, should sleep for the parsed delay and retry once."""
    client, completions = _make_client(
        test_db,
        fail_models={"heavy-1": 429},
        model_heavy=["heavy-1", "heavy-2"],
    )
    mock_sleep = AsyncMock()
    with patch("asyncio.sleep", mock_sleep):
        result = await client.generate("Hello", "System", model="heavy-1")

    assert result["reply"] == "ok"
    # asyncio.sleep should be called once for the backoff
    mock_sleep.assert_called_once_with(60.0)  # default delay
    # heavy-1 tried twice (original + retry), then heavy-2
    assert completions.calls == ["heavy-1", "heavy-1", "heavy-2"]


@pytest.mark.asyncio
async def test_429_backoff_uses_retry_after(test_db):
    """If the exception has a retry_after attribute, use that delay."""
    completions = FakeCompletions()
    call_count = 0

    async def create_with_retry_after(*, model, messages, **kwargs):
        nonlocal call_count
        completions.calls.append(model)
        call_count += 1
        if model == "heavy-1" and call_count <= 2:
            exc = Exception("Rate limited")
            exc.status_code = 429  # type: ignore[attr-defined]
            exc.retry_after = 5.0  # type: ignore[attr-defined]
            raise exc
        return FakeCompletion(
            '{"reply":"ok","new_facts":[],"relay_draft":null}'
        )

    client = OpenAICompatClient(
        base_url="https://api.example.com/v1",
        api_key="test-key",
        default_model="test-model",
        db_path=test_db,
        model_heavy=["heavy-1", "heavy-2"],
    )
    client._client = FakeAsyncOpenAI(completions)  # type: ignore[assignment]
    client._client.chat.completions.create = create_with_retry_after  # type: ignore[assignment]

    mock_sleep = AsyncMock()
    with patch("asyncio.sleep", mock_sleep):
        result = await client.generate("Hello", "System", model="heavy-1")

    assert result["reply"] == "ok"
    mock_sleep.assert_called_once_with(5.0)


# ---------------------------------------------------------------------------
# reasoning_effort passthrough (disables thinking on Qwen3 via Ollama)
# ---------------------------------------------------------------------------

class _KwargsCapturingCompletions(FakeCompletions):
    def __init__(self):
        super().__init__()
        self.kwargs: dict = {}

    async def create(self, *, model, messages, **kwargs):
        self.kwargs = kwargs
        return await super().create(model=model, messages=messages, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("effort,expected", [("none", {"reasoning_effort": "none"}), ("", None)])
async def test_reasoning_effort_sent_only_when_configured(test_db, effort, expected):
    comps = _KwargsCapturingCompletions()
    with patch("couples_bot.llm.AsyncOpenAI", return_value=FakeAsyncOpenAI(comps)):
        client = OpenAICompatClient(
            base_url="http://x/v1", api_key="k", default_model="m",
            db_path=test_db, reasoning_effort=effort,
        )
    await client.generate("hi", "sys")
    assert comps.kwargs["extra_body"] == expected
