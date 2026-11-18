"""End-to-end tests for review_before_send: approval callback -> delivery loop."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from pairtner.adapters.base import BaseAdapter
from pairtner.adapters.cli_adapter import CLIAdapter
from pairtner.adapters.telegram_adapter import TelegramAdapter
from pairtner.features.approval import delivery_loop
from pairtner.config import Config, load_config
from pairtner.db import create_pending, get_batch
from pairtner.handlers import BotService
from tests.helpers import MockLLMClient

DRAFT = "REVIEWSENTINEL-gentle-version-2290"


def _cfg(test_db: str, review: bool) -> Config:
    return Config(
        telegram_token="t", gemini_api_key="k", model_default="d",
        model_heavy=["h"], db_path=test_db, cooldown_minutes=0,
        review_before_send=review,
    )


async def _run_loop(test_db: str, cfg: Config, ticks: float = 0.15):
    sent: list[tuple[int, str]] = []
    buttons: list[tuple[int, str, list]] = []

    async def send(uid: int, text: str) -> None:
        sent.append((uid, text))

    async def send_buttons(uid: int, text: str, btns: list) -> None:
        buttons.append((uid, text, btns))

    task = asyncio.create_task(delivery_loop(
        test_db, send, send_buttons_func=send_buttons,
        poll_interval=0.01, review_before_send=cfg.review_before_send,
    ))
    await asyncio.sleep(ticks)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return sent, buttons


async def _approve(test_db, cfg, a, b):
    svc = BotService(config=cfg, db_path=test_db, llm=MockLLMClient())
    pid = await create_pending(test_db, a, b, DRAFT)
    resp = await svc.handle_callback(a, f"ap:{pid}")
    assert resp
    return svc


async def test_review_false_delivers_without_prompt(test_db, paired_users):
    a, b = paired_users
    cfg = _cfg(test_db, False)
    await _approve(test_db, cfg, a, b)
    sent, buttons = await _run_loop(test_db, cfg)
    assert any(uid == b and DRAFT in text for uid, text in sent)
    assert not [x for x in buttons if x[0] == a]
    assert not [s for s in sent if s[0] == a and "review" in s[1].lower()]


async def test_review_true_prompts_sender_first(test_db, paired_users):
    a, b = paired_users
    cfg = _cfg(test_db, True)
    svc = await _approve(test_db, cfg, a, b)
    sent, buttons = await _run_loop(test_db, cfg)
    assert [x for x in buttons if x[0] == a], "sender must get a review prompt"
    data = [d for _, _, rows in buttons for row in rows for _, d in row]
    assert any(d.startswith("ba:") for d in data)
    assert not [s for s in sent if s[0] == b], "receiver gets nothing before approval"

    batch_id = int(next(d for d in data if d.startswith("ba:")).split(":")[1])
    assert (await get_batch(test_db, batch_id))["status"] == "reviewing"
    # "Send all" returns the relay addressed to the receiver for the transport to send.
    resp = await svc.handle_callback(a, f"ba:{batch_id}")
    assert any(r.target_user_id == b and DRAFT in r.text for r in resp)
    assert (await get_batch(test_db, batch_id))["status"] == "sent"


def test_default_is_true():
    assert Config(telegram_token="t").review_before_send is True


def test_config_value_reaches_loop_via_load_config(tmp_path, monkeypatch):
    yaml_file = tmp_path / "settings.yaml"
    yaml_file.write_text("review_before_send: false\n")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("GEMINI_API_KEY", "key")
    monkeypatch.delenv("REVIEW_BEFORE_SEND", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    with patch("pairtner.config.load_dotenv"):
        cfg = load_config(yaml_path=str(yaml_file))
    assert cfg.review_before_send is False


@pytest.mark.parametrize("flag", [True, False])
async def test_base_adapter_passes_flag_to_loop(test_db, flag):
    cfg = _cfg(test_db, flag)
    svc = BotService(config=cfg, db_path=test_db, llm=MockLLMClient())
    adapter = CLIAdapter(svc, cfg)
    assert isinstance(adapter, BaseAdapter)
    captured: dict = {}

    async def fake_loop(*args, **kwargs):
        captured.update(kwargs)

    with patch("pairtner.adapters.loops.delivery_loop", fake_loop):
        await adapter._start_background_tasks()
        await asyncio.sleep(0.05)
        for task in adapter._background_tasks:
            task.cancel()
        await asyncio.gather(*adapter._background_tasks, return_exceptions=True)
    assert captured["review_before_send"] is flag


@pytest.mark.parametrize("flag", [True, False])
async def test_telegram_post_init_passes_flag_to_loop(test_db, flag):
    cfg = _cfg(test_db, flag)
    svc = BotService(config=cfg, db_path=test_db, llm=MockLLMClient())
    adapter = TelegramAdapter(svc, cfg)
    captured: dict = {}

    async def fake_loop(*args, **kwargs):
        captured.update(kwargs)

    app = MagicMock()
    created: list = []
    app.create_task = lambda coro: created.append(asyncio.ensure_future(coro))
    with patch("pairtner.adapters.loops.delivery_loop", fake_loop):
        await adapter._post_init(app)
        await asyncio.sleep(0.05)
        for task in created:
            task.cancel()
        await asyncio.gather(*created, return_exceptions=True)
    assert captured["review_before_send"] is flag


@pytest.mark.parametrize("flag", [True, False])
async def test_rest_adapter_passes_flag_to_loop(test_db, flag):
    from pairtner.adapters.rest_adapter import RestAdapter

    cfg = _cfg(test_db, flag)
    svc = BotService(config=cfg, db_path=test_db, llm=MockLLMClient())
    adapter = RestAdapter(svc, cfg)
    captured: dict = {}

    async def fake_loop(*args, **kwargs):
        captured.update(kwargs)

    with patch("pairtner.adapters.loops.delivery_loop", fake_loop):
        await adapter._start_background_tasks()
        await asyncio.sleep(0.05)
        for task in adapter._background_tasks:
            task.cancel()
        await asyncio.gather(*adapter._background_tasks, return_exceptions=True)
    assert captured["review_before_send"] is flag
