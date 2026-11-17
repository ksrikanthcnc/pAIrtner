"""MultiAdapter routing, loop ownership and launcher wiring."""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import MagicMock, patch

import pytest

from couples_bot.adapters import ADAPTER_REGISTRY, get_adapter
from couples_bot.adapters.base import BaseAdapter
from couples_bot.adapters.multi import MultiAdapter
from couples_bot.adapters.rest_adapter import RestAdapter
from couples_bot.adapters.telegram_adapter import TelegramAdapter
from couples_bot.config import Config
from couples_bot.handlers import BotService
from couples_bot.rest.ids import REST_ID_BASE
from tests.helpers import MockLLMClient


class FakeChild(BaseAdapter):
    def __init__(self, service, config, name, owns):
        super().__init__(service, config)
        self.name = name
        self._owns = owns
        self.sent: list = []
        self.started = asyncio.Event()
        self.stopped = False
        self.transport_description = name

    def owns_user(self, user_id):
        return self._owns(user_id)

    async def start(self):
        self.started.set()
        while not self.stopped:
            await asyncio.sleep(0.01)

    async def stop(self):
        self.stopped = True

    async def send_message(self, user_id, text, buttons=None, document=None):
        self.sent.append((int(user_id), text, buttons, document))

    async def send_typing(self, user_id):
        return None


def _cfg(db):
    return Config(
        telegram_token="t", db_path=db, review_before_send=False,
        backup_enabled=False, digest_enabled=False, checkin_enabled=False,
        nudges_enabled=False, appreciation_enabled=False,
    )


@pytest.fixture
def pair(test_db):
    cfg = _cfg(test_db)
    svc = BotService(cfg, test_db, MockLLMClient())
    tg = FakeChild(svc, cfg, "tg", lambda u: u < REST_ID_BASE)
    rest = FakeChild(svc, cfg, "rest", lambda u: u >= REST_ID_BASE)
    return svc, cfg, tg, rest


async def test_routing_by_owns_user(pair):
    svc, cfg, tg, rest = pair
    multi = MultiAdapter([tg, rest])
    await multi.send_message(5, "to tg")
    await multi.send_message(REST_ID_BASE + 1, "to rest", buttons=[[("a", "b")]])
    assert [m[:2] for m in tg.sent] == [(5, "to tg")]
    assert rest.sent[0][:3] == (REST_ID_BASE + 1, "to rest", [[("a", "b")]])
    assert multi.owns_user(5) and multi.owns_user(REST_ID_BASE)
    assert "tg" in multi.transport_description and "rest" in multi.transport_description


async def test_unowned_user_is_dropped_not_raised(pair):
    svc, cfg, tg, rest = pair
    only_tg = MultiAdapter([tg])
    await only_tg.send_message(REST_ID_BASE + 1, "nobody")
    assert tg.sent == []
    assert not only_tg.owns_user(REST_ID_BASE + 1)


def test_empty_children_rejected():
    with pytest.raises(ValueError):
        MultiAdapter([])


async def test_loops_started_once_and_children_started(pair):
    svc, cfg, tg, rest = pair
    multi = MultiAdapter([tg, rest])
    calls = []

    def fake_start(service, config, send, send_buttons, spawn):
        calls.append(send)
        return []

    with patch("couples_bot.adapters.base.start_background_loops", fake_start):
        task = asyncio.create_task(multi.start())
        await asyncio.wait_for(asyncio.gather(tg.started.wait(), rest.started.wait()), 2)
        await multi.stop()
        await asyncio.wait_for(task, 2)
    assert len(calls) == 1
    # the send function handed to the loops routes by owner
    await calls[0](REST_ID_BASE + 2, "loop msg")
    assert rest.sent and not tg.sent


async def test_forward_hook_routes_rest_response_to_telegram_child(test_db):
    cfg = _cfg(test_db)
    svc = BotService(cfg, test_db, MockLLMClient())
    rest = RestAdapter(svc, cfg, run_background=False)
    tg = FakeChild(svc, cfg, "tg", lambda u: u < REST_ID_BASE)
    MultiAdapter([tg, rest])
    assert rest.api.forward is not None
    await rest.api.forward(77, {
        "text": "hi", "markup": "<b>hi</b>",
        "buttons": [[{"text": "Ok", "data": "ap:1"}]], "document": None,
    })
    assert tg.sent == [(77, "<b>hi</b>", [[("Ok", "ap:1")]], None)]


async def test_telegram_run_background_false_creates_no_loops(test_db):
    cfg = _cfg(test_db)
    svc = BotService(cfg, test_db, MockLLMClient())
    app = MagicMock()
    app.create_task = MagicMock()
    off = TelegramAdapter(svc, cfg, run_background=False)
    await off._post_init(app)
    app.create_task.assert_not_called()

    on = TelegramAdapter(svc, cfg)  # default True: unchanged behaviour
    assert on.run_background is True
    created = []

    def spawn(coro):
        created.append(asyncio.ensure_future(coro))
        return created[-1]

    app.create_task = spawn
    await on._post_init(app)
    assert len(created) == 1  # delivery loop (others disabled in cfg)
    for t in created:
        t.cancel()
    await asyncio.gather(*created, return_exceptions=True)


def test_telegram_owns_only_non_rest_ids(test_db):
    adapter = TelegramAdapter(MagicMock(), _cfg(test_db))
    assert adapter.owns_user(123) and not adapter.owns_user(REST_ID_BASE)


def test_base_adapter_owns_everyone_by_default(test_db):
    cfg = _cfg(test_db)
    child = FakeChild(MagicMock(), cfg, "x", lambda u: True)
    assert BaseAdapter.owns_user(child, 1)


def test_registry_has_rest():
    assert ADAPTER_REGISTRY["rest"] is RestAdapter
    assert get_adapter("rest") is RestAdapter


@pytest.mark.parametrize("name", ["rest", "telegram+rest", "both", "telegram"])
def test_launcher_accepts_adapter_names(name, test_db, monkeypatch):
    import couples_bot.__main__ as launcher

    cfg = _cfg(test_db)
    ran = []

    def fake_run(coro):
        ran.append(coro.cr_code.co_name)
        coro.close()

    monkeypatch.setattr(sys, "argv", ["prog", "--adapter", name])
    with patch.object(launcher, "load_config", return_value=cfg), \
         patch.object(launcher, "create_llm_client", return_value=MockLLMClient()), \
         patch.object(launcher.asyncio, "run", fake_run):
        launcher.main()
    expected = {
        "telegram+rest": "_run_telegram_rest",
        "both": "_run_both",
    }.get(name, "start")
    assert ran[-1] == expected


def test_launcher_rejects_unknown_adapter(monkeypatch):
    import couples_bot.__main__ as launcher
    monkeypatch.setattr(sys, "argv", ["prog", "--adapter", "smoke-signal"])
    with pytest.raises(SystemExit):
        launcher.main()


def test_set_privacy_info_combines_transports(test_db):
    import couples_bot.__main__ as launcher
    cfg = _cfg(test_db)
    svc = BotService(cfg, test_db, MockLLMClient())
    tg = TelegramAdapter(svc, cfg, run_background=False)
    rest = RestAdapter(svc, cfg, run_background=False)
    launcher._set_privacy_info(svc, cfg, [tg, rest])
    assert "Telegram" in svc.privacy_info.transport
    assert "HTTP" in svc.privacy_info.transport
    assert svc.privacy_info.llm == "Google Gemini"
