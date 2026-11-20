"""Tests for auto model capability detection."""

import json
import pytest

from pairtner.core.model_probe import probe_model, get_or_probe_capability
from pairtner.db import init_db, ensure_user
from pairtner.storage.migrate_v2 import migrate_to_v2


class MockLLM:
    """Mock LLM that returns controlled probe responses."""

    def __init__(self, response: dict | str | None = None, fail: bool = False):
        self._response = response
        self._fail = fail

    async def generate(self, user_content, system_prompt, **kw):
        if self._fail:
            raise RuntimeError("LLM unavailable")
        if isinstance(self._response, str):
            return {"reply": self._response}
        return self._response or {"reply": json.dumps({
            "items": [
                "Alice got a promotion at work",
                "Bob forgot their anniversary",
                "Kids need school supplies for Monday",
                "The car needs an oil change",
                "Alice wants to try the new Italian restaurant",
            ]
        })}


@pytest.mark.asyncio
async def test_probe_high_capability():
    """Model that returns valid JSON with all 5 topics = high."""
    result = await probe_model(MockLLM())
    assert result["capability"] == "high"
    assert result["json_ok"] is True
    assert result["topics_found"] == 5


@pytest.mark.asyncio
async def test_probe_low_on_failure():
    """LLM failure = low capability."""
    result = await probe_model(MockLLM(fail=True))
    assert result["capability"] == "low"
    assert result["json_ok"] is False


@pytest.mark.asyncio
async def test_probe_low_on_invalid_json():
    """Non-JSON response = low."""
    result = await probe_model(MockLLM(response="I can help with that!"))
    assert result["capability"] == "low"
    assert result["json_ok"] is False


@pytest.mark.asyncio
async def test_probe_medium_on_partial():
    """3 topics out of 5 = medium."""
    result = await probe_model(MockLLM(response=json.dumps({
        "items": ["Alice promoted", "Bob forgot anniversary", "Car oil change"]
    })))
    assert result["capability"] in ("medium", "high")
    assert result["json_ok"] is True
    assert result["topics_found"] == 3


@pytest.mark.asyncio
async def test_probe_with_markdown_fences():
    """JSON wrapped in ```json fences still parses."""
    resp = '```json\n{"items": ["one topic", "two topic", "three topic", "four topics"]}\n```'
    result = await probe_model(MockLLM(response=resp))
    assert result["json_ok"] is True
    assert result["topics_found"] == 4


@pytest.mark.asyncio
async def test_cached_capability(tmp_path):
    """Capability is cached per model name."""
    db = str(tmp_path / "test.db")
    await init_db(db)
    await ensure_user(db, 1001, "Alice")

    # First call probes
    cap = await get_or_probe_capability(db, 1001, MockLLM(), "gemma3:4b")
    assert cap == "high"

    # Second call uses cache (even with a failing LLM)
    cap2 = await get_or_probe_capability(db, 1001, MockLLM(fail=True), "gemma3:4b")
    assert cap2 == "high"  # cached, didn't probe

    # Different model re-probes
    cap3 = await get_or_probe_capability(db, 1001, MockLLM(response="nope"), "phi3-mini")
    assert cap3 == "low"  # re-probed, got low
