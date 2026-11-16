"""Slash-command handler methods for BotService.

These methods are mixed into :class:`~couples_bot.handlers.service.BotService`
via the :class:`CommandsMixin` base class.
"""

from __future__ import annotations

import os

from couples_bot import db as db_mod
from couples_bot.config import is_user_allowed
from couples_bot.crypto import decrypt_value, encrypt_value, mask_key
from couples_bot.i18n import t
from couples_bot.prompts import build_mediate_prompt, build_tell_prompt

from .models import BotResponse, format_fact_date, format_relative_time, parse_tags


class CommandsMixin:
    """Mixin providing slash-command handlers for BotService."""

    # ── /tell ────────────────────────────────────────────────────────────

    async def handle_tell(
        self, user_id: int, text: str | None
    ) -> list[BotResponse]:
        """Draft a softened relay message for the user's partner."""
        if not is_user_allowed(self.config, user_id):
            return []

        lang = await self._get_lang(user_id)

        rate_resp = await self._check_rate_limit(user_id)
        if rate_resp:
            return rate_resp

        user = await db_mod.get_user(self.db_path, user_id)
        if not user or not user.get("partner_id"):
            return [BotResponse(t(lang, "tell_no_partner"))]
        if not text or not text.strip():
            return [BotResponse(t(lang, "tell_need_message"))]

        await self._increment_rate_counters(user_id)

        tags, clean = parse_tags(text)
        skip_cooldown = "#now" in tags

        ctx = await self._build_ctx(user_id)
        system, content = build_tell_prompt(ctx, clean)

        api_key = await self._get_couple_api_key(user_id)
        await db_mod.log_message(self.db_path, user_id, "user", clean)
        result = await self.llm.generate(
            content, system, api_key_override=api_key,
        )
        await db_mod.log_message(self.db_path, user_id, "assistant", result["reply"])

        await self._save_facts(user_id, result.get("new_facts", []))

        draft = result.get("relay_draft") or result["reply"]

        pending_id = await db_mod.create_pending(
            self.db_path, user_id, user["partner_id"], draft
        )

        buttons = self._approval_buttons(pending_id, lang, skip_cooldown)
        return [BotResponse(
            t(lang, "tell_draft_header") + draft,
            buttons=buttons,
        )]

    # ── /mediate ─────────────────────────────────────────────────────────

    async def handle_mediate(self, user_id: int) -> list[BotResponse]:
        """Provide a neutral perspective based on shared facts."""
        if not is_user_allowed(self.config, user_id):
            return []

        lang = await self._get_lang(user_id)
        user = await db_mod.get_user(self.db_path, user_id)
        if not user or not user.get("partner_id"):
            return [BotResponse(t(lang, "mediate_no_partner"))]

        shared = await db_mod.get_shared_facts_for_pair(
            self.db_path, user_id, user["partner_id"]
        )
        shared_texts = [f["text"] for f in shared]
        if not shared_texts:
            return [BotResponse(t(lang, "mediate_no_shared"))]

        system, content = build_mediate_prompt(shared_texts)

        model = self.config.model_heavy[0] if self.config.model_heavy else None
        api_key = await self._get_couple_api_key(user_id)
        result = await self.llm.generate(
            content, system, model=model, api_key_override=api_key,
        )

        await db_mod.log_message(self.db_path, user_id, "assistant", result["reply"])

        if model and hasattr(self.llm, "check_usage_warning"):
            warning = await self.llm.check_usage_warning(model)
            if warning:
                result["reply"] += "\n\n" + warning

        return [BotResponse(result["reply"])]

    # ── /whatdoyouknow ───────────────────────────────────────────────────

    async def handle_whatdoyouknow(self, user_id: int) -> list[BotResponse]:
        """List all stored facts for the user."""
        if not is_user_allowed(self.config, user_id):
            return []

        lang = await self._get_lang(user_id)

        private_facts = await db_mod.get_facts(self.db_path, user_id, "private")
        shared_facts = await db_mod.get_facts(self.db_path, user_id, "shared")

        if not private_facts and not shared_facts:
            return [BotResponse(t(lang, "whatdoyouknow_empty"))]

        parts = [t(lang, "whatdoyouknow_header")]

        if private_facts:
            parts.append(t(lang, "whatdoyouknow_private"))
            for f in private_facts:
                date = format_fact_date(f.get("created_at"))
                parts.append(t(
                    lang, "fact_item",
                    id=f["id"], date=date, text=f["text"], visibility="private",
                ))

        if shared_facts:
            parts.append(t(lang, "whatdoyouknow_shared"))
            for f in shared_facts:
                date = format_fact_date(f.get("created_at"))
                parts.append(t(
                    lang, "fact_item",
                    id=f["id"], date=date, text=f["text"], visibility="shared",
                ))

        return [BotResponse("".join(parts))]

    # ── /status ──────────────────────────────────────────────────────────

    async def handle_status(self, user_id: int) -> list[BotResponse]:
        """Show the user's relationship dashboard."""
        if not is_user_allowed(self.config, user_id):
            return []

        user = await db_mod.ensure_user(self.db_path, user_id)
        lang = user.get("lang") or "en"

        if not user.get("partner_id"):
            return [BotResponse(t(lang, "status_not_paired"))]

        partner = await db_mod.get_user(self.db_path, user["partner_id"])
        partner_name = (partner or {}).get("name") or "partner"

        private_facts = await db_mod.get_facts(self.db_path, user_id, "private")
        shared_facts = await db_mod.get_facts(self.db_path, user_id, "shared")
        pending_count = await db_mod.get_pending_count(self.db_path, user_id)
        usage_today = await db_mod.get_usage_today(self.db_path)
        last_active = await db_mod.get_partner_last_active(
            self.db_path, user["partner_id"]
        )

        parts = [t(lang, "status_header")]
        parts.append(t(lang, "status_partner", partner_name=partner_name))
        parts.append(t(
            lang, "status_facts",
            private=len(private_facts), shared=len(shared_facts),
        ))
        parts.append(t(lang, "status_pending", count=pending_count))
        parts.append(t(lang, "status_usage", count=usage_today))
        parts.append(t(
            lang, "status_partner_active",
            time=format_relative_time(last_active),
        ))

        if user.get("paused"):
            parts.append(t(lang, "status_paused_note"))
        if partner and partner.get("paused"):
            parts.append(t(lang, "status_partner_paused_note"))

        return [BotResponse("".join(parts))]

    # ── /pause ───────────────────────────────────────────────────────────

    async def handle_pause(self, user_id: int) -> list[BotResponse]:
        """Pause relay delivery for the user."""
        if not is_user_allowed(self.config, user_id):
            return []

        user = await db_mod.ensure_user(self.db_path, user_id)
        lang = user.get("lang") or "en"

        if not user.get("partner_id"):
            return [BotResponse(t(lang, "not_paired"))]

        await db_mod.set_user_paused(self.db_path, user_id, True)
        return [BotResponse(t(lang, "paused_confirm"))]

    # ── /resume ──────────────────────────────────────────────────────────

    async def handle_resume(self, user_id: int) -> list[BotResponse]:
        """Resume relay delivery for the user."""
        if not is_user_allowed(self.config, user_id):
            return []

        user = await db_mod.ensure_user(self.db_path, user_id)
        lang = user.get("lang") or "en"

        await db_mod.set_user_paused(self.db_path, user_id, False)
        return [BotResponse(t(lang, "resumed_confirm"))]

    # ── /setup ────────────────────────────────────────────────────────────

    async def handle_setup(
        self, user_id: int, arg: str | None
    ) -> list[BotResponse]:
        """Configure the couple's BYO Gemini API key."""
        if not is_user_allowed(self.config, user_id):
            return []

        user = await db_mod.ensure_user(self.db_path, user_id)
        lang = user.get("lang") or "en"

        if not user.get("partner_id"):
            return [BotResponse(t(lang, "setup_not_paired"))]

        couple_id = db_mod.make_couple_id(user_id, user["partner_id"])

        # No arg → show current config
        if not arg or not arg.strip():
            stored = await db_mod.get_couple_config(
                self.db_path, couple_id, "gemini_key"
            )
            if stored:
                encryption_key = os.environ.get("DB_ENCRYPTION_KEY", "")
                if not encryption_key:
                    return [BotResponse(t(lang, "setup_no_encryption_key"))]
                try:
                    plaintext = decrypt_value(stored, encryption_key)
                    masked = mask_key(plaintext)
                except Exception:
                    masked = "****"
                return [BotResponse(
                    t(lang, "setup_show_config", api_key=masked)
                )]
            return [BotResponse(t(lang, "setup_key_not_set"))]

        parts = arg.strip().split(None, 1)
        sub_cmd = parts[0].lower()

        if sub_cmd != "gemini_key":
            return [BotResponse(t(lang, "setup_usage"))]

        # /setup gemini_key clear
        if len(parts) == 2 and parts[1].strip().lower() == "clear":
            deleted = await db_mod.delete_couple_config(
                self.db_path, couple_id, "gemini_key"
            )
            if deleted:
                return [BotResponse(t(lang, "setup_key_cleared"))]
            return [BotResponse(t(lang, "setup_key_not_set"))]

        # /setup gemini_key <key>
        if len(parts) < 2 or not parts[1].strip():
            return [BotResponse(t(lang, "setup_usage"))]

        api_key = parts[1].strip()
        encryption_key = os.environ.get("DB_ENCRYPTION_KEY", "")
        if not encryption_key:
            return [BotResponse(t(lang, "setup_no_encryption_key"))]

        encrypted = encrypt_value(api_key, encryption_key)
        await db_mod.set_couple_config(
            self.db_path, couple_id, "gemini_key", encrypted
        )
        masked = mask_key(api_key)
        return [BotResponse(t(lang, "setup_key_stored", api_key=masked))]

    # ── /forget ──────────────────────────────────────────────────────────

    async def handle_forget(
        self, user_id: int, arg: str | None
    ) -> list[BotResponse]:
        """Delete facts by ID or delete all facts (with confirmation)."""
        if not is_user_allowed(self.config, user_id):
            return []

        lang = await self._get_lang(user_id)

        if not arg or not arg.strip():
            return [BotResponse(t(lang, "forget_usage"))]

        arg = arg.strip()

        if arg.lower() == "all":
            if user_id not in self._forget_confirm:
                all_facts = await db_mod.get_facts(self.db_path, user_id)
                self._forget_confirm.add(user_id)
                return [BotResponse(
                    t(lang, "forget_confirm_all", count=len(all_facts))
                )]
            # Confirmed
            self._forget_confirm.discard(user_id)
            count = await db_mod.delete_all_facts(self.db_path, user_id)
            return [BotResponse(t(lang, "forget_all_done", count=count))]

        try:
            fact_id = int(arg)
        except ValueError:
            return [BotResponse(t(lang, "forget_usage"))]

        deleted = await db_mod.delete_fact(self.db_path, fact_id, user_id)
        if deleted:
            return [BotResponse(t(lang, "forget_done", id=fact_id))]
        return [BotResponse(t(lang, "forget_not_found"))]

    # ── /export ──────────────────────────────────────────────────────────

    async def handle_export(self, user_id: int) -> list[BotResponse]:
        """Export the user's facts as a text file."""
        if not is_user_allowed(self.config, user_id):
            return []

        lang = await self._get_lang(user_id)

        facts = await db_mod.get_facts(self.db_path, user_id)
        if not facts:
            return [BotResponse(t(lang, "export_empty"))]

        lines = [t(lang, "export_header")]
        for f in facts:
            vis = "🔒 private" if f["visibility"] == "private" else "🔓 shared"
            lines.append(f"- [{vis}] {f['text']}  (id: {f['id']})\n")

        content = "".join(lines)
        return [BotResponse(
            t(lang, "export_caption", count=len(facts)),
            document=("my_facts.txt", content),
        )]
