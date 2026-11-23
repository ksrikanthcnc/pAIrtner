"""LLM client tests — mock the google-genai SDK, never call real API."""

from __future__ import annotations

import pytest

from pairtner.db import get_usage_count, increment_usage
from pairtner.llm import GeminiClient, parse_llm_response, parse_retry_delay, _get_error_code


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

    def test_extended_fields(self):
        raw = (
            '{"reply": "ok", "new_facts": [], "relay_draft": null, '
            '"detected_mood": "happy", "patterns_observed": ["positive framing"], '
            '"partner_themes": ["feels supported"], "articulation_note": "good job"}'
        )
        result = parse_llm_response(raw)
        assert result["detected_mood"] == "happy"
        assert result["patterns_observed"] == ["positive framing"]
        assert result["partner_themes"] == ["feels supported"]
        assert result["articulation_note"] == "good job"

    def test_extended_fields_defaults(self):
        """Missing extended fields get safe defaults."""
        raw = '{"reply": "Hi"}'
        result = parse_llm_response(raw)
        assert result["detected_mood"] is None
        assert result["patterns_observed"] == []
        assert result["partner_themes"] == []
        assert result["articulation_note"] is None

    def test_invalid_json_extended_defaults(self):
        """Invalid JSON returns safe defaults for all fields."""
        result = parse_llm_response("not json")
        assert result["detected_mood"] is None
        assert result["patterns_observed"] == []
        assert result["partner_themes"] == []
        assert result["articulation_note"] is None


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
async def test_llm_429_backoff(test_db, config, monkeypatch):
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
    monkeypatch.setattr("pairtner.llm.parse_retry_delay", lambda exc: 0.01)

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
    assert "trouble" in result["reply"].lower() or "couldn't reach" in result["reply"].lower()


# ---------------------------------------------------------------------------
# parse_retry_delay — module-level function (FEAT-002 dedup)
# ---------------------------------------------------------------------------

class TestParseRetryDelay:
    def test_retry_delay_attr(self):
        exc = Exception("test")
        exc.retry_delay = 30.0  # type: ignore
        assert parse_retry_delay(exc) == 30.0

    def test_retry_after_attr(self):
        exc = Exception("test")
        exc.retry_after = 45  # type: ignore
        assert parse_retry_delay(exc) == 45.0

    def test_no_attr_defaults_to_60(self):
        exc = Exception("plain error")
        assert parse_retry_delay(exc) == 60.0

    def test_non_numeric_attr(self):
        exc = Exception("test")
        exc.retry_delay = "not-a-number"  # type: ignore
        assert parse_retry_delay(exc) == 60.0

    def test_is_module_level_function(self):
        """parse_retry_delay is a module-level function, not a method."""
        import pairtner.llm as llm_mod
        assert callable(llm_mod.parse_retry_delay)
        assert not hasattr(GeminiClient, "_parse_retry_delay")


def test_parse_llm_response_flags_parse_failure():
    bad = parse_llm_response("not json")
    assert bad["_parse_failed"] is True
    assert bad["_parse_error"]
    assert bad["reply"] == "not json"
    assert "_parse_failed" not in parse_llm_response('{"reply": "hi"}')


# ---------------------------------------------------------------------------
# MultiProviderLLM routing
# ---------------------------------------------------------------------------

class TestMultiProviderRouting:
    """Verify model-name routing between Ollama and Gemini backends."""

    def setup_method(self):
        from unittest.mock import AsyncMock
        self.ollama = AsyncMock()
        self.ollama.generate = AsyncMock(return_value={"reply": "from-ollama"})
        self.gemini = AsyncMock()
        self.gemini.generate = AsyncMock(return_value={"reply": "from-gemini"})

    def _make(self, default="ollama"):
        from pairtner.llm import MultiProviderLLM
        return MultiProviderLLM(self.ollama, self.gemini, default)

    @pytest.mark.asyncio
    async def test_default_routes_to_ollama(self):
        mp = self._make("ollama")
        await mp.generate("hi", "sys")
        self.ollama.generate.assert_awaited_once()
        self.gemini.generate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_default_routes_to_gemini(self):
        mp = self._make("gemini")
        await mp.generate("hi", "sys")
        self.gemini.generate.assert_awaited_once()
        self.ollama.generate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_gemini_prefix_routes_to_gemini(self):
        mp = self._make("ollama")
        await mp.generate("hi", "sys", model="gemini:flash-2")
        self.gemini.generate.assert_awaited_once()
        # Model name should be stripped of prefix
        call_args = self.gemini.generate.call_args
        assert call_args[0][2] == "flash-2"  # positional arg: model
        self.ollama.generate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ollama_prefix_routes_to_ollama(self):
        mp = self._make("gemini")
        await mp.generate("hi", "sys", model="ollama:qwen3:8b")
        self.ollama.generate.assert_awaited_once()
        call_args = self.ollama.generate.call_args
        assert call_args[0][2] == "qwen3:8b"
        self.gemini.generate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_native_gemini_model_name(self):
        mp = self._make("ollama")
        await mp.generate("hi", "sys", model="gemini-3.1-flash-lite")
        self.gemini.generate.assert_awaited_once()
        self.ollama.generate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_native_ollama_model_name(self):
        mp = self._make("ollama")
        await mp.generate("hi", "sys", model="qwen3:8b")
        self.ollama.generate.assert_awaited_once()
        self.gemini.generate.assert_not_awaited()

    def test_providers_list(self):
        mp = self._make()
        assert set(mp.providers) == {"ollama", "gemini"}

    def test_providers_ollama_only(self):
        from pairtner.llm import MultiProviderLLM
        mp = MultiProviderLLM(self.ollama, None, "ollama")
        assert mp.providers == ["ollama"]

    @pytest.mark.asyncio
    async def test_missing_gemini_raises(self):
        from pairtner.llm import MultiProviderLLM
        mp = MultiProviderLLM(self.ollama, None, "ollama")
        with pytest.raises(RuntimeError, match="Gemini not configured"):
            await mp.generate("hi", "sys", model="gemini:flash")
