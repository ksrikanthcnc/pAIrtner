"""Slash-command handler methods for BotService.

These methods are mixed into :class:`~couples_bot.handlers.service.BotService`
via the :class:`CommandsMixin` base class.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from couples_bot import db as db_mod
from couples_bot.config import is_user_allowed
from couples_bot.crypto import decrypt_value, encrypt_value, mask_key
from couples_bot.i18n import t
from couples_bot.profile_updater import process_llm_extras
from couples_bot.prompts import build_mediate_prompt, build_tell_prompt

from .models import BotResponse, InlineButton, format_fact_date, format_relative_time, parse_tags

# Category key → i18n key mapping
_CATEGORY_I18N: dict[str | None, str] = {
    "relationship": "cat_relationship",
    "household": "cat_household",
    "emotional": "cat_emotional",
    "work": "cat_work",
    "health": "cat_health",
    "other": "cat_other",
    None: "cat_other",
}


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

        # Fire-and-forget: auto-update profiles from LLM extras
        await process_llm_extras(self.db_path, user_id, result, self.config)

        draft = result.get("relay_draft") or result["reply"]

        pending_id = await db_mod.create_pending(
            self.db_path, user_id, user["partner_id"], draft
        )

        buttons = self._approval_buttons(pending_id, lang, skip_cooldown)
        return [BotResponse(
            t(lang, "tell_draft_header") + draft,
            buttons=buttons,
        )]

    # ── /history ───────────────────────────────────────────────────────

    async def handle_history(
        self, user_id: int, arg: str | None
    ) -> list[BotResponse]:
        """Show the user's recent sent and received relay messages."""
        if not is_user_allowed(self.config, user_id):
            return []

        lang = await self._get_lang(user_id)

        limit = 10
        if arg and arg.strip():
            try:
                limit = int(arg.strip())
            except ValueError:
                return [BotResponse(t(lang, "history_usage"))]

        relays = await db_mod.get_sent_relays(self.db_path, user_id, limit)
        if not relays:
            return [BotResponse(t(lang, "history_empty"))]

        parts = [t(lang, "history_header")]
        for row in relays:
            text = row.get("edited_draft") or row["draft"]
            sent_at = row.get("sent_at") or ""
            try:
                dt = datetime.fromisoformat(sent_at)
                date_str = dt.strftime("%b %d %H:%M")
            except (ValueError, TypeError):
                date_str = "?"

            if row["sender_id"] == user_id:
                parts.append(t(lang, "history_sent", date=date_str, message=text))
            else:
                parts.append(t(lang, "history_received", date=date_str, message=text))

        return [BotResponse("".join(parts))]

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
        """List all stored facts for the user, grouped by fact type and category."""
        if not is_user_allowed(self.config, user_id):
            return []

        lang = await self._get_lang(user_id)

        private_facts = await db_mod.get_active_facts(self.db_path, user_id, "private")
        shared_facts = await db_mod.get_active_facts(self.db_path, user_id, "shared")

        if not private_facts and not shared_facts:
            return [BotResponse(t(lang, "whatdoyouknow_empty"))]

        all_facts = private_facts + shared_facts

        parts = [t(lang, "whatdoyouknow_header")]

        # Group by fact_type first, then by category within each type
        type_order = ["stable", "situational", "transient"]
        type_labels = {
            "stable": t(lang, "fact_type_stable"),
            "situational": t(lang, "fact_type_situational"),
            "transient": t(lang, "fact_type_transient"),
        }
        cat_order = [
            "relationship", "household", "emotional",
            "work", "health", "other", None,
        ]

        for ftype in type_order:
            facts_of_type = [
                f for f in all_facts
                if (f.get("fact_type") or "stable") == ftype
            ]
            if not facts_of_type:
                continue

            parts.append(f"\n<b>{type_labels.get(ftype, ftype)}:</b>\n")

            has_categories = any(f.get("category") for f in facts_of_type)
            if has_categories:
                grouped: dict[str | None, list[dict]] = {}
                for f in facts_of_type:
                    cat = f.get("category") or None
                    grouped.setdefault(cat, []).append(f)
                for cat in cat_order:
                    facts_in_cat = grouped.get(cat)
                    if not facts_in_cat:
                        continue
                    i18n_key = _CATEGORY_I18N.get(cat, "cat_other")
                    parts.append(f"  <i>{t(lang, i18n_key)}:</i>\n")
                    for f in facts_in_cat:
                        parts.append(self._format_fact_line(f, lang))
            else:
                for f in facts_of_type:
                    parts.append(self._format_fact_line(f, lang))

        return [BotResponse("".join(parts))]

    def _format_fact_line(self, f: dict, lang: str) -> str:
        """Format a single fact line with age and expiry info."""
        age = format_relative_time(f.get("created_at"))
        line = t(
            lang, "fact_item",
            id=f["id"], date=age, text=f["text"],
            visibility=f["visibility"],
        )
        # Show expiry warning if approaching
        if f.get("expires_at"):
            from datetime import datetime, timezone
            try:
                exp = datetime.fromisoformat(f["expires_at"])
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                remaining = exp - now
                if remaining.total_seconds() > 0:
                    days = remaining.days
                    if days > 0:
                        line = line.rstrip("\n") + f" {t(lang, 'fact_expiring', time=f'{days}d')}\n"
                    else:
                        hours = int(remaining.total_seconds() // 3600)
                        line = line.rstrip("\n") + f" {t(lang, 'fact_expiring', time=f'{hours}h')}\n"
            except (ValueError, TypeError):
                pass
        return line

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

        # Voice, mood, dampening
        profile = await db_mod.get_user_profile(self.db_path, user_id)
        if profile:
            voice = profile.get("default_voice") or "casual_en"
            intensity = profile.get("voice_intensity", 0.5)
            parts.append(t(lang, "status_voice", voice=voice, intensity=intensity))

        active_mood = await db_mod.get_active_mood(self.db_path, user_id)
        if active_mood:
            parts.append(t(lang, "status_mood", mood=active_mood["mood"]))

        from couples_bot.dampening import resolve_dampening as _resolve
        damp_level, _ = await _resolve(
            self.db_path, user_id, self.config, has_blunt_tag=False,
        )
        parts.append(t(lang, "status_dampening", level=damp_level))

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

    # ── /style ───────────────────────────────────────────────────────────

    async def handle_style(
        self, user_id: int, arg: str | None
    ) -> list[BotResponse]:
        """Show or change the user's voice and intensity settings."""
        if not is_user_allowed(self.config, user_id):
            return []

        lang = await self._get_lang(user_id)
        profile = await db_mod.ensure_user_profile(self.db_path, user_id)

        if arg and arg.strip():
            # Direct set: /style casual_en or /style 0.5
            # handled via callbacks instead; show usage
            pass

        # Show current + inline buttons
        voice = profile.get("default_voice") or "casual_en"
        intensity = profile.get("voice_intensity", 0.5)

        voice_labels = {
            "casual_en": "Casual English",
            "telugu": "Telugu",
            "hindi": "Hindi",
            "formal_en": "Formal English",
        }
        voice_display = voice_labels.get(voice, voice)

        text = (
            t(lang, "style_header")
            + t(lang, "style_current", voice=voice_display, intensity=intensity)
        )

        voice_buttons = [
            InlineButton("🗣️ Casual EN", "sv:casual_en"),
            InlineButton("🗣️ Telugu", "sv:telugu"),
            InlineButton("🗣️ Hindi", "sv:hindi"),
            InlineButton("🗣️ Formal EN", "sv:formal_en"),
        ]
        intensity_buttons = [
            InlineButton("Light 0.3", "si:0.3"),
            InlineButton("Medium 0.5", "si:0.5"),
            InlineButton("Deep 0.8", "si:0.8"),
        ]

        return [BotResponse(text, buttons=[voice_buttons, intensity_buttons])]

    # ── /mood ────────────────────────────────────────────────────────────

    async def handle_mood(
        self, user_id: int, arg: str | None
    ) -> list[BotResponse]:
        """Show or set the user's current mood."""
        if not is_user_allowed(self.config, user_id):
            return []

        lang = await self._get_lang(user_id)

        if not arg or not arg.strip():
            # Show current mood
            active = await db_mod.get_active_mood(self.db_path, user_id)
            if not active:
                return [BotResponse(t(lang, "mood_none"))]
            expires = active.get("expires_at", "")
            return [BotResponse(t(
                lang, "mood_current",
                mood=active["mood"], expires=expires,
            ))]

        mood = arg.strip().lower()
        valid_moods = set(self.config.mood_styles.keys())
        if mood not in valid_moods:
            return [BotResponse(t(lang, "mood_usage"))]

        hours = self.config.mood_default_expiry_hours
        expires_at = (
            datetime.now(timezone.utc) + timedelta(hours=hours)
        ).isoformat()

        style = self.config.mood_styles.get(mood, {})
        await db_mod.set_user_mood(
            self.db_path, user_id, mood, expires_at,
            dampening_override=style.get("dampening_override"),
            response_length=style.get("response_length", "normal"),
        )

        return [BotResponse(t(lang, "mood_set", mood=mood, hours=hours))]
