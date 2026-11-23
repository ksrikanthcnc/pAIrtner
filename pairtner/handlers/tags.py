"""Tag handler methods — ``#keep``, ``#send``, ``#deep``, plain messages.

These methods are mixed into :class:`~pairtner.handlers.service.BotService`
via the :class:`TagsMixin` base class.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from pairtner import db as db_mod
from pairtner import pack
from pairtner.llm import get_reply_text
from pairtner.features.conflict_detector import detect_conflict_opening
from pairtner.core.dampening import get_dampening_instruction, resolve_dampening
from pairtner.core.memory import _has_v2_columns as _is_v2_db
from pairtner.features.nudge_injector import (
    inject_nudge_context,
    mark_nudges_delivered,
    maybe_inject_camouflage,
)
from pairtner.features.nudge_prefs import apply_nudge_request
from pairtner.config import is_user_allowed
from pairtner.i18n import t
from pairtner.storage.schema_v2 import make_relationship_id
from pairtner.core.profile_updater import process_llm_extras
from pairtner.storage.helpers import _to_iso
from pairtner.storage.facts import get_private_tags
from pairtner.storage.tagfmt import parse_fact_tags
from pairtner.core.prompts import (
    build_blunt_prompt,
    apply_solo_persona,
    build_chat_prompt,
    build_chat_prompt_v2,
    build_goals_section,
    build_keep_prompt,
    build_partner_summary_block,
    build_smart_context,
    build_system_prompt,
    build_tell_prompt,
    load_voice,
)

from .models import BotResponse, InlineButton

logger = logging.getLogger(__name__)

MODEL_PREF_KEY = "llm_model"


class TagsMixin:
    """Mixin providing tag-based message handlers for BotService."""

    async def _get_enriched_style(
        self, user_id: int, has_blunt_tag: bool = False
    ) -> tuple[str | None, str | None]:
        """Load the user's voice file and resolve dampening.

        When voice auto-learning is enabled and the user has sent enough
        messages, the learned voice traits are prepended to the static
        voice text so the LLM adapts to the user's communication style.

        Returns ``(voice_text, dampening_instruction)``.
        """
        import json as _json

        profile = await db_mod.get_user_profile(self.db_path, user_id)
        voice_name = (profile or {}).get("default_voice") or self.config.default_voice
        voice_text = load_voice(voice_name)

        # Check for mature learned voice profile
        if (
            getattr(self.config, "voice_auto_learn", True)
            and profile
            and profile.get("learned_voice")
            and (profile.get("message_count") or 0) >= getattr(
                self.config, "voice_learn_after_messages", 10
            )
        ):
            try:
                traits = _json.loads(profile["learned_voice"])
                if isinstance(traits, dict) and traits:
                    parts = ", ".join(
                        f"{k}={v}" for k, v in traits.items()
                    )
                    learned_instruction = pack.current().s(
                        "learned_voice", parts=parts,
                    )
                    voice_text = learned_instruction + "\n\n" + voice_text
            except (_json.JSONDecodeError, TypeError):
                pass

        _, dampening_instruction = await resolve_dampening(
            self.db_path, user_id, self.config, has_blunt_tag=has_blunt_tag,
        )
        return voice_text, dampening_instruction

    async def _get_existing_tags(self, user_id: int) -> list[str]:
        """Tags already used by this user and their partner."""
        user = await db_mod.get_user(self.db_path, user_id)
        partner_id = user.get("partner_id") if user else None
        raw = await db_mod.get_couple_tags(self.db_path, user_id, partner_id)
        # Rows hold JSON arrays (new) or plain strings (legacy): flatten, dedupe.
        return sorted({tg for r in raw for tg in parse_fact_tags(r)})

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
        ctx = await self._build_ctx(user_id, current_message=text)
        voice_text, damp_instr = await self._get_enriched_style(user_id)
        smart_ctx = await build_smart_context(
            self.db_path, user_id, text, self.config,
        )
        system, content = build_keep_prompt(
            ctx, text, voice_text=voice_text,
            dampening_instruction=damp_instr, smart_ctx=smart_ctx,
        )

        api_key = await self._get_couple_api_key(user_id)
        await db_mod.log_message(self.db_path, user_id, "user", text)
        result = await self.llm.generate(
            content, system, api_key_override=api_key,
        )
        await db_mod.log_message(self.db_path, user_id, "assistant", result["reply"])

        count = await self._apply_memory_ops_from_result(user_id, result, text)

        # Fire-and-forget: auto-update profiles from LLM extras
        await process_llm_extras(self.db_path, user_id, result, self.config)

        reply = result["reply"]
        # Don't append fact-saved notices to regular chat — it breaks conversational flow.
        # The LLM's reply IS the conversation. Use /debug to see extractions.
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

        ctx = await self._build_ctx(user_id, current_message=text)
        voice_text, damp_instr = await self._get_enriched_style(user_id)
        partner_summary = await self._get_partner_summary(user_id)
        goals_text = await self._get_goals_text(user_id)
        smart_ctx = await build_smart_context(
            self.db_path, user_id, text, self.config,
        )
        system, content = build_tell_prompt(
            ctx, text, voice_text=voice_text,
            dampening_instruction=damp_instr,
            partner_summary=partner_summary,
            goals_text=goals_text,
            smart_ctx=smart_ctx,
            mode=await db_mod.get_user_preference(self.db_path, user_id, "mode", default="mediator") or "mediator",
        )

        api_key = await self._get_couple_api_key(user_id)
        await db_mod.log_message(self.db_path, user_id, "user", text)
        result = await self.llm.generate(
            content, system, api_key_override=api_key,
        )
        await db_mod.log_message(self.db_path, user_id, "assistant", result["reply"])

        await self._apply_memory_ops_from_result(user_id, result, text, allow_pin=False)

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
                InlineButton(t(lang, "btn_edit"), f"ab_ed:{ab_id}"),
                InlineButton(t(lang, "btn_discard"), f"ab_di:{ab_id}"),
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
        ctx = await self._build_ctx(user_id, current_message=text)
        voice_text, damp_instr = await self._get_enriched_style(user_id)
        partner_summary = await self._get_partner_summary(user_id)
        goals_text = await self._get_goals_text(user_id)
        smart_ctx = await build_smart_context(
            self.db_path, user_id, text, self.config,
        )
        system, content = build_chat_prompt(
            ctx, text, voice_text=voice_text,
            dampening_instruction=damp_instr,
            partner_summary=partner_summary,
            goals_text=goals_text,
            smart_ctx=smart_ctx,
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

        count = await self._apply_memory_ops_from_result(user_id, result, text)

        # Fire-and-forget: auto-update profiles from LLM extras
        await process_llm_extras(self.db_path, user_id, result, self.config)

        replies: list[BotResponse] = [BotResponse(result["reply"])]
        if count:
            replies[0].text += "\n\n" + self._format_fact_saved(lang, count)
        replies.extend(self._drain_contradictions())
        return replies

    async def _handle_blunt(
        self, user_id: int, text: str
    ) -> list[BotResponse]:
        """Process a ``#blunt`` / ``#honest`` message — direct, unfiltered feedback."""
        lang = await self._get_lang(user_id)
        ctx = await self._build_ctx(user_id, current_message=text)
        voice_text, damp_instr = await self._get_enriched_style(user_id, has_blunt_tag=True)
        smart_ctx = await build_smart_context(
            self.db_path, user_id, text, self.config,
        )
        system, content = build_blunt_prompt(
            ctx, text, voice_text=voice_text,
            dampening_instruction=damp_instr, smart_ctx=smart_ctx,
        )

        api_key = await self._get_couple_api_key(user_id)
        await db_mod.log_message(self.db_path, user_id, "user", text)
        result = await self.llm.generate(
            content, system, api_key_override=api_key,
        )
        await db_mod.log_message(self.db_path, user_id, "assistant", result["reply"])

        count = await self._apply_memory_ops_from_result(user_id, result, text)

        # Fire-and-forget: auto-update profiles from LLM extras
        await process_llm_extras(self.db_path, user_id, result, self.config)

        reply_text = t(lang, "blunt_disclaimer") + "\n\n" + result["reply"]
        responses = [BotResponse(reply_text)]
        responses.extend(self._drain_contradictions())
        return responses

    async def _handle_plain(
        self, user_id: int, text: str
    ) -> list[BotResponse]:
        """Plain message: chat normally. Personality preferences are inferred
        from conversation by the LLM (no explicit questionnaire needed).
        Users can set preferences anytime via natural language or /style."""
        responses = await self._handle_plain_core(user_id, text)
        return responses

    async def _handle_plain_core(
        self, user_id: int, text: str
    ) -> list[BotResponse]:
        """Process a plain message (no tag) — chat, then optionally offer relay."""
        lang = await self._get_lang(user_id)
        # "nudge me less" / "no nudges on weekends": apply the setting now; the
        # LLM still extracts the same statement as a core fact below.
        await apply_nudge_request(self.db_path, user_id, text)
        solo = await self._is_solo(user_id)
        # Solo users never use the couple context (partner facts, nudges).
        v2 = None if solo else await self._build_ctx_v2(user_id, text)
        voice_text, damp_instr = await self._get_enriched_style(user_id)
        goals_text = await self._get_goals_text(user_id)
        existing_tags = await self._get_existing_tags(user_id)
        private_tags = await get_private_tags(self.db_path, user_id)
        rel_id: str | None = None
        if v2 is not None:
            composed, rel_id = v2
            # Get partner name for context section headers
            _usr = await db_mod.get_user(self.db_path, user_id)
            _partner_name = ""
            if _usr and _usr.get("partner_id"):
                _partner = await db_mod.get_user(self.db_path, _usr["partner_id"])
                _partner_name = (_partner or {}).get("name", "")
            system, content = build_chat_prompt_v2(
                composed, text, voice_text=voice_text,
                dampening_instruction=damp_instr, goals_text=goals_text,
                existing_tags=existing_tags, private_tags=private_tags,
                partner_name=_partner_name,
            )
        else:
            ctx = await self._build_ctx(user_id, current_message=text)
            partner_summary = None if solo else await self._get_partner_summary(user_id)
            if solo:
                ctx.partner_name = None
            smart_ctx = await build_smart_context(
                self.db_path, user_id, text, self.config,
            )
            system, content = build_chat_prompt(
                ctx, text, voice_text=voice_text,
                dampening_instruction=damp_instr,
                partner_summary=partner_summary,
                goals_text=goals_text,
                smart_ctx=smart_ctx,
                existing_tags=existing_tags, private_tags=private_tags,
            )

        # v2 DB: nudge suggestions (v1-context fallback only; the composed v2
        # context already carries them) + camouflage questions.
        injected_nudges: list[int] = []
        if solo:
            system = apply_solo_persona(system)
        elif await _is_v2_db(self.db_path):
            if rel_id is None:
                _u = await db_mod.get_user(self.db_path, user_id)
                rel_id_n = (
                    make_relationship_id(user_id, _u["partner_id"])
                    if _u and _u.get("partner_id") else None
                )
                system, injected_nudges = await inject_nudge_context(
                    self.db_path, user_id, rel_id_n, system,
                )
                system = await maybe_inject_camouflage(self.db_path, user_id, system, rel_id_n)
            else:
                system = await maybe_inject_camouflage(self.db_path, user_id, system, rel_id)

        api_key = await self._get_couple_api_key(user_id)
        user_model = await self._get_user_model(user_id)
        model_kw = {"model": user_model} if user_model else {}
        await db_mod.log_message(
            self.db_path, user_id, "user", text, relationship_id=rel_id,
        )
        if rel_id is not None:
            result = await self._generate_with_tools(
                user_id, content, system, api_key, rel_id, **model_kw,
            )
        else:
            result = await self.llm.generate(
                content, system, api_key_override=api_key, **model_kw,
            )
        if result.get("_parse_failed"):
            result = await self._retry_json_repair(
                result, content, system, api_key,
            )
        await db_mod.log_message(
            self.db_path, user_id, "assistant",
            result["reply"] if result["reply"].strip() else "💭",
            relationship_id=rel_id,
        )
        if injected_nudges:
            await mark_nudges_delivered(self.db_path, injected_nudges)
        if v2 is not None and rel_id is not None:
            await self._deliver_nudges(user_id, rel_id, v2[0].nudge_suggestions)

        count = await self._apply_memory_ops_from_result(
            user_id, result, text, v2=rel_id is not None, relationship_id=rel_id,
        )

        # Fire-and-forget: auto-update profiles from LLM extras
        extras = {**result, "partner_themes": []} if solo else result
        await process_llm_extras(self.db_path, user_id, extras, self.config)

        # Conflict detection (FEAT-002) -- partner feature, skipped in solo
        if not solo and self.config.conflicts_enabled and self.config.conflicts_auto_detect:
            _usr = await db_mod.get_user(self.db_path, user_id)
            if _usr and _usr.get("partner_id"):
                couple_id = db_mod.make_couple_id(user_id, _usr["partner_id"])
                is_conflict, theme = await detect_conflict_opening(
                    self.db_path, couple_id, user_id, result,
                )
                if is_conflict and theme:
                    await db_mod.create_conflict(self.db_path, couple_id, theme)

        # Soft read receipts: mark relays sent TO this user as acknowledged
        if not solo:
            await self._acknowledge_relays(user_id)

        reply_text = get_reply_text(result, fallback="💭")

        if self._last_settings_confirmation:
            reply_text += "\n\n" + self._last_settings_confirmation
            self._last_settings_confirmation = ""

        # Articulation tip (if enabled and relay_draft present)
        art_note = result.get("articulation_note")
        if art_note and result.get("relay_draft") and self.config.articulation_enabled:
            reply_text += "\n\n" + t(lang, "articulation_tip", tip=art_note)

        # If user is paired and LLM suggested a relay, offer save/relay choice
        user = await db_mod.get_user(self.db_path, user_id)
        if (not solo and user and user.get("partner_id")
                and result.get("relay_draft")):
            # Check if user is in yolo mode (auto-send)
            relay_mode = await db_mod.get_user_preference(
                self.db_path, user_id, "relay_mode", default="review",
            )
            if relay_mode == "yolo":
                # Auto-approve: create pending, approve immediately
                partner_id = user["partner_id"]
                draft = result["relay_draft"]
                pid = await db_mod.create_pending(
                    self.db_path, user_id, partner_id, draft,
                )
                send_after = _to_iso(
                    datetime.now(timezone.utc)
                    + timedelta(minutes=self.config.cooldown_minutes)
                )
                await db_mod.update_pending_status(
                    self.db_path, pid, "approved", send_after,
                )
                responses = [BotResponse(
                    reply_text + "\n\n🚀 " + t(
                        lang, "yolo_auto_sent",
                        minutes=self.config.cooldown_minutes,
                    ),
                )]
                responses.extend(self._drain_contradictions())
                await self._maybe_run_analysis(user_id)
                return responses

            # Simple informational relays skip review and become auto-nudges.
            # Default to "info" when urgency is not set (most relays are informational).
            urgency = result.get("relay_urgency") or "info"
            if urgency == "info":
                partner_id = user["partner_id"]
                await db_mod.add_fact(
                    self.db_path, user_id, result["relay_draft"], "shared",
                    kind="working",
                    msg_source="bot",
                    nudge_type="communication",
                    nudge_suggestion=result["relay_draft"],
                    nudge_target_id=partner_id,
                    nudge_cooldown_h=0,
                    nudge_status="pending",
                    relationship_id=make_relationship_id(user_id, partner_id),
                )
                responses = [BotResponse(
                    reply_text + "\n\n" + t(lang, "relay_auto_nudged"),
                )]
                responses.extend(self._drain_contradictions())
                await self._maybe_run_analysis(user_id)
                return responses

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

            # Background analysis trigger
            await self._maybe_run_analysis(user_id)

            return responses

        responses = [BotResponse(reply_text)]
        responses.extend(self._drain_contradictions())
        if solo:
            return responses  # no relay feedback / partner analysis in solo

        # Feedback check: after relay delivery, ask sender for feedback
        feedback_responses = await self._check_feedback(user_id, lang)
        responses.extend(feedback_responses)

        # Background analysis trigger (fire-and-forget, every N messages)
        await self._maybe_run_analysis(user_id)

        return responses

    MAX_TOOL_ROUNDS = 2

    async def _retry_json_repair(
        self, failed: dict, content: str, system: str, api_key: str | None,
    ) -> dict:
        """Retry ONCE after a JSON parse failure; fall back to the raw result."""
        error = failed.get("_parse_error", "invalid JSON")
        repair = (
            f"{content}\n\nYour previous response was not valid JSON. "
            f"Error: {error}. Return ONLY valid JSON with no markdown fences."
        )
        try:
            retry = await self.llm.generate(
                repair, system, api_key_override=api_key,
            )
        except Exception:  # noqa: BLE001 - any provider failure keeps raw fallback
            logger.warning("JSON repair retry failed", exc_info=True)
            return failed
        if retry.get("_parse_failed"):
            return failed
        return retry

    async def _generate_with_tools(
        self, user_id: int, content: str, system: str,
        api_key: str | None, rel_id: str | None, model: str | None = None,
    ) -> dict:
        """LLM call offering ``search_memory``; execute requested tools and
        re-call (max ``MAX_TOOL_ROUNDS`` rounds). Falls back to a plain call
        for clients whose ``generate`` does not accept ``tools``."""
        import inspect
        import json as _json

        from pairtner.core.llm_tools import (
            TOOL_DEFINITIONS, execute_search_memory, format_tool_results,
        )

        params = inspect.signature(self.llm.generate).parameters
        supports_tools = "tools" in params or any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
        model_kw = {"model": model} if model else {}
        if not supports_tools:
            return await self.llm.generate(
                content, system, api_key_override=api_key, **model_kw,
            )

        rounds = 0
        while True:
            # After the round cap, withhold tools so the LLM must answer.
            offer = TOOL_DEFINITIONS if rounds < self.MAX_TOOL_ROUNDS else None
            kwargs = {"tools": offer} if offer else {}
            kwargs.update(model_kw)
            result = await self.llm.generate(
                content, system, api_key_override=api_key, **kwargs,
            )
            calls = result.get("tool_calls") if offer else None
            if not calls:
                return result
            rounds += 1
            parts: list[str] = []
            for call in calls:
                fn = call.get("function", call)
                if fn.get("name") != "search_memory":
                    continue
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = _json.loads(args)
                    except _json.JSONDecodeError:
                        args = {}
                found = await execute_search_memory(
                    self.db_path, user_id, str(args.get("query", "")), rel_id,
                )
                parts.append(
                    f"[search_memory results for {args.get('query', '')!r}]\n"
                    + format_tool_results(found)
                )
            if not parts:
                return result
            content = content + "\n\n" + "\n\n".join(parts) + (
                "\n\nUse these memory results to answer the user's message."
            )

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
        from pairtner.features.feedback import (
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

    async def _maybe_run_analysis(self, user_id: int) -> None:
        """Trigger background analysis if enough new messages have accumulated."""
        try:
            from pairtner.core.analysis import should_run_analysis, run_analysis
            interval = getattr(self.config, "analysis_interval", 10)
            if await should_run_analysis(self.db_path, user_id, interval=interval):
                api_key = await self._get_couple_api_key(user_id)
                await run_analysis(
                    self.db_path, self.llm, user_id,
                    config=self.config, api_key_override=api_key,
                )
        except Exception:
            # Analysis is non-critical -- log and continue
            import logging
            logging.getLogger(__name__).exception(
                "Background analysis failed for user %s", user_id,
            )

    # ── /dryrun and /debug ──────────────────────────────────────────────

    @staticmethod
    def _parse_message_flags(text: str) -> tuple[set[str], str | None, str]:
        """Parse leading /flags and /model from a message.

        Returns (flags, model_override, clean_message).
        Supports: /dryrun, /debug, /model <name>
        Example: '/debug /model gemma3:4b how are we doing?'
          -> ({'debug'}, 'gemma3:4b', 'how are we doing?')
        """
        flags: set[str] = set()
        model: str | None = None
        words = text.split()
        i = 0
        while i < len(words):
            w = words[i]
            if w in ("/dryrun", "/debug"):
                flags.add(w[1:])  # strip the /
                i += 1
            elif w == "/model" and i + 1 < len(words):
                model = words[i + 1]
                i += 2
            else:
                break
        message = " ".join(words[i:])
        return flags, model, message

    async def _run_dryrun(
        self, user_id: int, text: str, show_debug: bool = False,
        model_override: str | None = None,
    ) -> list[BotResponse]:
        """Run the full pipeline without any DB writes.

        /dryrun: returns only the bot reply (incognito for one message).
        /debug:  same + shows extracted facts, visibility, nudges, context used.
        """
        from pairtner.core.memory import parse_memory_ops, parse_settings_ops

        if not text:
            return [BotResponse("Usage: /dryrun <message> or /debug <message>")]

        lang = await self._get_lang(user_id)

        # Build context (reads DB, no writes)
        ctx = await self._build_ctx(user_id, current_message=text)
        voice_text, damp_instr = await self._get_enriched_style(user_id)
        partner_summary = await self._get_partner_summary(user_id)
        goals_text = await self._get_goals_text(user_id)
        smart_ctx = await build_smart_context(
            self.db_path, user_id, text, self.config,
        )
        system, content = build_chat_prompt(
            ctx, text, voice_text=voice_text,
            dampening_instruction=damp_instr,
            partner_summary=partner_summary,
            goals_text=goals_text,
            smart_ctx=smart_ctx,
        )

        # Call LLM (the only external cost -- one LLM call)
        api_key = await self._get_couple_api_key(user_id)
        result = await self.llm.generate(
            content, system, model=model_override, api_key_override=api_key,
        )

        # --- NO DB WRITES from here ---
        reply = result.get("reply", "")

        if not show_debug:
            # /dryrun: just the reply, marked as dry run
            model_note = f" via `{model_override}`" if model_override else ""
            return [BotResponse(f"🧪 *Dry run{model_note}* (nothing saved)\n\n{reply}")]

        # /debug: full diagnostic output
        model_note = f" via `{model_override}`" if model_override else ""
        parts = [f"🔬 *Debug mode{model_note}* (nothing saved)\n\n**Bot reply:**\n{reply}"]

        # Parse memory ops (but don't apply them)
        memory_ops_raw = result.get("memory_ops", [])
        if memory_ops_raw:
            user_facts = await db_mod.get_active_facts(self.db_path, user_id)
            user_fact_ids = {f["id"] for f in user_facts}
            ops = parse_memory_ops(
                memory_ops_raw, user_fact_ids, v2=True,
            )
            if ops:
                facts_lines = []
                for op in ops:
                    if op.op == "add":
                        vis_icon = {"private": "🔒", "shared": "👥"}.get(op.visibility, "❓")
                        line = f"  {vis_icon} `{op.kind}` {op.text}"
                        if op.nudge_suggestion:
                            line += f"\n    → nudge: {op.nudge_suggestion}"
                        facts_lines.append(line)
                    elif op.op == "status":
                        facts_lines.append(f"  📝 fact #{op.id} → {op.status}")
                    elif op.op == "cancel_nudge":
                        facts_lines.append(f"  ❌ cancel nudge #{op.id}: {op.why}")
                parts.append("**Facts extracted:**\n" + "\n".join(facts_lines))
            else:
                parts.append("**Facts extracted:** none")
        else:
            parts.append("**Facts extracted:** none")

        # Settings ops
        settings_raw = result.get("settings_ops", [])
        if settings_raw:
            s_ops = parse_settings_ops(settings_raw)
            if s_ops:
                parts.append("**Settings changes:**\n" + "\n".join(
                    f"  ⚙️ {s.key} → {s.value}" for s in s_ops
                ))

        # Context summary
        ctx_lines = [
            f"  Core facts: {len(ctx.private_facts)} items",
            f"  Recent messages: {len(ctx.recent_messages)}",
        ]
        if ctx.shared_facts:
            ctx_lines.append(f"  Shared facts: {len(ctx.shared_facts)}")
        if smart_ctx:
            ctx_lines.append(f"  Smart context: {smart_ctx.total_fact_count} total facts")
        parts.append("**Context used:**\n" + "\n".join(ctx_lines))

        return [BotResponse("\n\n".join(parts))]

    async def dryrun_extraction(self, user_id: int | None, text: str) -> dict:
        """Structured dry run for the admin playground: prompts, raw LLM
        response, parsed ops and the facts that WOULD be saved. Reads the DB
        for context only; never writes."""
        import dataclasses

        from pairtner.core.memory import parse_memory_ops, parse_settings_ops
        from pairtner.core.prompts import UserContext

        if user_id is None:
            ctx = UserContext()
            system, content = build_chat_prompt(ctx, text)
            api_key = None
            fact_ids: set[int] = set()
        else:
            ctx = await self._build_ctx(user_id, current_message=text)
            voice_text, damp_instr = await self._get_enriched_style(user_id)
            smart_ctx = await build_smart_context(
                self.db_path, user_id, text, self.config,
            )
            system, content = build_chat_prompt(
                ctx, text, voice_text=voice_text,
                dampening_instruction=damp_instr,
                partner_summary=await self._get_partner_summary(user_id),
                goals_text=await self._get_goals_text(user_id),
                smart_ctx=smart_ctx,
            )
            api_key = await self._get_couple_api_key(user_id)
            fact_ids = {f["id"] for f in await db_mod.get_active_facts(self.db_path, user_id)}

        result = await self.llm.generate(content, system, api_key_override=api_key)
        llm_response = dict(result)
        ops = parse_memory_ops(llm_response.get("memory_ops", []), fact_ids, v2=True)
        s_ops = parse_settings_ops(llm_response.get("settings_ops", []))
        parsed_ops = [dataclasses.asdict(o) for o in ops]
        return {
            "system_prompt": system,
            "user_content": content,
            "llm_response": llm_response,
            "parsed": {
                "reply": llm_response.get("reply", ""),
                "memory_ops": parsed_ops,
                "settings_ops": [dataclasses.asdict(s) for s in s_ops],
            },
            "would_save": [o for o in parsed_ops if o["op"] == "add"],
        }

    async def handle_dryrun(self, user_id: int, text: str | None) -> list[BotResponse]:
        """Handle /dryrun — full pipeline, no DB writes. Supports combined flags."""
        flags, model, message = self._parse_message_flags(text or "")
        flags.add("dryrun")
        return await self._run_dryrun(
            user_id, message,
            show_debug="debug" in flags,
            model_override=model,
        )

    async def handle_debug(self, user_id: int, text: str | None) -> list[BotResponse]:
        """Handle /debug.

        Without arguments: memory state report.  With a message: full pipeline,
        no DB writes, show internals (supports combined flags).
        """
        if not (text or "").strip():
            return await self.handle_memory_debug(user_id)
        flags, model, message = self._parse_message_flags(text or "")
        flags.add("debug")
        return await self._run_dryrun(
            user_id, message,
            show_debug=True,
            model_override=model,
        )

    def _default_model_name(self) -> str:
        cfg = self.config
        return getattr(cfg, "llm_model_default", "") or getattr(cfg, "model_default", "") or "default"

    async def _get_user_model(self, user_id: int) -> str | None:
        """The user's preferred model (``llm_model`` preference) or None."""
        val = await db_mod.get_user_preference(
            self.db_path, user_id, MODEL_PREF_KEY, default=None,
        )
        return val or None

    async def _set_user_model(self, user_id: int, model: str | None) -> None:
        """Set the preferred model; None/empty clears the override."""
        await db_mod.set_user_preference(
            self.db_path, user_id, MODEL_PREF_KEY, model or "",
        )

    async def handle_model(self, user_id: int, text: str | None) -> list[BotResponse]:
        """Handle /model <name> <message> — use a specific model for one message.

        This is a live message (DB writes happen), just with a different model.
        Combines with /dryrun and /debug if present in the remaining text.
        """
        if not is_user_allowed(self.config, user_id):
            return []
        if not text or not text.strip():
            current = await self._get_user_model(user_id)
            default = self._default_model_name()
            shown = f"<b>{current}</b> (yours)" if current else f"<b>{default}</b> (server default)"
            # Show available providers
            providers = getattr(self.llm, 'providers', None)
            if providers is None:
                # TracingLLM wraps the inner client
                inner = getattr(self.llm, '_inner', self.llm)
                providers = getattr(inner, 'providers', None)
            provider_line = ""
            if providers and len(providers) > 1:
                provider_line = (
                    "\n\n<b>Providers:</b> " + ", ".join(providers)
                    + "\nUse prefix to switch: <code>/model gemini:flash-2</code> or <code>/model ollama:qwen3:8b</code>"
                )
            return [BotResponse(
                f"🤖 Model: {shown}{provider_line}\n\n"
                "/model &lt;name&gt; — use this model for all replies\n"
                "/model reset — back to the server default\n"
                "/model &lt;name&gt; &lt;message&gt; — one message only\n"
                "/model gemini:&lt;name&gt; — switch to Gemini provider\n"
                "/model ollama:&lt;name&gt; — switch to Ollama provider"
            )]
        parts = text.strip().split(maxsplit=1)
        model_name = parts[0]
        message = parts[1] if len(parts) > 1 else ""
        if model_name.lower() == "reset" and not message:
            await self._set_user_model(user_id, None)
            return [BotResponse(
                f"🤖 Model reset to server default (<b>{self._default_model_name()}</b>)."
            )]
        if not message:
            # Validate before setting persistent model preference
            validate = getattr(self.llm, 'validate_model', None)
            if validate is None:
                inner = getattr(self.llm, '_inner', None)
                validate = getattr(inner, 'validate_model', None) if inner else None
            if validate:
                err = await validate(model_name)
                if err:
                    return [BotResponse(f"❌ Cannot use <b>{model_name}</b>: {err}")]
            await self._set_user_model(user_id, model_name)
            return [BotResponse(f"🤖 Model set to <b>{model_name}</b>.")]

        # Check for /dryrun or /debug in the remaining message
        flags, nested_model, clean = self._parse_message_flags(message)
        if "dryrun" in flags or "debug" in flags:
            return await self._run_dryrun(
                user_id, clean,
                show_debug="debug" in flags,
                model_override=nested_model or model_name,
            )

        # Live message with model override -- use the normal handler but swap model
        # Store original model, swap, call, restore
        original_generate = self.llm.generate

        async def _override_generate(content, system, model=None, **kw):
            return await original_generate(content, system, model=model_name, **kw)

        self.llm.generate = _override_generate  # type: ignore[method-assign]
        try:
            responses = await self._handle_plain(user_id, clean or message)
        finally:
            self.llm.generate = original_generate  # type: ignore[method-assign]

        # Prepend model indicator
        if responses:
            responses[0].text = f"🔄 *Using {model_name}*\n\n" + responses[0].text
        return responses
