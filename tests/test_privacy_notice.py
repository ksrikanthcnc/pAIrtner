"""Tests for the transport- and LLM-aware privacy notice."""
from __future__ import annotations

from pairtner.adapters.cli_adapter import CLIAdapter
from pairtner.adapters.telegram_adapter import TelegramAdapter
from pairtner.adapters.websocket_adapter import WebSocketAdapter
from pairtner.config import Config
from pairtner.handlers.service import PrivacyInfo
from pairtner.i18n import _STRINGS, t
from pairtner.llm import describe_llm


def _cfg(**kw) -> Config:
    return Config(telegram_token="t", gemini_api_key="SECRET-KEY-123", **kw)


async def test_default_notice_names_telegram_and_gemini(service, paired_users):
    a, _ = paired_users
    for resp in (await service.handle_help(a), await service.handle_start(a, "Alice")):
        text = resp[0].text
        assert "Telegram" in text and "Gemini" in text
        assert "not a substitute for professional therapy" in text


async def test_unpaired_start_includes_notice(service):
    text = (await service.handle_start(7777, "New"))[0].text
    assert "Telegram" in text and "Gemini" in text


async def test_rest_local_model_notice(service, paired_users):
    a, _ = paired_users
    cfg = _cfg(
        llm_provider="openai_compat", llm_base_url="http://127.0.0.1:11434/v1",
        llm_model_default="qwen3:8b",
    )
    service.privacy_info = PrivacyInfo(
        transport="this server only (no third-party messaging service)",
        llm=describe_llm(cfg),
    )
    for resp in (await service.handle_help(a), await service.handle_start(a, "Alice")):
        # The help body mentions Gemini keys (/setup); check the notice itself.
        notice = resp[0].text.split("Privacy note:")[1].split("\n\n")[0]
        assert "Telegram" not in notice and "Gemini" not in notice
        assert "this server" in notice
        assert "qwen3:8b" in notice and "local model" in notice


def test_describe_llm_gemini_never_leaks_key():
    desc = describe_llm(_cfg())
    assert desc == "Google Gemini"
    assert "SECRET-KEY-123" not in desc


def test_describe_llm_local_hosts():
    for url in (
        "http://localhost:11434/v1", "http://127.0.0.1:8080", "http://[::1]:8080/v1",
        "http://host.docker.internal:11434/v1", "http://192.168.1.20:11434/v1",
        "http://10.0.0.5/v1", "http://172.16.3.4/v1",
    ):
        cfg = _cfg(llm_provider="openai_compat", llm_base_url=url,
                   llm_model_default="m1", llm_api_key="KEY-ABC")
        desc = describe_llm(cfg)
        assert "local model" in desc and "m1" in desc, url
        assert "KEY-ABC" not in desc


def test_describe_llm_external_host():
    cfg = _cfg(llm_provider="openai_compat", llm_base_url="https://api.example.com/v1",
               llm_model_default="m1", llm_api_key="KEY-ABC")
    desc = describe_llm(cfg)
    assert "api.example.com" in desc and "local" not in desc
    assert "KEY-ABC" not in desc
    public_ip = _cfg(llm_provider="openai_compat", llm_base_url="http://8.8.8.8/v1")
    assert "8.8.8.8" in describe_llm(public_ip) and "local" not in describe_llm(public_ip)


def test_transport_descriptions():
    assert "Telegram" in TelegramAdapter.transport_description
    assert "this server" in WebSocketAdapter.transport_description
    assert "this server" in CLIAdapter.transport_description


def test_privacy_keys_exist():
    """Privacy-related keys exist in the string catalog."""
    for key in ("privacy_note", "not_therapy", "mediate_usage", "mediate_no_input",
                "fact_now_shared", "fact_now_private", "btn_share_fact",
                "btn_unshare_fact", "whatdoyouknow_more"):
        assert key in _STRINGS["en"], f"Missing key: {key}"
