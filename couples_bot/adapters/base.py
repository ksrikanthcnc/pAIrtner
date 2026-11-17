"""Abstract base adapter — shared infrastructure for all transport adapters."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

from couples_bot.config import Config
from couples_bot.handlers import BotService

from .loops import start_background_loops

logger = logging.getLogger(__name__)


class BaseAdapter(ABC):
    """Abstract transport adapter.

    Subclasses implement ``start()``, ``stop()``, ``send_message()`` and
    ``send_typing()`` for their specific transport.  Background loops
    (delivery, backup, digest, check-in, nudges) are started via the
    shared ``_start_background_tasks()`` helper.
    """

    # Shown in the /start and /help privacy note. Subclasses override.
    transport_description = "this server only (no third-party messaging service)"

    def __init__(self, service: BotService, config: Config) -> None:
        self.service = service
        self.config = config
        self._background_tasks: list[asyncio.Task] = []

    @abstractmethod
    async def start(self) -> None:
        """Start the adapter (blocking — runs until stopped)."""

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully shut down the adapter."""

    @abstractmethod
    async def send_message(
        self,
        user_id: int | str,
        text: str,
        buttons: list[list[tuple[str, str]]] | None = None,
        document: tuple[str, str] | None = None,
    ) -> None:
        """Send a message to a user via this transport."""

    @abstractmethod
    async def send_typing(self, user_id: int | str) -> None:
        """Indicate that the bot is typing / processing."""

    # ── shared background loops ──────────────────────────────────────────

    def owns_user(self, user_id: int) -> bool:
        """Whether this transport delivers to *user_id* (used by MultiAdapter)."""
        return True

    async def _start_background_tasks(self) -> None:
        """Start background loops, routing messages through this adapter."""
        async def _send(user_id: int, text: str) -> None:
            await self.send_message(user_id, text)

        async def _send_with_buttons(
            user_id: int, text: str, buttons: list[list[tuple[str, str]]]
        ) -> None:
            await self.send_message(user_id, text, buttons=buttons)

        self._background_tasks.extend(start_background_loops(
            self.service, self.config, _send, _send_with_buttons,
            asyncio.create_task,
        ))

    async def _cancel_background_tasks(self) -> None:
        """Cancel all running background tasks."""
        for task in self._background_tasks:
            task.cancel()
        for task in self._background_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._background_tasks.clear()
