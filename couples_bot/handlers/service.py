"""Core BotService class — transport-agnostic bot logic.

All handler methods return ``list[BotResponse]``.  The thin adapter layer
in ``__main__`` converts these into Telegram-specific API calls.
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Any

from couples_bot import db as db_mod
from couples_bot.config import Config, is_user_allowed
from couples_bot.crypto import decrypt_value
from couples_bot.i18n import t
from couples_bot.llm import LLMClient
from couples_bot.prompts import UserContext
from couples_bot.sanitize import sanitize_input

from .callbacks import CallbacksMixin
from .commands import CommandsMixin
from .models import BotResponse, InlineButton, parse_tags
from .tags import TagsMixin

logger = logging.getLogger(__name__)


class BotService(TagsMixin, CommandsMixin, CallbacksMixin):
    """Stateful bot logic — transport-agnostic.

    Inherits command, callback, and tag handlers from mixin classes.
    Core infrastructure (pairing, routing, helpers) lives here.
    """

    def __init__(
        self, config: Config, db_path: str, llm: LLMClient
    ) -> None:
        self.config = config
        self.db_path = db_path
        self.llm = llm

        # Ephemeral in-memory state (lost on restart — acceptable for MVP)
        self._pairing_codes: dict[str, int] = {}
        self._edit_state: dict[int, int] = {}
        self._relay_choices: dict[str, dict[str, Any]] = {}
        self._forget_confirm: set[int] = set()
        self._batch_item_edit_state: dict[int, int] = {}

    # ── helpers ──────────────────────────────────────────────────────────

    async def _get_lang(self, user_id: int) -> str:
        """Return the user's preferred language code, defaulting to ``'en'``."""
        user = await db_mod.get_user(self.db_path, user_id)
        return (user or {}).get("lang") or "en"

    async def _get_couple_api_key(self, user_id: int) -> str | None:
        """Resolve the BYO Gemini key for this user's couple, or ``None``."""
        user = await db_mod.get_user(self.db_path, user_id)
        if not user or not user.get("partner_id"):
            return None
        couple_id = db_mod.make_couple_id(user_id, user["partner_id"])
        encrypted = await db_mod.get_couple_config(
            self.db_path, couple_id, "gemini_key"
        )
        if not encrypted:
            return None
        encryption_key = os.environ.get("DB_ENCRYPTION_KEY", "")
        if not encryption_key:
            return None
        try:
            return decrypt_value(encrypted, encryption_key)
        except Exception:
            return None

    async def _build_ctx(self, user_id: int) -> UserContext:
        """Build the prompt context for a user."""
        data = await db_mod.get_user_context_data(self.db_path, user_id)
        return UserContext(**data)

    def _approval_buttons(
        self, pending_id: int, lang: str, skip_cooldown: bool = False
    ) -> list[list[InlineButton]]:
        """Build approve / edit / discard inline buttons for a pending draft."""
        ap_data = f"apn:{pending_id}" if skip_cooldown else f"ap:{pending_id}"
        return [[
            InlineButton(t(lang, "btn_approve"), ap_data),
            InlineButton(t(lang, "btn_edit"), f"ed:{pending_id}"),
            InlineButton(t(lang, "btn_discard"), f"di:{pending_id}"),
        ]]

    def _choice_buttons(
        self, choice_id: str, lang: str
    ) -> list[list[InlineButton]]:
        """Build save / relay inline buttons for a plain-message reply."""
        return [[
            InlineButton(t(lang, "btn_save"), f"cs:{choice_id}"),
            InlineButton(t(lang, "btn_relay"), f"cr:{choice_id}"),
        ]]

    async def _save_facts(
        self, user_id: int, new_facts: list[dict[str, str]]
    ) -> int:
        """Persist LLM-extracted facts and return the count saved."""
        count = 0
        for fact in new_facts:
            text = fact.get("text", "").strip()
            vis = fact.get("visibility", "private")
            if vis not in ("private", "shared"):
                vis = "private"
            category = fact.get("category") or None
            if text:
                await db_mod.add_fact(
                    self.db_path, user_id, text, vis, source="llm",
                    category=category,
                )
                count += 1
        return count

    async def _check_rate_limit(self, user_id: int) -> list[BotResponse] | None:
        """Check per-minute and per-day rate limits.

        Returns a rate-limited response if exceeded, otherwise ``None``.
        """
        lang = await self._get_lang(user_id)

        allowed, _ = await db_mod.check_rate_limit(
            self.db_path, user_id, "minute",
            self.config.rate_limit_per_minute, 60,
        )
        if not allowed:
            return [BotResponse(t(lang, "rate_limited"))]

        allowed, _ = await db_mod.check_rate_limit(
            self.db_path, user_id, "day",
            self.config.rate_limit_per_day, 86400,
        )
        if not allowed:
            return [BotResponse(t(lang, "rate_limit_day"))]

        return None

    async def _increment_rate_counters(self, user_id: int) -> None:
        """Increment both per-minute and per-day rate counters."""
        await db_mod.increment_rate_counter(self.db_path, user_id, "minute", 60)
        await db_mod.increment_rate_counter(self.db_path, user_id, "day", 86400)

    # ── /start ───────────────────────────────────────────────────────────

    async def handle_start(
        self, user_id: int, user_name: str | None
    ) -> list[BotResponse]:
        """Generate a pairing code or notify if already paired."""
        if not is_user_allowed(self.config, user_id):
            return []

        user = await db_mod.ensure_user(self.db_path, user_id, user_name)
        lang = user.get("lang") or "en"

        if user.get("partner_id"):
            partner = await db_mod.get_user(self.db_path, user["partner_id"])
            pname = (partner or {}).get("name") or "your partner"
            return [BotResponse(
                t(lang, "already_paired", partner_name=pname)
                + "\n\n" + t(lang, "not_therapy")
                + "\n\n" + t(lang, "crisis_resources")
            )]

        code = secrets.token_hex(3).upper()
        self._pairing_codes[code] = user_id

        parts = [
            t(lang, "welcome", code=code),
            t(lang, "not_therapy"),
            t(lang, "crisis_resources"),
        ]
        return [BotResponse("\n\n".join(parts))]

    # ── /join ────────────────────────────────────────────────────────────

    async def handle_join(
        self, user_id: int, user_name: str | None, code: str | None
    ) -> list[BotResponse]:
        """Pair with another user via their pairing code."""
        if not is_user_allowed(self.config, user_id):
            return []

        user = await db_mod.ensure_user(self.db_path, user_id, user_name)
        lang = user.get("lang") or "en"

        if not code:
            return [BotResponse(t(lang, "join_need_code"))]

        if user.get("partner_id"):
            return [BotResponse(t(lang, "join_already_paired"))]

        code = code.strip().upper()
        partner_id = self._pairing_codes.get(code)

        if partner_id is None:
            return [BotResponse(t(lang, "join_invalid_code"))]
        if partner_id == user_id:
            return [BotResponse(t(lang, "join_self"))]

        del self._pairing_codes[code]
        await db_mod.set_partner(self.db_path, user_id, partner_id)

        partner = await db_mod.get_user(self.db_path, partner_id)
        pname = (partner or {}).get("name") or "your partner"
        uname = user_name or user.get("name") or "your partner"

        responses: list[BotResponse] = [
            BotResponse(t(lang, "join_success", partner_name=pname)),
        ]
        plang = (partner or {}).get("lang") or "en"
        responses.append(BotResponse(
            t(plang, "join_partner_notify", partner_name=uname),
            target_user_id=partner_id,
        ))
        return responses

    # ── /help ────────────────────────────────────────────────────────────

    async def handle_help(self, user_id: int) -> list[BotResponse]:
        """Show the help message with available commands."""
        if not is_user_allowed(self.config, user_id):
            return []

        lang = await self._get_lang(user_id)
        parts = [
            t(lang, "help"),
            t(lang, "not_therapy"),
            t(lang, "crisis_resources"),
        ]
        return [BotResponse("\n\n".join(parts))]

    # ── /lang ────────────────────────────────────────────────────────────

    async def handle_lang(
        self, user_id: int, arg: str | None
    ) -> list[BotResponse]:
        """Switch the user's language preference."""
        if not is_user_allowed(self.config, user_id):
            return []

        await db_mod.ensure_user(self.db_path, user_id)
        lang = await self._get_lang(user_id)

        if not arg or arg.strip().lower() not in ("en", "te"):
            return [BotResponse(t(lang, "lang_usage"))]

        new_lang = arg.strip().lower()
        await db_mod.update_user_lang(self.db_path, user_id, new_lang)
        return [BotResponse(t(new_lang, "lang_changed"))]

    # ── plain message ────────────────────────────────────────────────────

    async def handle_message(
        self, user_id: int, user_name: str | None, text: str
    ) -> list[BotResponse]:
        """Route an incoming plain text message based on tags and state."""
        if not is_user_allowed(self.config, user_id):
            return []

        await db_mod.ensure_user(self.db_path, user_id, user_name)

        # Clear any stale forget-confirm state on new message
        self._forget_confirm.discard(user_id)

        # Check if user is in batch-item edit state
        if user_id in self._batch_item_edit_state:
            return await self._handle_batch_item_edit_input(user_id, text)

        # Check if user is in edit-pending state
        if user_id in self._edit_state:
            return await self._handle_edit_input(user_id, text)

        # Rate limiting (for actual message processing, not edit inputs)
        rate_resp = await self._check_rate_limit(user_id)
        if rate_resp:
            return rate_resp

        await self._increment_rate_counters(user_id)

        tags, clean = parse_tags(text)

        # Sanitize input — strip injection patterns before LLM processing
        clean, stripped = sanitize_input(clean)
        if stripped:
            logger.warning(
                "Sanitized input from user %d: stripped patterns %s",
                user_id, stripped,
            )

        if "#keep" in tags:
            return await self._handle_keep(user_id, clean)
        if "#blunt" in tags or "#honest" in tags:
            if "#send" in tags:
                # Blunt reply for sender + gentle relay draft for partner
                blunt_resp = await self._handle_blunt(user_id, clean)
                send_resp = await self._handle_send(user_id, clean, "#now" in tags)
                return blunt_resp + send_resp
            return await self._handle_blunt(user_id, clean)
        if "#send" in tags:
            return await self._handle_send(user_id, clean, "#now" in tags)
        if "#deep" in tags:
            return await self._handle_deep(user_id, clean)

        return await self._handle_plain(user_id, clean)

    # ── callback queries (inline buttons) ────────────────────────────────

    async def handle_callback(
        self, user_id: int, callback_data: str
    ) -> list[BotResponse]:
        """Dispatch an inline-button callback to the appropriate handler."""
        if not is_user_allowed(self.config, user_id):
            return []

        rate_resp = await self._check_rate_limit(user_id)
        if rate_resp:
            return rate_resp

        lang = await self._get_lang(user_id)

        if callback_data.startswith("ap:") or callback_data.startswith("apn:"):
            return await self._cb_approve(user_id, callback_data, lang)
        if callback_data.startswith("ed:"):
            return await self._cb_edit(user_id, callback_data, lang)
        if callback_data.startswith("di:"):
            return await self._cb_discard(user_id, callback_data, lang)
        if callback_data.startswith("cr:"):
            return await self._cb_choose_relay(user_id, callback_data, lang)
        if callback_data.startswith("cs:"):
            return await self._cb_choose_save(user_id, callback_data, lang)

        # Batch review callbacks
        if callback_data.startswith("ba:"):
            return await self._cb_batch_send_all(user_id, callback_data, lang)
        if callback_data.startswith("br:"):
            return await self._cb_batch_review_each(user_id, callback_data, lang)
        if callback_data.startswith("bd:"):
            return await self._cb_batch_discard_all(user_id, callback_data, lang)
        if callback_data.startswith("bia:"):
            return await self._cb_batch_item_approve(user_id, callback_data, lang)
        if callback_data.startswith("bie:"):
            return await self._cb_batch_item_edit(user_id, callback_data, lang)
        if callback_data.startswith("bix:"):
            return await self._cb_batch_item_discard(user_id, callback_data, lang)

        return [BotResponse(t(lang, "error_generic"))]
