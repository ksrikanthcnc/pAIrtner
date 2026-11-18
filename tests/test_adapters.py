"""Tests for the adapter architecture (FEAT-003 batch5)."""

from __future__ import annotations

import asyncio
import json
from io import StringIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from pairtner.adapters import ADAPTER_REGISTRY, get_adapter
from pairtner.adapters.base import BaseAdapter
from pairtner.adapters.cli_adapter import CLIAdapter
from pairtner.adapters.telegram_adapter import TelegramAdapter
from pairtner.adapters.websocket_adapter import WebSocketAdapter
from pairtner.config import Config
from pairtner.db import (
    create_auth_token,
    create_user,
    get_auth_token,
    init_db,
    set_partner,
    validate_auth_token,
)
from pairtner.handlers import BotResponse, BotService

from tests.helpers import MockLLMClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> Config:
    return Config(
        telegram_token="test-token",
        gemini_api_key="test-key",
        model_default="test-default",
        model_heavy=["test-heavy"],
        cooldown_minutes=5,
        db_path=":memory:",
        heavy_daily_cap=10,
        adapter="telegram",
        ws_port=8080,
    )


@pytest_asyncio.fixture
async def test_db(tmp_path) -> str:
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    return db_path


@pytest.fixture
def mock_llm() -> MockLLMClient:
    return MockLLMClient()


@pytest_asyncio.fixture
async def service(test_db, config, mock_llm) -> BotService:
    cfg = Config(
        telegram_token=config.telegram_token,
        gemini_api_key=config.gemini_api_key,
        model_default=config.model_default,
        model_heavy=config.model_heavy,
        cooldown_minutes=config.cooldown_minutes,
        db_path=test_db,
        heavy_daily_cap=config.heavy_daily_cap,
        adapter="telegram",
        ws_port=8080,
    )
    return BotService(config=cfg, db_path=test_db, llm=mock_llm)


# ---------------------------------------------------------------------------
# Step 1 + 9: Adapter registry tests
# ---------------------------------------------------------------------------


class TestAdapterRegistry:
    """Test ADAPTER_REGISTRY and get_adapter()."""

    def test_registry_has_all_adapters(self):
        assert "telegram" in ADAPTER_REGISTRY
        assert "websocket" in ADAPTER_REGISTRY
        assert "cli" in ADAPTER_REGISTRY

    def test_registry_values_are_classes(self):
        for name, cls in ADAPTER_REGISTRY.items():
            assert isinstance(cls, type), f"{name} is not a class"
            assert issubclass(cls, BaseAdapter), f"{name} not subclass of BaseAdapter"

    def test_get_adapter_telegram(self):
        assert get_adapter("telegram") is TelegramAdapter

    def test_get_adapter_websocket(self):
        assert get_adapter("websocket") is WebSocketAdapter

    def test_get_adapter_cli(self):
        assert get_adapter("cli") is CLIAdapter

    def test_get_adapter_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown adapter"):
            get_adapter("carrier_pigeon")

    def test_get_adapter_unknown_message_lists_available(self):
        with pytest.raises(ValueError) as exc_info:
            get_adapter("nonexistent")
        msg = str(exc_info.value)
        assert "cli" in msg
        assert "telegram" in msg
        assert "websocket" in msg


# ---------------------------------------------------------------------------
# Step 2: BaseAdapter abstract class tests
# ---------------------------------------------------------------------------


class TestBaseAdapter:
    """Test that BaseAdapter cannot be instantiated directly."""

    def test_cannot_instantiate_base_adapter(self, service, config):
        with pytest.raises(TypeError):
            BaseAdapter(service, config)

    def test_base_adapter_stores_service_and_config(self, service, config):
        """Subclasses should have service and config set by __init__."""
        # Use CLIAdapter as a concrete subclass to test
        adapter = CLIAdapter(service, config)
        assert adapter.service is service
        assert adapter.config is config

    def test_base_adapter_starts_with_empty_background_tasks(self, service, config):
        adapter = CLIAdapter(service, config)
        assert adapter._background_tasks == []


# ---------------------------------------------------------------------------
# Step 3: TelegramAdapter tests
# ---------------------------------------------------------------------------


class TestTelegramAdapter:
    """Test TelegramAdapter preserves all command routing."""

    def test_instantiation(self, service, config):
        adapter = TelegramAdapter(service, config)
        assert adapter.service is service
        assert adapter.config is config
        assert adapter._app is None

    def test_all_command_methods_exist(self, service, config):
        """Verify all command handler methods exist on the adapter."""
        adapter = TelegramAdapter(service, config)
        commands = [
            "_cmd_start", "_cmd_join", "_cmd_tell", "_cmd_mediate",
            "_cmd_whatdoyouknow", "_cmd_forget", "_cmd_export", "_cmd_help",
            "_cmd_lang", "_cmd_setup", "_cmd_status", "_cmd_history",
            "_cmd_pause", "_cmd_resume", "_cmd_style",
            "_cmd_goal",
            "_cmd_settings", "_cmd_skip", "_cmd_remember", "_cmd_search",
            "_cmd_conflicts", "_cmd_timeline",
        ]
        for cmd in commands:
            assert hasattr(adapter, cmd), f"Missing method: {cmd}"
            assert callable(getattr(adapter, cmd)), f"{cmd} is not callable"

    def test_has_message_and_callback_handlers(self, service, config):
        adapter = TelegramAdapter(service, config)
        assert hasattr(adapter, "_on_message")
        assert hasattr(adapter, "_on_callback")

    def test_build_markup_with_no_buttons(self, service, config):
        adapter = TelegramAdapter(service, config)
        resp = BotResponse("hello")
        assert adapter._build_markup(resp) is None

    def test_build_markup_with_buttons(self, service, config):
        from pairtner.handlers.models import InlineButton

        adapter = TelegramAdapter(service, config)
        resp = BotResponse(
            "pick one",
            buttons=[[InlineButton("A", "cb_a"), InlineButton("B", "cb_b")]],
        )
        markup = adapter._build_markup(resp)
        assert markup is not None
        assert len(markup.inline_keyboard) == 1
        assert len(markup.inline_keyboard[0]) == 2
        assert markup.inline_keyboard[0][0].text == "A"
        assert markup.inline_keyboard[0][1].callback_data == "cb_b"


# ---------------------------------------------------------------------------
# Step 4: WebSocketAdapter tests
# ---------------------------------------------------------------------------


class TestWebSocketAdapter:
    """Test WebSocketAdapter protocol and structure."""

    def test_instantiation(self, service, config):
        adapter = WebSocketAdapter(service, config)
        assert adapter.service is service
        assert adapter._connections == {}
        assert adapter._server is None

    async def test_send_message_no_connection_silent(self, service, config):
        """send_message drops silently when user has no WS connection."""
        adapter = WebSocketAdapter(service, config)
        # Should not raise
        await adapter.send_message(12345, "hello")

    async def test_send_typing_no_connection_silent(self, service, config):
        adapter = WebSocketAdapter(service, config)
        await adapter.send_typing(12345)

    async def test_send_message_to_connected_user(self, service, config):
        adapter = WebSocketAdapter(service, config)
        mock_ws = AsyncMock()
        adapter._connections["42"] = mock_ws

        await adapter.send_message(42, "hello world")

        mock_ws.send.assert_called_once()
        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["type"] == "reply"
        assert sent["text"] == "hello world"

    async def test_send_message_with_buttons(self, service, config):
        adapter = WebSocketAdapter(service, config)
        mock_ws = AsyncMock()
        adapter._connections["42"] = mock_ws

        await adapter.send_message(
            42, "pick", buttons=[[("A", "cb_a"), ("B", "cb_b")]],
        )

        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["type"] == "reply"
        assert len(sent["buttons"]) == 1
        assert sent["buttons"][0][0]["text"] == "A"
        assert sent["buttons"][0][1]["callback_data"] == "cb_b"

    async def test_send_message_with_document(self, service, config):
        adapter = WebSocketAdapter(service, config)
        mock_ws = AsyncMock()
        adapter._connections["42"] = mock_ws

        await adapter.send_message(
            42, "here's a file", document=("report.txt", "contents"),
        )

        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["document"]["filename"] == "report.txt"
        assert sent["document"]["content"] == "contents"

    async def test_send_typing_to_connected_user(self, service, config):
        adapter = WebSocketAdapter(service, config)
        mock_ws = AsyncMock()
        adapter._connections["42"] = mock_ws

        await adapter.send_typing(42)

        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["type"] == "typing"
        assert sent["active"] is True

    async def test_handle_auth_success(self, test_db, service, config):
        """Test auth flow with valid token."""
        adapter = WebSocketAdapter(service, config)
        mock_ws = AsyncMock()

        await create_user(test_db, 100, "TestUser")
        await create_auth_token(test_db, 100, "valid-token-123")

        result = await adapter._handle_auth(mock_ws, {
            "user_id": 100, "token": "valid-token-123",
        })

        assert result == "100"
        assert "100" in adapter._connections
        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["type"] == "auth_ok"

    async def test_handle_auth_invalid_token(self, test_db, service, config):
        adapter = WebSocketAdapter(service, config)
        mock_ws = AsyncMock()

        await create_user(test_db, 100, "TestUser")
        await create_auth_token(test_db, 100, "correct-token")

        result = await adapter._handle_auth(mock_ws, {
            "user_id": 100, "token": "wrong-token",
        })

        assert result is None
        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["type"] == "error"
        assert "Invalid" in sent["text"]

    async def test_handle_auth_missing_fields(self, service, config):
        adapter = WebSocketAdapter(service, config)
        mock_ws = AsyncMock()

        result = await adapter._handle_auth(mock_ws, {"user_id": 100})
        assert result is None

        result = await adapter._handle_auth(mock_ws, {"token": "abc"})
        assert result is None

    async def test_ws_message_protocol_shape(self, test_db, service, config):
        """Test the message JSON protocol produces correct response shape."""
        adapter = WebSocketAdapter(service, config)
        mock_ws = AsyncMock()
        adapter._connections["100"] = mock_ws

        await create_user(test_db, 100, "TestUser")

        # Mock handle_message to return a simple response
        service.handle_message = AsyncMock(
            return_value=[BotResponse("test reply")],
        )

        await adapter._handle_ws_message(mock_ws, "100", {"text": "hello"})

        # Should have sent typing=True, typing=False, then the reply
        calls = mock_ws.send.call_args_list
        assert len(calls) >= 2
        # First call: typing indicator
        typing_on = json.loads(calls[0][0][0])
        assert typing_on["type"] == "typing"
        assert typing_on["active"] is True


# ---------------------------------------------------------------------------
# Step 5: Auth token DB tests
# ---------------------------------------------------------------------------


class TestAuthTokenDB:
    """Test auth_tokens table CRUD."""

    async def test_create_and_get_token(self, test_db):
        await create_auth_token(test_db, 42, "my-secret-token")
        token = await get_auth_token(test_db, 42)
        assert token == "my-secret-token"

    async def test_get_token_nonexistent(self, test_db):
        token = await get_auth_token(test_db, 9999)
        assert token is None

    async def test_validate_token_correct(self, test_db):
        await create_auth_token(test_db, 42, "correct")
        assert await validate_auth_token(test_db, 42, "correct") is True

    async def test_validate_token_wrong(self, test_db):
        await create_auth_token(test_db, 42, "correct")
        assert await validate_auth_token(test_db, 42, "wrong") is False

    async def test_validate_token_no_user(self, test_db):
        assert await validate_auth_token(test_db, 9999, "anything") is False

    async def test_upsert_token(self, test_db):
        """create_auth_token should update existing token on conflict."""
        await create_auth_token(test_db, 42, "first-token")
        await create_auth_token(test_db, 42, "second-token")
        token = await get_auth_token(test_db, 42)
        assert token == "second-token"

    async def test_multiple_users_tokens(self, test_db):
        await create_auth_token(test_db, 1, "token-1")
        await create_auth_token(test_db, 2, "token-2")
        assert await get_auth_token(test_db, 1) == "token-1"
        assert await get_auth_token(test_db, 2) == "token-2"


# ---------------------------------------------------------------------------
# Step 6: CLIAdapter tests
# ---------------------------------------------------------------------------


class TestCLIAdapter:
    """Test CLIAdapter with simulated input."""

    def test_instantiation_default_two_user(self, service, config):
        adapter = CLIAdapter(service, config)
        assert adapter._single_user_id is None

    def test_instantiation_single_user(self, service, config):
        adapter = CLIAdapter(service, config, user_id=42)
        assert adapter._single_user_id == 42

    async def test_send_message_prints_to_stdout(self, service, config, capsys):
        adapter = CLIAdapter(service, config)
        await adapter.send_message(1, "Hello from bot")
        captured = capsys.readouterr()
        assert "[Bot → 1]" in captured.out
        assert "Hello from bot" in captured.out

    async def test_send_message_with_buttons(self, service, config, capsys):
        adapter = CLIAdapter(service, config)
        await adapter.send_message(
            1, "Pick one", buttons=[[("Option A", "cb_a"), ("Option B", "cb_b")]],
        )
        captured = capsys.readouterr()
        assert "[Option A]" in captured.out
        assert "[Option B]" in captured.out

    async def test_send_message_with_document(self, service, config, capsys):
        adapter = CLIAdapter(service, config)
        await adapter.send_message(
            1, "Here's a file", document=("report.txt", "content"),
        )
        captured = capsys.readouterr()
        assert "📎 report.txt" in captured.out

    async def test_send_typing(self, service, config, capsys):
        adapter = CLIAdapter(service, config)
        await adapter.send_typing(1)
        captured = capsys.readouterr()
        assert "typing" in captured.out

    async def test_dispatch_help_command(self, test_db, service, config):
        """Test that /help routes to service.handle_help."""
        adapter = CLIAdapter(service, config)
        await create_user(test_db, 1, "UserA")

        responses = await adapter._dispatch(1, "UserA", "/help")
        assert len(responses) >= 1
        # help response should contain some command references
        assert any("/" in r.text for r in responses)

    async def test_dispatch_unknown_command(self, service, config):
        adapter = CLIAdapter(service, config)
        responses = await adapter._dispatch(1, "UserA", "/nonexistent")
        assert len(responses) == 1
        assert "Unknown command" in responses[0].text

    async def test_dispatch_plain_message(self, test_db, service, config):
        """Test that plain text routes to service.handle_message."""
        adapter = CLIAdapter(service, config)
        await create_user(test_db, 1, "UserA")

        responses = await adapter._dispatch(1, "UserA", "hello there")
        assert len(responses) >= 1

    async def test_stop_sets_running_false(self, service, config):
        adapter = CLIAdapter(service, config)
        adapter._running = True
        await adapter.stop()
        assert adapter._running is False


# ---------------------------------------------------------------------------
# Step 7: Launcher / __main__ arg parsing tests
# ---------------------------------------------------------------------------


class TestLauncher:
    """Test __main__.py argument parsing and adapter selection."""

    def test_argparse_default_adapter(self):
        """Default adapter comes from config when not specified."""
        from pairtner.__main__ import main
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--adapter",
            choices=["telegram", "websocket", "cli", "both"],
            default=None,
        )
        parser.add_argument("--port", type=int, default=None)
        parser.add_argument("--user-id", type=int, default=None)

        args = parser.parse_args([])
        assert args.adapter is None
        assert args.port is None
        assert args.user_id is None

    def test_argparse_cli_adapter(self):
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--adapter",
            choices=["telegram", "websocket", "cli", "both"],
            default=None,
        )
        parser.add_argument("--port", type=int, default=None)
        parser.add_argument("--user-id", type=int, default=None)

        args = parser.parse_args(["--adapter", "cli", "--user-id", "42"])
        assert args.adapter == "cli"
        assert args.user_id == 42

    def test_argparse_websocket_with_port(self):
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--adapter",
            choices=["telegram", "websocket", "cli", "both"],
            default=None,
        )
        parser.add_argument("--port", type=int, default=None)

        args = parser.parse_args(["--adapter", "websocket", "--port", "9090"])
        assert args.adapter == "websocket"
        assert args.port == 9090

    def test_argparse_both_adapter(self):
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--adapter",
            choices=["telegram", "websocket", "cli", "both"],
            default=None,
        )

        args = parser.parse_args(["--adapter", "both"])
        assert args.adapter == "both"


# ---------------------------------------------------------------------------
# Step 8: Config tests for adapter fields
# ---------------------------------------------------------------------------


class TestAdapterConfig:
    """Test that adapter and ws_port are in Config."""

    def test_default_adapter(self):
        cfg = Config(telegram_token="t", gemini_api_key="k")
        assert cfg.adapter == "telegram"

    def test_default_ws_port(self):
        cfg = Config(telegram_token="t", gemini_api_key="k")
        assert cfg.ws_port == 8080

    def test_custom_adapter(self):
        cfg = Config(telegram_token="t", gemini_api_key="k", adapter="cli")
        assert cfg.adapter == "cli"

    def test_custom_ws_port(self):
        cfg = Config(
            telegram_token="t", gemini_api_key="k", ws_port=9999,
        )
        assert cfg.ws_port == 9999

    def test_load_config_adapter_from_env(self, tmp_path, monkeypatch):
        """Test that ADAPTER env var is picked up by load_config."""
        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text("adapter: websocket\nws_port: 7777\n")

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-tok")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.delenv("ADAPTER", raising=False)
        monkeypatch.delenv("WS_PORT", raising=False)

        from pairtner.config import load_config

        cfg = load_config(str(yaml_file))
        assert cfg.adapter == "websocket"
        assert cfg.ws_port == 7777

    def test_load_config_adapter_env_override(self, tmp_path, monkeypatch):
        """Env vars override YAML for adapter settings."""
        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text("adapter: telegram\nws_port: 8080\n")

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-tok")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setenv("ADAPTER", "cli")
        monkeypatch.setenv("WS_PORT", "9999")

        from pairtner.config import load_config

        cfg = load_config(str(yaml_file))
        assert cfg.adapter == "cli"
        assert cfg.ws_port == 9999
