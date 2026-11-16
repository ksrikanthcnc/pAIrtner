"""Callback-query handler methods (inline button presses).

These methods are mixed into :class:`~couples_bot.handlers.service.BotService`
via the :class:`CallbacksMixin` base class.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from couples_bot import db as db_mod
from couples_bot.i18n import t

from .models import BotResponse, InlineButton


# Voice name → display label mapping (shared with commands.py)
_VOICE_LABELS: dict[str, str] = {
    "casual_en": "Casual English",
    "telugu": "Telugu",
    "hindi": "Hindi",
    "formal_en": "Formal English",
}


class CallbacksMixin:
    """Mixin providing callback-query handlers for BotService."""

    # ── approval callbacks ───────────────────────────────────────────────

    async def _cb_approve(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Approve a relay draft — either immediately or via batch cooldown."""
        skip_cooldown = data.startswith("apn:")
        pending_id = int(data.split(":")[1])

        pending = await db_mod.get_pending(self.db_path, pending_id)
        if not pending or pending["sender_id"] != user_id:
            return [BotResponse(t(lang, "error_generic"))]

        if skip_cooldown:
            send_after = datetime.now(timezone.utc).isoformat()
            await db_mod.update_pending_status(
                self.db_path, pending_id, "approved", send_after
            )
            msg = t(lang, "approved_now")
        else:
            send_after = (
                datetime.now(timezone.utc)
                + timedelta(minutes=self.config.cooldown_minutes)
            ).isoformat()
            await db_mod.update_pending_status(
                self.db_path, pending_id, "approved"
            )

            draft = pending.get("edited_draft") or pending["draft"]
            existing_batch = await db_mod.get_active_batch_for_sender(
                self.db_path, user_id, pending["receiver_id"]
            )
            batch = await db_mod.create_or_get_active_batch(
                self.db_path, user_id, pending["receiver_id"], send_after
            )
            await db_mod.add_item_to_batch(self.db_path, batch["id"], draft)

            # Only reset timer for new batches, or when behavior is 'reset'
            if not existing_batch or self.config.cooldown_behavior == "reset":
                await db_mod.reset_batch_timer(self.db_path, batch["id"], send_after)

            msg = t(lang, "approved", minutes=self.config.cooldown_minutes)

        # Check if delivery is paused for either party
        receiver = await db_mod.get_user(self.db_path, pending["receiver_id"])
        sender_user = await db_mod.get_user(self.db_path, user_id)
        if (receiver and receiver.get("paused")) or (
            sender_user and sender_user.get("paused")
        ):
            msg += "\n\n" + t(lang, "delivery_paused_notice")

        return [BotResponse(msg)]

    async def _cb_edit(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Enter edit mode for a pending draft."""
        pending_id = int(data.split(":")[1])

        pending = await db_mod.get_pending(self.db_path, pending_id)
        if not pending or pending["sender_id"] != user_id:
            return [BotResponse(t(lang, "error_generic"))]

        self._edit_state[user_id] = pending_id
        return [BotResponse(t(lang, "edit_prompt"))]

    async def _cb_discard(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Discard a pending relay draft."""
        pending_id = int(data.split(":")[1])

        pending = await db_mod.get_pending(self.db_path, pending_id)
        if not pending or pending["sender_id"] != user_id:
            return [BotResponse(t(lang, "error_generic"))]

        await db_mod.update_pending_status(
            self.db_path, pending_id, "discarded"
        )
        return [BotResponse(t(lang, "discarded"))]

    async def _cb_choose_relay(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """User chose to relay the LLM-suggested draft to their partner."""
        choice_id = data.split(":")[1]
        choice = self._relay_choices.pop(choice_id, None)
        if not choice:
            return [BotResponse(t(lang, "error_generic"))]

        user = await db_mod.get_user(self.db_path, user_id)
        if not user or not user.get("partner_id"):
            return [BotResponse(t(lang, "not_paired"))]

        draft = choice["relay_draft"]
        pending_id = await db_mod.create_pending(
            self.db_path, user_id, user["partner_id"], draft
        )
        buttons = self._approval_buttons(pending_id, lang)
        return [BotResponse(
            t(lang, "tell_draft_header") + draft,
            buttons=buttons,
        )]

    async def _cb_choose_save(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """User chose to save privately instead of relaying."""
        choice_id = data.split(":")[1]
        self._relay_choices.pop(choice_id, None)
        return [BotResponse(t(lang, "save_done"))]

    # ── edit flow ────────────────────────────────────────────────────────

    async def _handle_edit_input(
        self, user_id: int, text: str
    ) -> list[BotResponse]:
        """Process the user's edited draft text."""
        lang = await self._get_lang(user_id)
        pending_id = self._edit_state.pop(user_id)

        await db_mod.update_pending_edited_draft(
            self.db_path, pending_id, text
        )

        buttons = self._approval_buttons(pending_id, lang)
        return [BotResponse(
            t(lang, "edit_updated", draft=text),
            buttons=buttons,
        )]

    # ── batch review callbacks ───────────────────────────────────────────

    async def _cb_batch_send_all(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Send all items in a batch as one merged message."""
        batch_id = int(data.split(":")[1])
        batch = await db_mod.get_batch(self.db_path, batch_id)
        if not batch or batch["sender_id"] != user_id:
            return [BotResponse(t(lang, "error_generic"))]

        items = await db_mod.get_batch_items(self.db_path, batch_id)
        drafts: list[str] = []
        for item in items:
            if item["status"] != "discarded":
                drafts.append(item.get("edited_draft") or item["draft"])
                if item["status"] == "pending":
                    await db_mod.update_batch_item_status(
                        self.db_path, item["id"], "approved"
                    )

        if not drafts:
            await db_mod.update_batch_status(self.db_path, batch_id, "discarded")
            return [BotResponse(t(lang, "batch_discarded"))]

        merged = "\n\n".join(drafts)
        receiver_id = batch["receiver_id"]

        # Check pause status
        receiver = await db_mod.get_user(self.db_path, receiver_id)
        sender = await db_mod.get_user(self.db_path, user_id)
        if (receiver and receiver.get("paused")) or (
            sender and sender.get("paused")
        ):
            await db_mod.update_batch_status(self.db_path, batch_id, "approved")
            return [BotResponse(
                t(lang, "batch_sent") + "\n\n" + t(lang, "delivery_paused_notice")
            )]

        rlang = (receiver or {}).get("lang") or "en"
        await db_mod.update_batch_status(self.db_path, batch_id, "sent")

        return [
            BotResponse(t(lang, "batch_sent")),
            BotResponse(
                t(rlang, "relay_sent_to_partner", message=merged),
                target_user_id=receiver_id,
            ),
        ]

    async def _cb_batch_review_each(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Start reviewing batch items one at a time."""
        batch_id = int(data.split(":")[1])
        batch = await db_mod.get_batch(self.db_path, batch_id)
        if not batch or batch["sender_id"] != user_id:
            return [BotResponse(t(lang, "error_generic"))]

        return await self._show_next_batch_item(user_id, batch_id, lang)

    async def _cb_batch_discard_all(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Discard all items in a batch."""
        batch_id = int(data.split(":")[1])
        batch = await db_mod.get_batch(self.db_path, batch_id)
        if not batch or batch["sender_id"] != user_id:
            return [BotResponse(t(lang, "error_generic"))]

        items = await db_mod.get_batch_items(self.db_path, batch_id)
        for item in items:
            if item["status"] == "pending":
                await db_mod.update_batch_item_status(
                    self.db_path, item["id"], "discarded"
                )
        await db_mod.update_batch_status(self.db_path, batch_id, "discarded")
        return [BotResponse(t(lang, "batch_discarded"))]

    async def _cb_batch_item_approve(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Approve a single batch item and show the next one."""
        item_id = int(data.split(":")[1])
        item = await db_mod.get_batch_item(self.db_path, item_id)
        if not item:
            return [BotResponse(t(lang, "error_generic"))]

        batch = await db_mod.get_batch(self.db_path, item["batch_id"])
        if not batch or batch["sender_id"] != user_id:
            return [BotResponse(t(lang, "error_generic"))]

        await db_mod.update_batch_item_status(self.db_path, item_id, "approved")
        return await self._show_next_batch_item(user_id, item["batch_id"], lang)

    async def _cb_batch_item_edit(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Enter edit mode for a single batch item."""
        item_id = int(data.split(":")[1])
        item = await db_mod.get_batch_item(self.db_path, item_id)
        if not item:
            return [BotResponse(t(lang, "error_generic"))]

        batch = await db_mod.get_batch(self.db_path, item["batch_id"])
        if not batch or batch["sender_id"] != user_id:
            return [BotResponse(t(lang, "error_generic"))]

        self._batch_item_edit_state[user_id] = item_id
        return [BotResponse(t(lang, "edit_prompt"))]

    async def _cb_batch_item_discard(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Discard a single batch item and show the next one."""
        item_id = int(data.split(":")[1])
        item = await db_mod.get_batch_item(self.db_path, item_id)
        if not item:
            return [BotResponse(t(lang, "error_generic"))]

        batch = await db_mod.get_batch(self.db_path, item["batch_id"])
        if not batch or batch["sender_id"] != user_id:
            return [BotResponse(t(lang, "error_generic"))]

        await db_mod.update_batch_item_status(self.db_path, item_id, "discarded")
        return await self._show_next_batch_item(user_id, item["batch_id"], lang)

    # ── batch review helpers ─────────────────────────────────────────────

    async def _show_next_batch_item(
        self, user_id: int, batch_id: int, lang: str
    ) -> list[BotResponse]:
        """Show the next pending item in the batch, or finalize."""
        items = await db_mod.get_batch_items(self.db_path, batch_id)
        pending = [i for i in items if i["status"] == "pending"]

        if not pending:
            return await self._finalize_batch_review(user_id, batch_id, items, lang)

        item = pending[0]
        total = len(items)
        reviewed = total - len(pending)
        draft = item.get("edited_draft") or item["draft"]

        buttons = [[
            InlineButton(t(lang, "btn_item_approve"), f"bia:{item['id']}"),
            InlineButton(t(lang, "btn_item_edit"), f"bie:{item['id']}"),
            InlineButton(t(lang, "btn_item_discard"), f"bix:{item['id']}"),
        ]]

        return [BotResponse(
            t(lang, "batch_item_review", index=reviewed + 1, total=total, draft=draft),
            buttons=buttons,
        )]

    async def _finalize_batch_review(
        self,
        user_id: int,
        batch_id: int,
        items: list[dict[str, Any]],
        lang: str,
    ) -> list[BotResponse]:
        """Deliver approved batch items as a merged message after review."""
        approved = [i for i in items if i["status"] in ("approved", "edited")]

        if not approved:
            await db_mod.update_batch_status(self.db_path, batch_id, "discarded")
            return [BotResponse(t(lang, "batch_discarded"))]

        batch = await db_mod.get_batch(self.db_path, batch_id)
        if not batch:
            return [BotResponse(t(lang, "error_generic"))]

        receiver_id = batch["receiver_id"]
        drafts = [(i.get("edited_draft") or i["draft"]) for i in approved]
        merged = "\n\n".join(drafts)

        # Check pause
        receiver = await db_mod.get_user(self.db_path, receiver_id)
        sender = await db_mod.get_user(self.db_path, user_id)
        if (receiver and receiver.get("paused")) or (
            sender and sender.get("paused")
        ):
            await db_mod.update_batch_status(self.db_path, batch_id, "approved")
            return [BotResponse(
                t(lang, "batch_sent") + "\n\n" + t(lang, "delivery_paused_notice")
            )]

        rlang = (receiver or {}).get("lang") or "en"
        await db_mod.update_batch_status(self.db_path, batch_id, "sent")

        return [
            BotResponse(t(lang, "batch_sent")),
            BotResponse(
                t(rlang, "relay_sent_to_partner", message=merged),
                target_user_id=receiver_id,
            ),
        ]

    async def _handle_batch_item_edit_input(
        self, user_id: int, text: str
    ) -> list[BotResponse]:
        """Process the user's edited text for a batch item."""
        lang = await self._get_lang(user_id)
        item_id = self._batch_item_edit_state.pop(user_id)

        await db_mod.update_batch_item_status(
            self.db_path, item_id, "pending", text
        )

        buttons = [[
            InlineButton(t(lang, "btn_item_approve"), f"bia:{item_id}"),
            InlineButton(t(lang, "btn_item_edit"), f"bie:{item_id}"),
            InlineButton(t(lang, "btn_item_discard"), f"bix:{item_id}"),
        ]]

        return [BotResponse(
            t(lang, "edit_updated", draft=text),
            buttons=buttons,
        )]

    # ── style / voice callbacks ──────────────────────────────────────────

    async def _cb_style_voice(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Update the user's default voice."""
        voice = data.split(":")[1]
        await db_mod.update_user_profile(self.db_path, user_id, default_voice=voice)
        display = _VOICE_LABELS.get(voice, voice)
        return [BotResponse(t(lang, "style_voice_set", voice=display))]

    async def _cb_style_intensity(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Update the user's voice intensity."""
        try:
            intensity = float(data.split(":")[1])
        except (ValueError, IndexError):
            return [BotResponse(t(lang, "error_generic"))]
        await db_mod.update_user_profile(
            self.db_path, user_id, voice_intensity=intensity,
        )
        return [BotResponse(t(lang, "style_intensity_set", intensity=intensity))]
