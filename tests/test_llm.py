"""LLM client tests — mock the google-genai SDK, never call real API."""

from __future__ import annotations

import pytest

from couples_bot.db import get_usage_count, increment_usage
from couples_bot.llm import GeminiClient, parse_llm_response, _get_error_code


# ---------------------------------------------------------------------------
# parse_llm_response
# ---------------------------------------------------------------------------

class TestParseLLMResponse:
    def test_valid_json(self):
        raw = '{"reply": "Hello!", "new_facts": [{"text": "likes tea", "visibility": "private"}], "relay_draft": null}'
        result = parse_llm_response(raw)
        assert result["reply"] == "Hello!"
        assert len(result["new_facts"]) == 1
        assert result["relay_draft"] is None

    def test_missing_fields(self):
        raw = '{"reply": "Hi"}'
        result = parse_llm_response(raw)
        assert result["reply"] == "Hi"
        assert result["new_facts"] == []
        assert result["relay_draft"] is None

    def test_invalid_json(self):
        raw = "Not JSON at all"
        result = parse_llm_response(raw)
        assert result["reply"] == "Not JSON at all"
        assert result["new_facts"] == []
        assert result["relay_draft"] is None

    def test_empty_string(self):
        result = parse_llm_response("")
        assert result["reply"] == ""
        assert result["new_facts"] == []

    def test_with_relay_draft(self):
        raw = '{"reply": "Got it", "new_facts": [], "relay_draft": "Please listen"}'
        result = parse_llm_response(raw)
        assert result["relay_draft"] == "Please listen"


# ---------------------------------------------------------------------------
# Error code extraction
# ---------------------------------------------------------------------------

class TestGetErrorCode:
    def test_code_attr(self):
        exc = Exception("test")
        exc.code = 429  # type: ignore
        assert _get_error_code(exc) == 429

    def test_status_code_attr(self):
        exc = Exception("test")
        exc.status_code = 503  # type: ignore
        assert _get_error_code(exc) == 503

    def test_from_string(self):
        exc = Exception("Error 404: model not found")
        assert _get_error_code(exc) == 404

    def test_unknown(self):
        exc = Exception("Something weird")
        assert _get_error_code(exc) == 0


# ---------------------------------------------------------------------------
# Usage counter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_usage_counter_increments(test_db):
    assert await get_usage_count(test_db, "model-x") == 0
    await increment_usage(test_db, "model-x")
    assert await get_usage_count(test_db, "model-x") == 1
    await increment_usage(test_db, "model-x")
    assert await get_usage_count(test_db, "model-x") == 2


# ---------------------------------------------------------------------------
# GeminiClient — model fallback (mocked at SDK level)
# ---------------------------------------------------------------------------

class FakeGenaiError(Exception):
    def __init__(self, code: int, msg: str = "error"):
        super().__init__(msg)
        self.code = code


class FakeResponse:
    def __init__(self, text: str):
        self.text = text


class FakeModels:
    """Mock for client.aio.models with configurable failures."""

    def __init__(self, fail_models: dict[str, int] | None = None):
        self.fail_models = fail_models or {}
        self.calls: list[str] = []

    async def generate_content(self, *, model, contents, config=None):
        self.calls.append(model)
        if model in self.fail_models:
            raise FakeGenaiError(self.fail_models[model])
        return FakeResponse('{"reply":"ok","new_facts":[],"relay_draft":null}')


class FakeAio:
    def __init__(self, models: FakeModels):
        self.models = models


class FakeGenaiClient:
    def __init__(self, models: FakeModels):
        self.aio = FakeAio(models)


@pytest.mark.asyncio
async def test_llm_fallback_on_404(test_db, config):
    """404 on first model should fall through to second."""
    models = FakeModels(fail_models={"test-heavy-1": 404})
    client = GeminiClient(config, test_db)
    client._client = FakeGenaiClient(models)  # type: ignore

    result = await client.generate("Hello", "System", model="test-heavy-1")
    assert result["reply"] == "ok"
    assert models.calls == ["test-heavy-1", "test-heavy-2"]


@pytest.mark.asyncio
async def test_llm_fallback_on_503(test_db, config):
    """503 should also trigger fallback."""
    models = FakeModels(fail_models={"test-heavy-1": 503})
    client = GeminiClient(config, test_db)
    client._client = FakeGenaiClient(models)  # type: ignore

    result = await client.generate("Hello", "System", model="test-heavy-1")
    assert result["reply"] == "ok"
    assert "test-heavy-2" in models.calls


@pytest.mark.asyncio
async def test_llm_429_backoff(test_db, config):
    """429 should retry once on the same model before moving on."""
    call_count = 0

    class Flaky(FakeModels):
        async def generate_content(self, *, model, contents, config=None):
            nonlocal call_count
            call_count += 1
            self.calls.append(model)
            # Fail first call, succeed on retry
            if call_count == 1:
                raise FakeGenaiError(429, "rate limited")
            return FakeResponse('{"reply":"retried","new_facts":[],"relay_draft":null}')

    models = Flaky()
    client = GeminiClient(config, test_db)
    client._client = FakeGenaiClient(models)  # type: ignore
    # Shorten backoff for test
    client._parse_retry_delay = staticmethod(lambda exc: 0.01)  # type: ignore

    result = await client.generate("Hello", "System", model="test-default")
    assert result["reply"] == "retried"
    assert call_count == 2


@pytest.mark.asyncio
async def test_all_models_exhausted(test_db, config):
    """When all models fail, return a safe fallback message."""
    models = FakeModels(fail_models={
        "test-heavy-1": 404,
        "test-heavy-2": 503,
    })
    client = GeminiClient(config, test_db)
    client._client = FakeGenaiClient(models)  # type: ignore

    result = await client.generate("Hello", "System", model="test-heavy-1")
    assert "trouble" in result["reply"].lower() or "try again" in result["reply"].lower()
