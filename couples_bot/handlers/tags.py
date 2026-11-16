"""Tag handler methods — ``#keep``, ``#send``, ``#deep``, plain messages.

These methods are mixed into :class:`~couples_bot.handlers.service.BotService`
via the :class:`TagsMixin` base class.
"""

from __future__ import annotations

import uuid

from couples_bot import db as db_mod
from couples_bot.conflict_detector import detect_conflict_opening
from couples_bot.dampening import get_dampening_instruction, resolve_dampening
from couples_bot.i18n import t
from couples_bot.profile_updater import process_llm_extras
from couples_bot.prompts import (
    build_blunt_prompt,
    build_chat_prompt,
    build_goals_section,
    build_keep_prompt,
    build_partner_summary_block,
    build_system_prompt,
    build_tell_prompt,
    load_voice,
)

from .models import BotResponse, InlineButton


class TagsMixin:
    """Mixin providing tag-based message handlers for BotService."""

    async def _get_enriched_style(
        self, user_id: int, has_blunt_tag: bool = False
    ) -> tuple[str | None, str | None]:
        """Load the user's voice file and resolve dampening.

        Returns ``(voice_text, dampening_instruction)``.
        """
        profile = await db_mod.get_user_profile(self.db_path, user_id)
        voice_name = (profile or {}).get("default_voice") or self.config.default_voice
        voice_text = load_voice(self.config.voices_dir, voice_name)

        _, dampening_instruction = await resolve_dampening(
            self.db_path, user_id, self.config, has_blunt_tag=has_blunt_tag,
        )
        return voice_text, dampening_instruction

    async def _get_partner_summary(self, user_id: int) -> str | None:
        """Build the partner perception summary block for prompts."""
        user = await db_mod.get_user(self.db_path, user_id)
        if not user or not user.get("partner_id"):
            return None
        perceptions = await db_mod.get_partner_perceptions(
            self.db_path, user_id,
        )
        if not perceptions:
            return None
        return build_partner_summary_block(perceptions)

    async def _get_goals_text(self, user_id: int) -> str | None:
        """Build the goals section for system prompts."""
        personal = await db_mod.get_active_goals(self.db_path, user_id)
        personal_texts = [g["text"] for g in personal]

        shared_texts: list[str] = []
        user = await db_mod.get_user(self.db_path, user_id)
        if user and user.get("partner_id"):
            couple_id = db_mod.make_couple_id(user_id, user["partner_id"])
            shared = await db_mod.get_shared_goals(self.db_path, couple_id)
            shared_texts = [g["text"] for g in shared]

        section = build_goals_section(personal_texts, shared_texts)
        return section or None

    async def _handle_keep(
        self, user_id: int, text: str
    ) -> list[BotResponse]:
        """Process a ``#keep`` message — extract and save facts only."""
        lang = await self._get_lang(user_id)
        ctx = await self._build_ctx(user_id)
        voice_text, damp_instr = await self._get_enriched_style(user_id)
        system, content = build_keep_prompt(ctx, text, voice_text=voice_text, dampening_instruction=damp_instr)

        api_key = await self._get_couple_api_key(user_id)
        await db_mod.log_message(self.db_path, user_id, "user", text)
        result = await self.llm.generate(
            content, system, api_key_override=api_key,
        )
        await db_mod.log_message(self.db_path, user_id, "assistant", result["reply"])

        count = await self._save_facts(user_id, result.get("new_facts", []))

        # Fire-and-forget: auto-update profiles from LLM extras
        await process_llm_extras(self.db_path, user_id, result, self.config)

        reply = result["reply"]
        if count:
            reply += "\n\n" + t(lang, "fact_saved", count=count)
        responses = [BotResponse(reply)]
        responses.extend(self._drain_contradictions())
        return responses

    async def _handle_send(
        self, user_id: int, text: str, skip_cooldown: bool
    ) -> list[BotResponse]:
        """Process a ``#send`` message — draft a relay for the partner."""
        lang = await self._get_lang(user_id)
        user = await db_mod.get_user(self.db_path, user_id)
        if not user or not user.get("partner_id"):
            return [BotResponse(t(lang, "not_paired"))]

        ctx = await self._build_ctx(user_id)
        voice_text, damp_instr = await self._get_enriched_style(user_id)
        partner_summary = await self._get_partner_summary(user_id)
        goals_text = await self._get_goals_text(user_id)
        system, content = build_tell_prompt(
            ctx, text, voice_text=voice_text,
            dampening_instruction=damp_instr,
            partner_summary=partner_summary,
            goals_text=goals_text,
        )

        api_key = await self._get_couple_api_key(user_id)
        await db_mod.log_message(self.db_path, user_id, "user", text)
        result = await self.llm.generate(
            content, system, api_key_override=api_key,
        )
        await db_mod.log_message(self.db_path, user_id, "assistant", result["reply"])

        await self._save_facts(user_id, result.get("new_facts", []))

        # Fire-and-forget: auto-update profiles from LLM extras
        await process_llm_extras(self.db_path, user_id, result, self.config)

        # Conflict detection (FEAT-002)
        if self.config.conflicts_enabled and self.config.conflicts_auto_detect:
            couple_id = db_mod.make_couple_id(user_id, user["partner_id"])
            is_conflict, theme = await detect_conflict_opening(
                self.db_path, couple_id, user_id, result,
            )
            if is_conflict and theme:
                await db_mod.create_conflict(self.db_path, couple_id, theme)

        # Soft read receipts: mark relays sent TO this user as acknowledged
        await self._acknowledge_relays(user_id)

        draft = result.get("relay_draft") or result["reply"]

        # A/B rewrite: if dampening is in mid-range, offer two variants
        damp_level, _ = await resolve_dampening(
            self.db_path, user_id, self.config,
        )
        lo, hi = self.config.ab_rewrite_dampening_range
        if (
            self.config.ab_rewrite_enabled
            and lo <= damp_level <= hi
        ):
            lighter_level = max(0.0, damp_level - 0.15)
            lighter_instr = get_dampening_instruction(lighter_level)
            system_b, content_b = build_tell_prompt(
                ctx, text, voice_text=voice_text,
                dampening_instruction=lighter_instr,
                partner_summary=partner_summary,
                goals_text=goals_text,
            )
            result_b = await self.llm.generate(
                content_b, system_b, api_key_override=api_key,
            )
            draft_a = result_b.get("relay_draft") or result_b["reply"]
            draft_b = draft  # original (more transformed)

            ab_id = uuid.uuid4().hex[:8]
            self._ab_choices[ab_id] = {
                "user_id": user_id,
                "partner_id": user["partner_id"],
                "draft_a": draft_a,
                "draft_b": draft_b,
                "skip_cooldown": skip_cooldown,
            }

            text_msg = (
                t(lang, "ab_option_header")
                + t(lang, "ab_option_a", draft=draft_a)
                + t(lang, "ab_option_b", draft=draft_b)
            )

            # Articulation tip (if enabled and present)
            art_note = result.get("articulation_note")
            if art_note and self.config.articulation_enabled:
                text_msg += "\n\n" + t(lang, "articulation_tip", tip=art_note)

            buttons = [[
                InlineButton(t(lang, "btn_send_a"), f"ab_a:{ab_id}"),
                InlineButton(t(lang, "btn_send_b"), f"ab_b:{ab_id}"),
                InlineButton(t(lang, "btn_edit"), f"ed:__ab_placeholder__"),
                InlineButton(t(lang, "btn_discard"), f"di:__ab_placeholder__"),
            ]]
            responses = [BotResponse(text_msg, buttons=buttons)]
            responses.extend(self._drain_contradictions())
            return responses

        pending_id = await db_mod.create_pending(
            self.db_path, user_id, user["partner_id"], draft
        )

        text_msg = t(lang, "tell_draft_header") + draft

        # Articulation tip (if enabled and present)
        art_note = result.get("articulation_note")
        if art_note and self.config.articulation_enabled:
            text_msg += "\n\n" + t(lang, "articulation_tip", tip=art_note)

        # Inform sender if partner is paused
        partner = await db_mod.get_user(self.db_path, user["partner_id"])
        if partner and partner.get("paused"):
            text_msg += "\n\n" + t(lang, "partner_paused_notice")

        buttons = self._approval_buttons(pending_id, lang, skip_cooldown)
        responses = [BotResponse(text_msg, buttons=buttons)]
        responses.extend(self._drain_contradictions())
        return responses

    async def _handle_deep(
        self, user_id: int, text: str
    ) -> list[BotResponse]:
        """Process a ``#deep`` message — chat using the heavy model."""
        lang = await self._get_lang(user_id)
        ctx = await self._build_ctx(user_id)
        voice_text, damp_instr = await self._get_enriched_style(user_id)
        partner_summary = await self._get_partner_summary(user_id)
        goals_text = await self._get_goals_text(user_id)
        system, content = build_chat_prompt(
            ctx, text, voice_text=voice_text,
            dampening_instruction=damp_instr,
            partner_summary=partner_summary,
            goals_text=goals_text,
        )

        model = self.config.model_heavy[0] if self.config.model_heavy else None
        api_key = await self._get_couple_api_key(user_id)
        await db_mod.log_message(self.db_path, user_id, "user", text)
        result = await self.llm.generate(
            content, system, model=model, api_key_override=api_key,
        )
        await db_mod.log_message(self.db_path, user_id, "assistant", result["reply"])

        if model and hasattr(self.llm, "check_usage_warning"):
            warning = await self.llm.check_usage_warning(model)
            if warning:
                result["reply"] += "\n\n" + warning

        count = await self._save_facts(user_id, result.get("new_facts", []))

        # Fire-and-forget: auto-update profiles from LLM extras
        await process_llm_extras(self.db_path, user_id, result, self.config)

        replies: list[BotResponse] = [BotResponse(result["reply"])]
        if count:
            replies[0].text += "\n\n" + t(lang, "fact_saved", count=count)
        replies.extend(self._drain_contradictions())
        return replies

    async def _handle_blunt(
        self, user_id: int, text: str
    ) -> list[BotResponse]:
        """Process a ``#blunt`` / ``#honest`` message — direct, unfiltered feedback."""
        lang = await self._get_lang(user_id)
        ctx = await self._build_ctx(user_id)
        voice_text, damp_instr = await self._get_enriched_style(user_id, has_blunt_tag=True)
        system, content = build_blunt_prompt(ctx, text, voice_text=voice_text, dampening_instruction=damp_instr)

        api_key = await self._get_couple_api_key(user_id)
        await db_mod.log_message(self.db_path, user_id, "user", text)
        result = await self.llm.generate(
            content, system, api_key_override=api_key,
        )
        await db_mod.log_message(self.db_path, user_id, "assistant", result["reply"])

        count = await self._save_facts(user_id, result.get("new_facts", []))

        # Fire-and-forget: auto-update profiles from LLM extras
        await process_llm_extras(self.db_path, user_id, result, self.config)

        reply_text = t(lang, "blunt_disclaimer") + "\n\n" + result["reply"]
        if count:
            reply_text += "\n\n" + t(lang, "fact_saved", count=count)
        responses = [BotResponse(reply_text)]
        responses.extend(self._drain_contradictions())
        return responses

    async def _handle_plain(
        self, user_id: int, text: str
    ) -> list[BotResponse]:
        """Process a plain message (no tag) — chat, then optionally offer relay."""
        lang = await self._get_lang(user_id)
        ctx = await self._build_ctx(user_id)
        voice_text, damp_instr = await self._get_enriched_style(user_id)
        partner_summary = await self._get_partner_summary(user_id)
        goals_text = await self._get_goals_text(user_id)
        system, content = build_chat_prompt(
            ctx, text, voice_text=voice_text,
            dampening_instruction=damp_instr,
            partner_summary=partner_summary,
            goals_text=goals_text,
        )

        api_key = await self._get_couple_api_key(user_id)
        await db_mod.log_message(self.db_path, user_id, "user", text)
        result = await self.llm.generate(
            content, system, api_key_override=api_key,
        )
        await db_mod.log_message(self.db_path, user_id, "assistant", result["reply"])

        count = await self._save_facts(user_id, result.get("new_facts", []))

        # Fire-and-forget: auto-update profiles from LLM extras
        await process_llm_extras(self.db_path, user_id, result, self.config)

        # Conflict detection (FEAT-002)
        if self.config.conflicts_enabled and self.config.conflicts_auto_detect:
            _usr = await db_mod.get_user(self.db_path, user_id)
            if _usr and _usr.get("partner_id"):
                couple_id = db_mod.make_couple_id(user_id, _usr["partner_id"])
                is_conflict, theme = await detect_conflict_opening(
                    self.db_path, couple_id, user_id, result,
                )
                if is_conflict and theme:
                    await db_mod.create_conflict(self.db_path, couple_id, theme)

        # Soft read receipts: mark relays sent TO this user as acknowledged
        await self._acknowledge_relays(user_id)

        reply_text = result["reply"]
        if count:
            reply_text += "\n\n" + t(lang, "fact_saved", count=count)

        # Articulation tip (if enabled and relay_draft present)
        art_note = result.get("articulation_note")
        if art_note and result.get("relay_draft") and self.config.articulation_enabled:
            reply_text += "\n\n" + t(lang, "articulation_tip", tip=art_note)

        # If user is paired and LLM suggested a relay, offer save/relay choice
        user = await db_mod.get_user(self.db_path, user_id)
        if user and user.get("partner_id") and result.get("relay_draft"):
            choice_id = uuid.uuid4().hex[:8]
            self._relay_choices[choice_id] = {
                "user_id": user_id,
                "relay_draft": result["relay_draft"],
            }
            buttons = self._choice_buttons(choice_id, lang)
            responses = [BotResponse(
                reply_text + "\n\n" + t(lang, "ask_action"),
                buttons=buttons,
            )]
            responses.extend(self._drain_contradictions())

            # Feedback check: after relay delivery, ask sender for feedback
            feedback_responses = await self._check_feedback(user_id, lang)
            responses.extend(feedback_responses)

            return responses

        responses = [BotResponse(reply_text)]
        responses.extend(self._drain_contradictions())

        # Feedback check: after relay delivery, ask sender for feedback
        feedback_responses = await self._check_feedback(user_id, lang)
        responses.extend(feedback_responses)

        return responses

    async def _acknowledge_relays(self, user_id: int) -> None:
        """Mark any unacknowledged relays sent TO this user as acknowledged."""
        unacked = await db_mod.get_unacknowledged_relays_to_user(
            self.db_path, user_id,
        )
        for relay in unacked:
            await db_mod.mark_pending_acknowledged(self.db_path, relay["id"])

    async def _check_feedback(
        self, user_id: int, lang: str
    ) -> list[BotResponse]:
        """Check if feedback should be requested for recently delivered relays."""
        if not self.config.feedback_enabled:
            return []
        from couples_bot.feedback import (
            check_for_feedback_opportunity,
            generate_feedback_request,
        )
        pending = await check_for_feedback_opportunity(self.db_path, user_id)
        if not pending:
            return []
        text, button_rows = generate_feedback_request(pending, lang)
        buttons = [
            [InlineButton(label, data) for label, data in row]
            for row in button_rows
        ]
        return [BotResponse(text, buttons=buttons)]
