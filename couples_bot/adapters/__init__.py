"""Adapter registry — maps adapter names to their classes."""

from __future__ import annotations

from .base import BaseAdapter
from .cli_adapter import CLIAdapter
from .telegram_adapter import TelegramAdapter
from .websocket_adapter import WebSocketAdapter

ADAPTER_REGISTRY: dict[str, type[BaseAdapter]] = {
    "telegram": TelegramAdapter,
    "websocket": WebSocketAdapter,
    "cli": CLIAdapter,
}


def get_adapter(name: str) -> type[BaseAdapter]:
    """Look up an adapter class by name.

    Raises ``ValueError`` for unknown adapter names.
    """
    cls = ADAPTER_REGISTRY.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown adapter: {name!r}. "
            f"Available: {', '.join(sorted(ADAPTER_REGISTRY))}"
        )
    return cls


__all__ = ["BaseAdapter", "get_adapter", "ADAPTER_REGISTRY"]
