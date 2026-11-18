"""Callback-query handler methods (inline button presses).

These methods are mixed into :class:`~pairtner.handlers.service.BotService`
via the :class:`CallbacksMixin` base class.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from pairtner.storage.helpers import _now_iso, _to_iso

from pairtner import db as db_mod
from pairtner.i18n import t

from .models import BotResponse, InlineButton


# Voice name → display label mapping (shared with commands.py)
_VOICE_LABELS: dict[str, str] = {
    "casual_en": "Casual English",
    "telugu": "Telugu",
    "hindi": "Hindi",
    "formal_en": "Formal English",
}

# Profile field name → i18n question key mapping
_PROFILE_FIELDS: dict[str, str] = {
    "relationship_type": "onboard_q_relationship_type",
    "duration_years": "onboard_q_duration",
    "has_kids": "onboard_q_kids",
    "age_range": "onboard_q_age_range",
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
            send_after = _now_iso()
            await db_mod.update_pending_status(
                self.db_path, pending_id, "approved", send_after
            )
            msg = t(lang, "approved_now")
        else:
            send_after = _to_iso(
                datetime.now(timezone.utc)
                + timedelta(minutes=self.config.cooldown_minutes)
            )
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

    # ── fact contradiction callbacks ─────────────────────────────────────

    async def _cb_fact_keep_new(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Keep the new fact, archive the old one."""
        parts = data.split(":")
        new_id, old_id = int(parts[1]), int(parts[2])
        await db_mod.supersede_fact(self.db_path, old_id, new_id)
        return [BotResponse(t(lang, "fact_contradiction_resolved"))]

    async def _cb_fact_keep_both(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Keep both facts — no action needed."""
        return [BotResponse(t(lang, "fact_contradiction_resolved"))]

    async def _cb_fact_keep_old(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Keep the old fact, archive the new one."""
        parts = data.split(":")
        new_id, old_id = int(parts[1]), int(parts[2])
        await db_mod.archive_fact(self.db_path, new_id)
        return [BotResponse(t(lang, "fact_contradiction_resolved"))]

    # ── fact visibility toggle ───────────────────────────────────────────

    async def _cb_fact_toggle_visibility(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Flip a fact between private and shared; only its owner may do so."""
        try:
            fact_id = int(data.split(":")[1])
        except (ValueError, IndexError):
            return [BotResponse(t(lang, "error_generic"))]
        facts = await db_mod.get_active_facts(self.db_path, user_id)
        fact = next((f for f in facts if f["id"] == fact_id), None)
        if fact is None:
            return [BotResponse(t(lang, "error_generic"))]
        new_vis = "private" if fact["visibility"] == "shared" else "shared"
        if not await db_mod.set_fact_visibility(
            self.db_path, fact_id, user_id, new_vis
        ):
            return [BotResponse(t(lang, "error_generic"))]
        key = "fact_now_shared" if new_vis == "shared" else "fact_now_private"
        return [BotResponse(t(lang, key, id=fact_id))]

    # ── memory core proposal callbacks ───────────────────────────────────

    async def _cb_make_core_confirm(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Handle mc:<id>:y|n — confirm or dismiss a core memory proposal."""
        parts = data.split(":")
        if len(parts) < 3:
            return [BotResponse(t(lang, "error_generic"))]
        try:
            fact_id = int(parts[1])
        except (ValueError, IndexError):
            return [BotResponse(t(lang, "error_generic"))]
        choice = parts[2]

        if choice == "y":
            if await db_mod.promote_to_core(self.db_path, fact_id):
                return [BotResponse(t(lang, "core_saved"))]
            return [BotResponse(t(lang, "error_generic"))]
        else:
            # Dismissed — keep as working memory, no action needed
            return [BotResponse(t(lang, "core_dismissed"))]

    # ── goal callbacks ───────────────────────────────────────────────────

    async def _cb_archive_memory(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Handle ar:<id> — archive a memory from /review."""
        try:
            fact_id = int(data.split(":")[1])
        except (ValueError, IndexError):
            return [BotResponse(t(lang, "error_generic"))]

        # Verify ownership
        fact = await db_mod.get_fact_by_id(self.db_path, fact_id)
        if not fact or fact["owner_id"] != user_id:
            return [BotResponse(t(lang, "forget_not_found"))]

        await db_mod.archive_fact(self.db_path, fact_id)
        return [BotResponse(f"📦 Archived memory #{fact_id}.")]

    async def _cb_goal_achieved(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Mark a personal goal as achieved."""
        goal_id = int(data.split(":")[1])
        goal = await db_mod.get_goal_by_id(self.db_path, goal_id)
        if not goal or goal["user_id"] != user_id:
            return [BotResponse(t(lang, "error_generic"))]
        await db_mod.update_goal_status(self.db_path, goal_id, "achieved")

        # Record 'milestone' event on the timeline (FEAT-004)
        user = await db_mod.get_user(self.db_path, user_id)
        if user and user.get("partner_id"):
            couple_id = db_mod.make_couple_id(user_id, user["partner_id"])
            await db_mod.add_timeline_event(
                self.db_path, couple_id, "milestone",
                f"Goal achieved: {goal['text']}",
            )

        return [BotResponse(t(lang, "goal_achieved"))]

    async def _cb_goal_dropped(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Mark a personal goal as dropped."""
        goal_id = int(data.split(":")[1])
        goal = await db_mod.get_goal_by_id(self.db_path, goal_id)
        if not goal or goal["user_id"] != user_id:
            return [BotResponse(t(lang, "error_generic"))]
        await db_mod.update_goal_status(self.db_path, goal_id, "dropped")
        return [BotResponse(t(lang, "goal_dropped"))]

    async def _cb_shared_goal_achieved(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Mark a shared goal as achieved (with ownership check)."""
        goal_id = int(data.split(":")[1])
        goal = await db_mod.get_shared_goal_by_id(self.db_path, goal_id)
        if not goal:
            return [BotResponse(t(lang, "error_generic"))]
        user = await db_mod.get_user(self.db_path, user_id)
        if not user or not user.get("partner_id"):
            return [BotResponse(t(lang, "error_generic"))]
        couple_id = db_mod.make_couple_id(user_id, user["partner_id"])
        if goal["couple_id"] != couple_id:
            return [BotResponse(t(lang, "error_generic"))]
        await db_mod.update_shared_goal_status(self.db_path, goal_id, "achieved")

        # Record 'milestone' event on the timeline (FEAT-004)
        await db_mod.add_timeline_event(
            self.db_path, couple_id, "milestone",
            f"Shared goal achieved: {goal['text']}",
        )

        return [BotResponse(t(lang, "goal_achieved"))]

    async def _cb_shared_goal_dropped(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Mark a shared goal as dropped (with ownership check)."""
        goal_id = int(data.split(":")[1])
        goal = await db_mod.get_shared_goal_by_id(self.db_path, goal_id)
        if not goal:
            return [BotResponse(t(lang, "error_generic"))]
        user = await db_mod.get_user(self.db_path, user_id)
        if not user or not user.get("partner_id"):
            return [BotResponse(t(lang, "error_generic"))]
        couple_id = db_mod.make_couple_id(user_id, user["partner_id"])
        if goal["couple_id"] != couple_id:
            return [BotResponse(t(lang, "error_generic"))]
        await db_mod.update_shared_goal_status(self.db_path, goal_id, "dropped")
        return [BotResponse(t(lang, "goal_dropped"))]

    # ── settings callbacks ───────────────────────────────────────────────

    async def _cb_settings_voice(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Show the voice picker (reuse /style)."""
        return await self.handle_style(user_id, None)

    async def _cb_settings_dampening(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Show dampening level with +/- buttons."""
        profile = await db_mod.ensure_user_profile(self.db_path, user_id)
        level = profile.get("dampening_base") or 0.7
        buttons = [[
            InlineButton("➖", "sd_down"),
            InlineButton(f"{level:.1f}", "sd_noop"),
            InlineButton("➕", "sd_up"),
        ]]
        return [BotResponse(
            t(lang, "settings_dampening_current", level=level),
            buttons=buttons,
        )]

    async def _cb_settings_goals(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Show the goals view."""
        return await self.handle_goal(user_id, None)

    async def _cb_settings_profile(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Show current profile with edit buttons per field."""
        profile = await db_mod.ensure_user_profile(self.db_path, user_id)
        rtype = profile.get("relationship_type") or "—"
        dur = profile.get("duration_years") or "—"
        kids = "yes" if profile.get("has_kids") else ("no" if profile.get("has_kids") == 0 else "—")
        age = profile.get("age_range") or "—"

        buttons = [
            [InlineButton(f"Type: {rtype}", "sp:relationship_type")],
            [InlineButton(f"Duration: {dur}", "sp:duration_years")],
            [InlineButton(f"Kids: {kids}", "sp:has_kids")],
            [InlineButton(f"Age: {age}", "sp:age_range")],
        ]
        return [BotResponse(
            t(lang, "settings_profile_current",
              relationship_type=rtype, duration_years=dur,
              has_kids=kids, age_range=age),
            buttons=buttons,
        )]

    async def _cb_settings_mood(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Show mood presets with set/clear buttons."""
        active = await db_mod.get_active_mood(self.db_path, user_id)
        current = active["mood"] if active else "none"

        mood_buttons = [
            InlineButton("😰 Stressed", "sm:stressed"),
            InlineButton("😊 Happy", "sm:happy"),
        ]
        mood_buttons2 = [
            InlineButton("😤 Angry", "sm:angry"),
            InlineButton("🤔 Reflective", "sm:reflective"),
        ]
        clear_row = [InlineButton("🔄 Clear", "sm_clear")]

        text = t(lang, "mood_current", mood=current, expires="") if active else t(lang, "mood_none")
        return [BotResponse(text, buttons=[mood_buttons, mood_buttons2, clear_row])]

    async def _cb_dampening_up(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Increase dampening base by 0.1."""
        profile = await db_mod.ensure_user_profile(self.db_path, user_id)
        level = min(1.0, round((profile.get("dampening_base") or 0.7) + 0.1, 1))
        await db_mod.update_user_profile(self.db_path, user_id, dampening_base=level)
        buttons = [[
            InlineButton("➖", "sd_down"),
            InlineButton(f"{level:.1f}", "sd_noop"),
            InlineButton("➕", "sd_up"),
        ]]
        return [BotResponse(
            t(lang, "settings_dampening_updated", level=level),
            buttons=buttons,
        )]

    async def _cb_dampening_down(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Decrease dampening base by 0.1."""
        profile = await db_mod.ensure_user_profile(self.db_path, user_id)
        level = max(0.1, round((profile.get("dampening_base") or 0.7) - 0.1, 1))
        await db_mod.update_user_profile(self.db_path, user_id, dampening_base=level)
        buttons = [[
            InlineButton("➖", "sd_down"),
            InlineButton(f"{level:.1f}", "sd_noop"),
            InlineButton("➕", "sd_up"),
        ]]
        return [BotResponse(
            t(lang, "settings_dampening_updated", level=level),
            buttons=buttons,
        )]

    async def _cb_profile_edit(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Start editing a profile field — send the question, store state."""
        field_name = data.split(":")[1]
        if field_name not in _PROFILE_FIELDS:
            return [BotResponse(t(lang, "error_generic"))]
        self._profile_edit_state[user_id] = field_name
        return [BotResponse(t(lang, _PROFILE_FIELDS[field_name]))]

    async def _handle_profile_edit_input(
        self, user_id: int, text: str
    ) -> list[BotResponse]:
        """Process the user's answer for a profile field edit."""
        lang = await self._get_lang(user_id)
        field_name = self._profile_edit_state.pop(user_id)
        value = text.strip()

        # Type coercion for specific fields
        if field_name == "duration_years":
            try:
                value = int(value)
            except ValueError:
                value = 0
        elif field_name == "has_kids":
            value = 1 if value.lower() in ("yes", "1", "true") else 0

        await db_mod.update_user_profile(self.db_path, user_id, **{field_name: value})
        return [BotResponse(t(lang, "settings_profile_updated", field=field_name, value=value))]

    async def _cb_mood_set(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Set a mood preset from settings menu."""
        mood = data.split(":")[1]
        return await self.handle_style(user_id, f"mood {mood}")

    async def _cb_mood_clear(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Clear all moods for this user (active and expired)."""
        await db_mod.clear_user_mood(self.db_path, user_id)
        return [BotResponse(t(lang, "mood_none"))]

    # ── feedback callbacks (FEAT-003) ────────────────────────────────────

    async def _cb_feedback_yes(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Record positive feedback on a relay."""
        pending_id = int(data.split(":")[1])
        await db_mod.update_pending_feedback(self.db_path, pending_id, "yes")
        return [BotResponse(t(lang, "feedback_thanks"))]

    async def _cb_feedback_not_sure(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Record uncertain feedback on a relay."""
        pending_id = int(data.split(":")[1])
        await db_mod.update_pending_feedback(self.db_path, pending_id, "not_sure")
        return [BotResponse(t(lang, "feedback_thanks"))]

    async def _cb_feedback_no(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Record negative feedback on a relay."""
        pending_id = int(data.split(":")[1])
        await db_mod.update_pending_feedback(self.db_path, pending_id, "no")
        return [BotResponse(t(lang, "feedback_thanks"))]

    # ── A/B rewrite callbacks (FEAT-003) ─────────────────────────────────

    async def _cb_ab_choose_a(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Send the lighter (option A) draft."""
        ab_id = data.split(":")[1]
        return await self._process_ab_choice(user_id, ab_id, "a", lang)

    async def _cb_ab_choose_b(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Send the more transformed (option B) draft."""
        ab_id = data.split(":")[1]
        return await self._process_ab_choice(user_id, ab_id, "b", lang)

    async def _cb_ab_edit(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Edit the A/B draft — create a pending from the original draft and enter edit mode."""
        ab_id = data.split(":")[1]
        ab = self._ab_choices.pop(ab_id, None)
        if not ab or ab["user_id"] != user_id:
            return [BotResponse(t(lang, "error_generic"))]

        draft = ab["draft_b"]  # original (more transformed) version
        partner_id = ab["partner_id"]
        pending_id = await db_mod.create_pending(
            self.db_path, user_id, partner_id, draft,
        )
        self._edit_state[user_id] = pending_id
        return [BotResponse(t(lang, "edit_prompt"))]

    async def _cb_ab_discard(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        """Discard the entire A/B rewrite choice."""
        ab_id = data.split(":")[1]
        ab = self._ab_choices.pop(ab_id, None)
        if not ab or ab["user_id"] != user_id:
            return [BotResponse(t(lang, "error_generic"))]
        return [BotResponse(t(lang, "discarded"))]

    async def _process_ab_choice(
        self, user_id: int, ab_id: str, choice: str, lang: str
    ) -> list[BotResponse]:
        """Create pending relay from A/B choice and track preference."""
        ab = self._ab_choices.pop(ab_id, None)
        if not ab or ab["user_id"] != user_id:
            return [BotResponse(t(lang, "error_generic"))]

        draft = ab["draft_a"] if choice == "a" else ab["draft_b"]
        partner_id = ab["partner_id"]
        skip_cooldown = ab.get("skip_cooldown", False)

        pending_id = await db_mod.create_pending(
            self.db_path, user_id, partner_id, draft,
        )

        # Track preference in couple_config
        couple_id = db_mod.make_couple_id(user_id, partner_id)
        pref_key = f"ab_preference_count_{choice}"
        current = await db_mod.get_couple_config(
            self.db_path, couple_id, pref_key,
        )
        new_count = int(current or 0) + 1
        await db_mod.set_couple_config(
            self.db_path, couple_id, pref_key, str(new_count),
        )

        buttons = self._approval_buttons(pending_id, lang, skip_cooldown)
        return [BotResponse(
            t(lang, "tell_draft_header") + draft,
            buttons=buttons,
        )]
