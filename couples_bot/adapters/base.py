"""Abstract base adapter — shared infrastructure for all transport adapters."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

from couples_bot.approval import delivery_loop
from couples_bot.backup import backup_loop
from couples_bot.checkin import checkin_loop
from couples_bot.config import Config
from couples_bot.digest import digest_loop
from couples_bot.handlers import BotService
from couples_bot.nudges import nudge_loop

logger = logging.getLogger(__name__)


class BaseAdapter(ABC):
    """Abstract transport adapter.

    Subclasses implement ``start()``, ``stop()``, ``send_message()`` and
    ``send_typing()`` for their specific transport.  Background loops
    (delivery, backup, digest, check-in, nudges) are started via the
    shared ``_start_background_tasks()`` helper.
    """

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

    async def _start_background_tasks(self) -> None:
        """Start background loops, routing messages through this adapter."""

        async def _send(user_id: int, text: str) -> None:
            await self.send_message(user_id, text)

        async def _send_with_buttons(
            user_id: int, text: str, buttons: list[list[tuple[str, str]]]
        ) -> None:
            await self.send_message(user_id, text, buttons=buttons)

        task = asyncio.create_task(delivery_loop(
            self.service.db_path, _send,
            send_buttons_func=_send_with_buttons,
            poll_interval=self.config.delivery_check_interval_seconds,
            review_before_send=self.config.review_before_send,
        ))
        self._background_tasks.append(task)
        logger.info("Delivery loop started")

        if self.config.backup_enabled:
            task = asyncio.create_task(backup_loop(
                self.config.db_path,
                self.config.backup_directory,
                daily_count=self.config.backup_daily_count,
                weekly_count=self.config.backup_weekly_count,
            ))
            self._background_tasks.append(task)
            logger.info("Backup loop started")

        if self.config.digest_enabled:
            task = asyncio.create_task(digest_loop(
                self.service.db_path,
                self.service.llm,
                _send,
                digest_time=self.config.digest_time,
                digest_tz=self.config.digest_timezone,
            ))
            self._background_tasks.append(task)
            logger.info("Digest loop started")

        if self.config.checkin_enabled:
            task = asyncio.create_task(checkin_loop(
                self.service.db_path,
                self.service.llm,
                _send,
                checkin_day=self.config.checkin_day,
                checkin_time=self.config.checkin_time,
                checkin_tz=self.config.checkin_timezone,
                checkin_questions=self.config.checkin_questions,
            ))
            self._background_tasks.append(task)
            logger.info("Check-in loop started")

        if self.config.nudges_enabled or self.config.appreciation_enabled:
            task = asyncio.create_task(nudge_loop(
                self.service.db_path,
                self.service.llm,
                _send,
                config=self.config,
                poll_interval=self.config.nudges_interval_hours * 3600,
            ))
            self._background_tasks.append(task)
            logger.info("Nudge loop started")

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
