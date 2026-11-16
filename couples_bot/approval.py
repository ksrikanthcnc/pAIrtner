"""Background delivery loop for approved relay messages."""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from couples_bot import db as db_mod
from couples_bot.i18n import t

logger = logging.getLogger(__name__)

# Type for the send callback provided by the adapter layer.
SendFunc = Callable[[int, str], Awaitable[None]]


async def delivery_loop(
    db_path: str,
    send_func: SendFunc,
    *,
    poll_interval: float = 30.0,
) -> None:
    """Continuously deliver approved messages whose cooldown has elapsed.

    Runs forever; intended to be launched as a background ``asyncio.Task``.

    Parameters
    ----------
    db_path:
        Path to the SQLite database.
    send_func:
        ``async def send(user_id: int, text: str) -> None`` — sends a
        Telegram message to the given user.
    poll_interval:
        Seconds between delivery checks (default 30).
    """
    while True:
        await asyncio.sleep(poll_interval)
        try:
            rows = await db_mod.get_deliverable(db_path)
            for row in rows:
                try:
                    receiver_id = row["receiver_id"]
                    sender_id = row["sender_id"]

                    # Skip delivery if either party is paused
                    receiver = await db_mod.get_user(db_path, receiver_id)
                    sender = await db_mod.get_user(db_path, sender_id)
                    if (receiver and receiver.get("paused")) or (
                        sender and sender.get("paused")
                    ):
                        logger.debug(
                            "Skipping pending %d — paused (sender=%s, receiver=%s)",
                            row["id"],
                            bool(sender and sender.get("paused")),
                            bool(receiver and receiver.get("paused")),
                        )
                        continue

                    text = row.get("edited_draft") or row["draft"]

                    # Look up sender to determine partner's lang
                    receiver = await db_mod.get_user(db_path, receiver_id)
                    lang = (receiver or {}).get("lang") or "en"

                    message = t(lang, "relay_sent_to_partner", message=text)
                    await send_func(receiver_id, message)
                    await db_mod.mark_as_sent(db_path, row["id"])

                    # Notify sender that it was delivered
                    sender = await db_mod.get_user(db_path, sender_id)
                    slang = (sender or {}).get("lang") or "en"
                    await send_func(sender_id, t(slang, "relay_delivered"))

                    logger.info(
                        "Delivered pending %d from %d to %d",
                        row["id"], sender_id, receiver_id,
                    )
                except Exception:
                    logger.exception(
                        "Failed to deliver pending %d", row["id"]
                    )
        except Exception:
            logger.exception("Error in delivery loop")
