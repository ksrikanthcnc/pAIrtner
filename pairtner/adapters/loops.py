"""Shared background-loop starter used by every adapter.

Keeping this in one place means Telegram, the base adapter and the
multi-adapter start exactly the same five loops with the same settings.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from pairtner.features.approval import delivery_loop
from pairtner.features.backup import backup_loop
from pairtner.features.checkin import checkin_loop
from pairtner.config import Config
from pairtner.features.digest import digest_loop
from pairtner.features.nudges import nudge_loop

logger = logging.getLogger(__name__)

SendFunc = Callable[[int, str], Awaitable[None]]
SendButtonsFunc = Callable[[int, str, list[list[tuple[str, str]]]], Awaitable[None]]


def start_background_loops(
    service: Any,
    config: Config,
    send: SendFunc,
    send_buttons: SendButtonsFunc,
    spawn: Callable[[Awaitable[None]], Any],
) -> list[Any]:
    """Start delivery, backup, digest, check-in and nudge loops via *spawn*.

    ``spawn`` schedules a coroutine and returns its task (``asyncio.create_task``
    or ``Application.create_task``). Returns the spawned tasks.
    """
    tasks: list[Any] = []

    tasks.append(spawn(delivery_loop(
        service.db_path, send,
        send_buttons_func=send_buttons,
        poll_interval=config.delivery_check_interval_seconds,
        review_before_send=config.review_before_send,
    )))
    logger.info("Delivery loop started")

    if config.backup_enabled:
        tasks.append(spawn(backup_loop(
            config.db_path,
            config.backup_directory,
            daily_count=config.backup_daily_count,
            weekly_count=config.backup_weekly_count,
            interval_seconds=config.loops_backup_interval_seconds,
        )))
        logger.info("Backup loop started")

    if config.digest_enabled:
        tasks.append(spawn(digest_loop(
            service.db_path,
            service.llm,
            send,
            digest_time=config.digest_time,
            digest_tz=config.digest_timezone,
            poll_interval=config.loops_digest_poll_seconds,
        )))
        logger.info("Digest loop started")

    if config.checkin_enabled:
        tasks.append(spawn(checkin_loop(
            service.db_path,
            service.llm,
            send,
            checkin_day=config.checkin_day,
            checkin_time=config.checkin_time,
            checkin_tz=config.checkin_timezone,
            checkin_questions=config.checkin_questions,
            poll_interval=config.loops_checkin_poll_seconds,
        )))
        logger.info("Check-in loop started")

    if config.nudges_enabled or config.appreciation_enabled:
        tasks.append(spawn(nudge_loop(
            service.db_path,
            service.llm,
            send,
            config=config,
            poll_interval=(
                config.loops_nudge_interval_seconds
                or config.nudges_interval_hours * 3600
            ),
        )))
        logger.info("Nudge loop started")

    # Memory maintenance: aging, auto-outdating, consolidation
    from pairtner.core.memory_maintenance import memory_maintenance_loop

    tasks.append(spawn(memory_maintenance_loop(
        service.db_path,
        service.llm,
        interval=config.loops_memory_interval_seconds
        if hasattr(config, "loops_memory_interval_seconds")
        else 3600,
    )))
    logger.info("Memory maintenance loop started")

    return tasks
