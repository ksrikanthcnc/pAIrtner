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

# Type for sending a message with inline buttons.
# buttons: [[("text", "callback_data"), ...], ...]
ButtonRow = list[tuple[str, str]]
SendWithButtonsFunc = Callable[[int, str, list[ButtonRow]], Awaitable[None]]


async def delivery_loop(
    db_path: str,
    send_func: SendFunc,
    *,
    send_buttons_func: SendWithButtonsFunc | None = None,
    poll_interval: float = 30.0,
    review_before_send: bool = True,
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
    send_buttons_func:
        Like *send_func* but also accepts inline-button rows.
    poll_interval:
        Seconds between delivery checks (default 30).
    review_before_send:
        If True, batch review prompt is shown before delivering accumulated
        messages.  If False, batches are delivered directly when the
        cooldown timer elapses.
    """
    while True:
        await asyncio.sleep(poll_interval)
        try:
            # ── Individual pending rows (existing behaviour) ─────────
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

                    # Track relay in open conflicts (FEAT-002)
                    cid = db_mod.make_couple_id(sender_id, receiver_id)
                    for oc in await db_mod.get_open_conflicts(db_path, cid):
                        await db_mod.add_relay_to_conflict(
                            db_path, oc["id"], row["id"],
                        )

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

            # ── Batch delivery ───────────────────────────────────────
            batches = await db_mod.get_deliverable_batches(db_path)
            for batch in batches:
                try:
                    await _process_batch(
                        db_path,
                        batch,
                        send_func,
                        send_buttons_func,
                        review_before_send,
                    )
                except Exception:
                    logger.exception(
                        "Failed to process batch %d", batch["id"]
                    )

        except Exception:
            logger.exception("Error in delivery loop")


async def _process_batch(
    db_path: str,
    batch: dict,
    send_func: SendFunc,
    send_buttons_func: SendWithButtonsFunc | None,
    review_before_send: bool,
) -> None:
    """Handle a single batch that is ready for delivery or review."""
    sender_id = batch["sender_id"]
    receiver_id = batch["receiver_id"]

    receiver = await db_mod.get_user(db_path, receiver_id)
    sender = await db_mod.get_user(db_path, sender_id)

    # Skip if either party is paused
    if (receiver and receiver.get("paused")) or (
        sender and sender.get("paused")
    ):
        logger.debug("Skipping batch %d — paused", batch["id"])
        return

    if batch["status"] == "accumulating":
        if review_before_send:
            # Transition to review — notify sender with buttons
            await db_mod.update_batch_status(db_path, batch["id"], "reviewing")
            items = await db_mod.get_batch_items(db_path, batch["id"])
            count = len(items)
            slang = (sender or {}).get("lang") or "en"
            partner_name = (receiver or {}).get("name") or "your partner"

            prompt = t(
                slang, "batch_review_prompt",
                count=count, partner=partner_name,
            )

            if send_buttons_func:
                buttons: list[ButtonRow] = [[
                    (t(slang, "btn_send_all"), f"ba:{batch['id']}"),
                    (t(slang, "btn_review_each"), f"br:{batch['id']}"),
                    (t(slang, "btn_discard_all"), f"bd:{batch['id']}"),
                ]]
                await send_buttons_func(sender_id, prompt, buttons)
            else:
                await send_func(sender_id, prompt)

            logger.info(
                "Batch %d moved to reviewing, sender %d notified",
                batch["id"], sender_id,
            )
        else:
            # Direct delivery — skip review
            await _deliver_batch(
                db_path, batch, sender, receiver, send_func
            )

    elif batch["status"] == "approved":
        # Already reviewed and approved — deliver
        await _deliver_batch(
            db_path, batch, sender, receiver, send_func
        )


async def _deliver_batch(
    db_path: str,
    batch: dict,
    sender: dict | None,
    receiver: dict | None,
    send_func: SendFunc,
) -> None:
    """Merge batch items and deliver as a single message."""
    items = await db_mod.get_batch_items(db_path, batch["id"])
    # Collect drafts from non-discarded items
    drafts = [
        (i.get("edited_draft") or i["draft"])
        for i in items
        if i["status"] != "discarded"
    ]

    if not drafts:
        await db_mod.update_batch_status(db_path, batch["id"], "discarded")
        return

    merged = "\n\n".join(drafts)
    receiver_id = batch["receiver_id"]
    sender_id = batch["sender_id"]

    rlang = (receiver or {}).get("lang") or "en"
    await send_func(
        receiver_id, t(rlang, "relay_sent_to_partner", message=merged)
    )
    await db_mod.update_batch_status(db_path, batch["id"], "sent")

    slang = (sender or {}).get("lang") or "en"
    await send_func(sender_id, t(slang, "relay_delivered"))

    logger.info(
        "Delivered batch %d (%d items) from %d to %d",
        batch["id"], len(drafts), sender_id, receiver_id,
    )
