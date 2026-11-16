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
from couples_bot.search import search_facts

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

            ack_mark = ""
            if row["sender_id"] == user_id and row.get("acknowledged_at"):
                ack_mark = t(lang, "history_acknowledged")

            if row["sender_id"] == user_id:
                parts.append(t(lang, "history_sent", date=date_str, message=text) .rstrip("\n") + ack_mark + "\n")
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
        """Configure Gemini API keys with three-tier lookup."""
        if not is_user_allowed(self.config, user_id):
            return []

        user = await db_mod.ensure_user(self.db_path, user_id)
        lang = user.get("lang") or "en"

        if not user.get("partner_id"):
            return [BotResponse(t(lang, "setup_not_paired"))]

        couple_id = db_mod.make_couple_id(user_id, user["partner_id"])
        encryption_key = os.environ.get("DB_ENCRYPTION_KEY", "")

        # No arg → show current tier info
        if not arg or not arg.strip():
            user_key_enc = await db_mod.get_couple_config(
                self.db_path, couple_id, f"gemini_key:{user_id}"
            )
            couple_key_enc = await db_mod.get_couple_config(
                self.db_path, couple_id, "gemini_key"
            )

            def _mask(enc: str | None) -> str:
                if not enc:
                    return "not set"
                if not encryption_key:
                    return "****"
                try:
                    return mask_key(decrypt_value(enc, encryption_key))
                except Exception:
                    return "****"

            user_key_display = _mask(user_key_enc)
            couple_key_display = _mask(couple_key_enc)

            if user_key_enc:
                active_tier = "your personal key"
            elif couple_key_enc:
                active_tier = "couple key"
            else:
                active_tier = "global key"

            return [BotResponse(t(
                lang, "setup_tier_info",
                user_key=user_key_display,
                couple_key=couple_key_display,
                active_tier=active_tier,
            ))]

        parts = arg.strip().split(None, 1)
        sub_cmd = parts[0].lower()

        # Backward compat: /setup gemini_key <key> → maps to our-key
        if sub_cmd == "gemini_key":
            sub_cmd = "our-key"
            if len(parts) > 1:
                parts = ["our-key", parts[1]]
            else:
                parts = ["our-key"]

        if sub_cmd not in ("my-key", "our-key"):
            return [BotResponse(t(lang, "setup_usage"))]

        if not encryption_key:
            return [BotResponse(t(lang, "setup_no_encryption_key"))]

        config_key = (
            f"gemini_key:{user_id}" if sub_cmd == "my-key"
            else "gemini_key"
        )
        is_personal = sub_cmd == "my-key"

        # Clear operation
        if len(parts) == 2 and parts[1].strip().lower() == "clear":
            deleted = await db_mod.delete_couple_config(
                self.db_path, couple_id, config_key
            )
            if deleted:
                key_str = "setup_my_key_cleared" if is_personal else "setup_our_key_cleared"
                return [BotResponse(t(lang, key_str))]
            key_str = "setup_key_not_set"
            return [BotResponse(t(lang, key_str))]

        # Store key
        if len(parts) < 2 or not parts[1].strip():
            return [BotResponse(t(lang, "setup_usage"))]

        api_key = parts[1].strip()
        encrypted = encrypt_value(api_key, encryption_key)
        await db_mod.set_couple_config(
            self.db_path, couple_id, config_key, encrypted
        )
        masked = mask_key(api_key)
        key_str = "setup_my_key_stored" if is_personal else "setup_our_key_stored"
        return [BotResponse(t(lang, key_str, api_key=masked))]

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

    async def handle_export(
        self, user_id: int, arg: str | None = None
    ) -> list[BotResponse]:
        """Export facts or therapy report as a text file."""
        if not is_user_allowed(self.config, user_id):
            return []

        lang = await self._get_lang(user_id)

        # /export therapy → structured therapy report
        if arg and arg.strip().lower() == "therapy":
            from couples_bot.therapy_export import generate_therapy_report

            user = await db_mod.get_user(self.db_path, user_id)
            if not user or not user.get("partner_id"):
                return [BotResponse(t(lang, "not_paired"))]

            generating_msg = t(lang, "export_therapy_generating")
            report = await generate_therapy_report(
                self.db_path, self.llm, user_id,
                prompts_dir=self.config.prompts_dir,
            )
            if not report:
                return [BotResponse(t(lang, "export_therapy_empty"))]

            from datetime import datetime, timezone
            date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
            filename = f"therapy_report_{date_str}.txt"
            return [BotResponse(
                t(lang, "export_therapy_caption"),
                document=(filename, report),
            )]

        # Default: /export (facts)
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

    # ── /vibe (was /style) ───────────────────────────────────────────────

    async def handle_vibe(
        self, user_id: int, arg: str | None
    ) -> list[BotResponse]:
        """Show learned voice profile, maturity status, and allow manual trait overrides."""
        import json as _json

        if not is_user_allowed(self.config, user_id):
            return []

        lang = await self._get_lang(user_id)
        profile = await db_mod.ensure_user_profile(self.db_path, user_id)

        # /vibe set <trait> <value> — manual override
        if arg and arg.strip().lower().startswith("set "):
            parts = arg.strip().split(None, 2)
            if len(parts) < 3:
                return [BotResponse(t(lang, "vibe_set_usage"))]
            trait = parts[1].lower()
            value = parts[2].lower()
            valid_traits = {
                "register": ["casual", "formal", "mixed"],
                "sentence_length": ["short", "medium", "long"],
                "emoji_use": ["none", "light", "heavy"],
                "humor": ["none", "light", "frequent"],
                "directness": ["indirect", "balanced", "very_direct"],
            }
            if trait not in valid_traits:
                return [BotResponse(t(lang, "vibe_set_invalid_trait"))]
            if value not in valid_traits[trait]:
                allowed = ", ".join(valid_traits[trait])
                return [BotResponse(t(lang, "vibe_set_invalid_value", trait=trait, values=allowed))]
            # Load existing or create empty
            raw = profile.get("learned_voice")
            try:
                traits = _json.loads(raw) if raw else {}
            except (_json.JSONDecodeError, TypeError):
                traits = {}
            traits[trait] = value
            await db_mod.update_learned_voice(
                self.db_path, user_id, _json.dumps(traits),
            )
            return [BotResponse(t(lang, "vibe_set_success", trait=trait, value=value))]

        # Show current vibe profile
        msg_count = profile.get("message_count") or 0
        threshold = getattr(self.config, "voice_learn_after_messages", 10)
        is_mature = msg_count >= threshold

        parts_out = [t(lang, "vibe_header")]

        if is_mature:
            parts_out.append(t(lang, "vibe_mature", count=msg_count))
        else:
            parts_out.append(t(lang, "vibe_not_mature", count=msg_count, threshold=threshold))

        raw = profile.get("learned_voice")
        if raw:
            try:
                traits = _json.loads(raw)
                if isinstance(traits, dict):
                    for k, v in traits.items():
                        parts_out.append(t(lang, "vibe_trait_line", trait=k, value=v))
            except (_json.JSONDecodeError, TypeError):
                pass

        # Also show static voice info
        voice = profile.get("default_voice") or "casual_en"
        intensity = profile.get("voice_intensity", 0.5)
        voice_labels = {
            "casual_en": "Casual English",
            "telugu": "Telugu",
            "hindi": "Hindi",
            "formal_en": "Formal English",
        }
        voice_display = voice_labels.get(voice, voice)
        parts_out.append(
            "\n" + t(lang, "style_current", voice=voice_display, intensity=intensity)
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

        return [BotResponse("".join(parts_out), buttons=[voice_buttons, intensity_buttons])]

    async def handle_style(
        self, user_id: int, arg: str | None
    ) -> list[BotResponse]:
        """Backward-compatible alias for /vibe."""
        return await self.handle_vibe(user_id, arg)

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

    # ── /goal ────────────────────────────────────────────────────────────

    async def handle_goal(
        self, user_id: int, text: str | None
    ) -> list[BotResponse]:
        """Create a personal goal."""
        if not is_user_allowed(self.config, user_id):
            return []

        lang = await self._get_lang(user_id)

        if not text or not text.strip():
            return [BotResponse(t(lang, "goal_usage"))]

        text = text.strip()
        await db_mod.add_user_goal(self.db_path, user_id, text)

        # Record 'goal' event on the timeline (FEAT-004)
        user = await db_mod.get_user(self.db_path, user_id)
        if user and user.get("partner_id"):
            couple_id = db_mod.make_couple_id(user_id, user["partner_id"])
            await db_mod.add_timeline_event(
                self.db_path, couple_id, "goal", f"Personal goal: {text}",
            )

        return [BotResponse(t(lang, "goal_created", text=text))]

    # ── /together ────────────────────────────────────────────────────────

    async def handle_together(
        self, user_id: int, text: str | None
    ) -> list[BotResponse]:
        """Create a shared goal (both partners see it)."""
        if not is_user_allowed(self.config, user_id):
            return []

        lang = await self._get_lang(user_id)
        user = await db_mod.get_user(self.db_path, user_id)
        if not user or not user.get("partner_id"):
            return [BotResponse(t(lang, "together_no_partner"))]

        if not text or not text.strip():
            return [BotResponse(t(lang, "together_usage"))]

        text = text.strip()
        partner_id = user["partner_id"]
        couple_id = db_mod.make_couple_id(user_id, partner_id)
        await db_mod.add_shared_goal(self.db_path, couple_id, user_id, text)

        # Record 'goal' event on the timeline (FEAT-004)
        await db_mod.add_timeline_event(
            self.db_path, couple_id, "goal", f"Shared goal: {text}",
        )

        # Notify partner
        user_name = user.get("name") or "Your partner"
        partner = await db_mod.get_user(self.db_path, partner_id)
        plang = (partner or {}).get("lang") or "en"

        return [
            BotResponse(t(lang, "goal_shared_created", text=text)),
            BotResponse(
                t(plang, "goal_shared_notify", partner_name=user_name, text=text),
                target_user_id=partner_id,
            ),
        ]

    # ── /goals ───────────────────────────────────────────────────────────

    async def handle_goals(self, user_id: int) -> list[BotResponse]:
        """Show all active goals with achieve/drop inline buttons."""
        if not is_user_allowed(self.config, user_id):
            return []

        lang = await self._get_lang(user_id)
        user = await db_mod.get_user(self.db_path, user_id)

        personal = await db_mod.get_active_goals(self.db_path, user_id)

        shared: list[dict] = []
        if user and user.get("partner_id"):
            couple_id = db_mod.make_couple_id(user_id, user["partner_id"])
            shared = await db_mod.get_shared_goals(self.db_path, couple_id)

        if not personal and not shared:
            return [BotResponse(t(lang, "goals_empty"))]

        parts = [t(lang, "goals_header")]
        buttons: list[list[InlineButton]] = []

        if personal:
            parts.append(t(lang, "goals_personal"))
            for g in personal:
                parts.append(f"  🎯 {g['text']}\n")
                buttons.append([
                    InlineButton("✅ Achieved", f"ga:{g['id']}"),
                    InlineButton("❌ Drop", f"gd:{g['id']}"),
                ])

        if shared:
            parts.append(t(lang, "goals_shared"))
            for g in shared:
                parts.append(f"  🎯 {g['text']}\n")
                buttons.append([
                    InlineButton("✅ Achieved", f"sg_a:{g['id']}"),
                    InlineButton("❌ Drop", f"sg_d:{g['id']}"),
                ])

        return [BotResponse("".join(parts), buttons=buttons if buttons else None)]

    # ── /settings ────────────────────────────────────────────────────────

    async def handle_settings(
        self, user_id: int, arg: str | None = None
    ) -> list[BotResponse]:
        """Show an interactive settings menu with inline buttons."""
        if not is_user_allowed(self.config, user_id):
            return []

        lang = await self._get_lang(user_id)

        buttons = [
            [
                InlineButton(t(lang, "settings_voice"), "stv"),
                InlineButton(t(lang, "settings_dampening"), "std"),
            ],
            [
                InlineButton(t(lang, "settings_goals"), "stg"),
                InlineButton(t(lang, "settings_profile"), "stp"),
            ],
            [
                InlineButton(t(lang, "settings_mood"), "stm"),
            ],
        ]

        return [BotResponse(t(lang, "settings_header"), buttons=buttons)]

    # ── /remember ────────────────────────────────────────────────────────

    async def handle_remember(self, user_id: int) -> list[BotResponse]:
        """Return a random positive shared memory."""
        if not is_user_allowed(self.config, user_id):
            return []

        import random

        lang = await self._get_lang(user_id)
        user = await db_mod.get_user(self.db_path, user_id)
        if not user or not user.get("partner_id"):
            return [BotResponse(t(lang, "not_paired"))]

        partner_id = user["partner_id"]
        positive_facts = await db_mod.get_positive_shared_facts(
            self.db_path, user_id, partner_id,
        )

        if not positive_facts:
            return [BotResponse(t(lang, "remember_empty"))]

        fact = random.choice(positive_facts)
        header = t(lang, "remember_header")
        item = t(lang, "remember_item", text=fact["text"])
        return [BotResponse(f"{header}\n{item}")]

    # ── /search ──────────────────────────────────────────────────────────

    async def handle_search(
        self, user_id: int, query: str | None
    ) -> list[BotResponse]:
        """Search the user's facts using FTS5 full-text search."""
        if not is_user_allowed(self.config, user_id):
            return []

        lang = await self._get_lang(user_id)

        if not query or not query.strip():
            return [BotResponse(t(lang, "search_no_query"))]

        results = await search_facts(self.db_path, user_id, query.strip())
        if not results:
            return [BotResponse(t(lang, "search_no_results"))]

        parts = [t(lang, "search_results_header", count=len(results))]
        for fact in results:
            category = fact.get("category") or "other"
            date = format_relative_time(fact.get("created_at"))
            snippet = fact["text"][:120]
            if len(fact["text"]) > 120:
                snippet += "…"
            parts.append(t(
                lang, "search_result_item",
                id=fact["id"], snippet=snippet, date=date, category=category,
            ))

        return [BotResponse("".join(parts))]

    # ── /conflicts ───────────────────────────────────────────────────────

    async def handle_conflicts(self, user_id: int) -> list[BotResponse]:
        """Show open conflicts for the user's couple."""
        if not is_user_allowed(self.config, user_id):
            return []

        lang = await self._get_lang(user_id)
        user = await db_mod.get_user(self.db_path, user_id)
        if not user or not user.get("partner_id"):
            return [BotResponse(t(lang, "not_paired"))]

        couple_id = db_mod.make_couple_id(user_id, user["partner_id"])
        conflicts = await db_mod.get_open_conflicts(self.db_path, couple_id)

        if not conflicts:
            return [BotResponse(t(lang, "conflicts_empty"))]

        parts = [t(lang, "conflicts_header") + "\n"]
        for c in conflicts:
            duration = format_relative_time(c.get("opened_at"))
            import json
            try:
                relay_ids = json.loads(c.get("relay_ids") or "[]")
            except (json.JSONDecodeError, TypeError):
                relay_ids = []
            parts.append(t(
                lang, "conflicts_item",
                theme=c["theme"],
                duration=duration,
                relay_count=len(relay_ids),
            ))

        return [BotResponse("".join(parts))]

    # ── /usage ───────────────────────────────────────────────────────────

    async def handle_usage(
        self, user_id: int, arg: str | None
    ) -> list[BotResponse]:
        """Show LLM API usage stats — today and last 7 days."""
        if not is_user_allowed(self.config, user_id):
            return []

        lang = await self._get_lang(user_id)
        days = 7
        if arg and arg.strip().isdigit():
            days = min(int(arg.strip()), 30)

        today_total = await db_mod.get_usage_today(self.db_path)
        detailed = await db_mod.get_usage_detailed(self.db_path, days)

        parts = [f"📊 API Usage\n\nToday: {today_total} calls\n"]
        if detailed:
            parts.append(f"\nLast {days} days:\n")
            current_date = None
            for row in detailed:
                if row["date"] != current_date:
                    current_date = row["date"]
                    parts.append(f"\n{current_date}:\n")
                parts.append(f"  {row['model']}: {row['count']}\n")
        else:
            parts.append("\nNo usage recorded yet.\n")

        # Show caps
        cap = getattr(self.config, "heavy_daily_cap", 10)
        parts.append(f"\nHeavy model daily cap: {cap}")

        return [BotResponse("".join(parts))]

    # ── /timeline ────────────────────────────────────────────────────────

    async def handle_timeline(
        self, user_id: int, arg: str | None
    ) -> list[BotResponse]:
        """Show the couple's relationship timeline events."""
        if not is_user_allowed(self.config, user_id):
            return []

        lang = await self._get_lang(user_id)
        user = await db_mod.get_user(self.db_path, user_id)
        if not user or not user.get("partner_id"):
            return [BotResponse(t(lang, "not_paired"))]

        couple_id = db_mod.make_couple_id(user_id, user["partner_id"])

        _emoji = {
            "paired": "✨",
            "conflict": "⚡",
            "resolution": "✅",
            "milestone": "🏆",
            "goal": "🎯",
            "relay": "💌",
            "checkin": "🔄",
        }

        if arg and arg.strip().lower() == "month":
            # Current month filter
            now = datetime.now(timezone.utc)
            start = now.strftime("%Y-%m-01")
            # Next month start
            if now.month == 12:
                end = f"{now.year + 1}-01-01"
            else:
                end = f"{now.year}-{now.month + 1:02d}-01"

            events = await db_mod.get_timeline_events_range(
                self.db_path, couple_id, start, end,
            )
            if not events:
                return [BotResponse(t(lang, "timeline_empty"))]

            month_name = now.strftime("%B %Y")
            parts = [t(lang, "timeline_month_header", month=month_name) + "\n"]
            for ev in events:
                emoji = _emoji.get(ev["event_type"], "📌")
                try:
                    dt = datetime.fromisoformat(ev["created_at"])
                    date_str = dt.strftime("%b %d")
                except (ValueError, TypeError):
                    date_str = "?"
                parts.append(
                    t(lang, "timeline_event_item",
                      emoji=emoji, date=date_str, summary=ev["summary"])
                )
            return [BotResponse("".join(parts))]

        # Default: last 20 events
        events = await db_mod.get_timeline_events(
            self.db_path, couple_id, limit=20,
        )
        if not events:
            return [BotResponse(t(lang, "timeline_empty"))]

        parts = [t(lang, "timeline_header") + "\n"]
        for ev in reversed(events):  # chronological order
            emoji = _emoji.get(ev["event_type"], "📌")
            try:
                dt = datetime.fromisoformat(ev["created_at"])
                date_str = dt.strftime("%b %d")
            except (ValueError, TypeError):
                date_str = "?"
            parts.append(
                t(lang, "timeline_event_item",
                  emoji=emoji, date=date_str, summary=ev["summary"])
            )
        return [BotResponse("".join(parts))]
