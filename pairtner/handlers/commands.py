"""Slash-command handler methods for BotService.

These methods are mixed into :class:`~pairtner.handlers.service.BotService`
via the :class:`CommandsMixin` base class.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

from pairtner import db as db_mod
from pairtner.config import is_user_allowed
from pairtner.crypto import decrypt_value, encrypt_value, mask_key
from pairtner.i18n import t
from pairtner.core.search_v2 import paginated_search

import re

import aiosqlite

from pairtner.storage.schema_v2 import make_relationship_id

from .models import BotResponse, InlineButton, format_relative_time

_MAX_SHARE_BUTTONS = 30

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


SEARCH_PAGE_SIZE = 5
_PAGE_ARG_RE = re.compile(r"^(.*?)\s+page\s+(\d+)\s*$", re.I | re.S)


def _split_page_arg(arg: str) -> tuple[str, int]:
    """Split ``"<query> page N"`` into (query, N); N defaults to 1."""
    m = _PAGE_ARG_RE.match(arg)
    if m and m.group(1).strip():
        return m.group(1).strip(), max(1, int(m.group(2)))
    return arg, 1


logger = logging.getLogger(__name__)

_PREF_RE = re.compile(
    r"prefer|style|tone|direct|gentle|soft|blunt|casual|formal|humor|emoji|"
    r"short|brief|detail|nudge|remind|quiet|call me|speak|language", re.I,
)


class CommandsMixin:
    """Mixin providing slash-command handlers for BotService."""

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

    @staticmethod
    def _append_fact_expiry(parts: list[str], f: dict, lang: str) -> None:
        """Append expiry warning if a fact is approaching expiration."""
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
                        parts[-1] = parts[-1].rstrip("\n") + f" {t(lang, 'fact_expiring', time=f'{days}d')}\n"
                    else:
                        hours = int(remaining.total_seconds() // 3600)
                        parts[-1] = parts[-1].rstrip("\n") + f" {t(lang, 'fact_expiring', time=f'{hours}h')}\n"
            except (ValueError, TypeError):
                pass

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

        from pairtner.core.dampening import resolve_dampening as _resolve
        damp_level, _ = await _resolve(
            self.db_path, user_id, self.config, has_blunt_tag=False,
        )
        parts.append(t(lang, "status_dampening", level=damp_level))

        # Mode and yolo status
        mode = await db_mod.get_user_preference(self.db_path, user_id, "mode", default="mediator") or "mediator"
        relay_mode = await db_mod.get_user_preference(self.db_path, user_id, "relay_mode", default="review") or "review"
        mode_icon = "🗣️" if mode == "voice" else "🧑‍⚖️"
        yolo_icon = "🟢" if relay_mode == "yolo" else "🔴"
        parts.append(f"\n{mode_icon} Mode: {mode}  |  🚀 Yolo: {yolo_icon}\n")

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

    # ── /password ────────────────────────────────────────────────────────

    async def handle_password(
        self, user_id: int, arg: str | None
    ) -> list[BotResponse]:
        """Set the password used to log in to the companion app."""
        if not is_user_allowed(self.config, user_id):
            return []
        user = await db_mod.ensure_user(self.db_path, user_id)
        lang = user.get("lang") or "en"
        value = (arg or "").strip()
        if not value:
            return [BotResponse(t(lang, "password_usage"))]
        await db_mod.set_password(self.db_path, user_id, value)
        return [BotResponse(t(lang, "password_set"))]

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

    # ── /share and /private (bulk visibility by tag) ─────────────────────

    @staticmethod
    def _facts_with_tag(facts: list[dict], tag: str) -> list[dict]:
        from pairtner.storage.tagfmt import parse_fact_tags
        if tag == "all":
            return list(facts)
        return [f for f in facts if tag in parse_fact_tags(f.get("tag"))]

    async def handle_share(
        self, user_id: int, arg: str | None
    ) -> list[BotResponse]:
        """Bulk-flip private facts to shared by tag (``/share <tag>|all``).

        The first call lists what would be shared; repeating the same command
        confirms. A catch-up summary fact is created for the partner context.
        """
        from pairtner.storage import facts as facts_mod

        if not is_user_allowed(self.config, user_id):
            return []
        lang = await self._get_lang(user_id)
        tag = (arg or "").strip().lower().lstrip("#")
        if not tag:
            return [BotResponse(t(lang, "share_usage"))]

        pending = self._visibility_confirm
        private = await db_mod.get_active_facts(self.db_path, user_id, "private")
        matched = self._facts_with_tag(private, tag)
        if not matched:
            pending.pop(user_id, None)
            return [BotResponse(t(lang, "share_none", tag=tag))]

        key = ("share", tag)
        if pending.get(user_id) != key:
            pending[user_id] = key
            lines = "".join(f"  • {f['text']}\n" for f in matched)
            return [BotResponse(t(
                lang, "share_confirm", tag=tag, count=len(matched), facts=lines))]
        pending.pop(user_id, None)

        texts = [f["text"] for f in matched]
        count = 0
        for f in matched:
            if await facts_mod.set_fact_visibility(
                self.db_path, f["id"], user_id, "shared"
            ):
                count += 1
        if tag == "all":
            for pt in await facts_mod.get_private_tags(self.db_path, user_id):
                await facts_mod.remove_private_tag(self.db_path, user_id, pt)
        else:
            await facts_mod.remove_private_tag(self.db_path, user_id, tag)

        summary = await self._summarize_for_partner(user_id, texts)
        await facts_mod.add_fact(
            self.db_path, user_id, summary, visibility="shared",
            tag=None if tag == "all" else tag, kind="working",
            relationship_id=await self._relationship_id_for(user_id),
        )
        return [BotResponse(t(lang, "share_done", count=count, tag=tag))]

    async def handle_private(
        self, user_id: int, arg: str | None
    ) -> list[BotResponse]:
        """Bulk-flip shared facts to private by tag (``/private <tag>|all``)."""
        from pairtner.storage import facts as facts_mod
        from pairtner.storage.tagfmt import parse_fact_tags

        if not is_user_allowed(self.config, user_id):
            return []
        lang = await self._get_lang(user_id)
        tag = (arg or "").strip().lower().lstrip("#")
        if not tag:
            return [BotResponse(t(lang, "private_usage"))]

        shared = await db_mod.get_active_facts(self.db_path, user_id, "shared")
        matched = self._facts_with_tag(shared, tag)
        if not matched:
            return [BotResponse(t(lang, "private_none", tag=tag))]

        count = 0
        for f in matched:
            if await facts_mod.set_fact_visibility(
                self.db_path, f["id"], user_id, "private"
            ):
                count += 1
        if tag == "all":
            tags = {x for f in matched for x in parse_fact_tags(f.get("tag"))}
        else:
            tags = {tag}
        for tg in sorted(tags):
            await facts_mod.add_private_tag(self.db_path, user_id, tg)

        await facts_mod.add_fact(
            self.db_path, user_id,
            f"User marked [{tag}] as private. Exclude from partner context.",
            visibility="private", tag=None if tag == "all" else tag,
            kind="working",
            relationship_id=await self._relationship_id_for(user_id),
        )
        return [BotResponse(t(lang, "private_done", count=count, tag=tag))]

    async def _relationship_id_for(self, user_id: int) -> str | None:
        user = await db_mod.get_user(self.db_path, user_id)
        if user and user.get("partner_id"):
            return make_relationship_id(user_id, user["partner_id"])
        return None

    async def _summarize_for_partner(self, user_id: int, texts: list[str]) -> str:
        """LLM catch-up summary of newly shared facts; falls back to a join."""
        joined = "; ".join(texts)
        system = (
            "You write one concise catch-up entry for a partner's context. "
            "Reply with JSON containing the entry in the 'reply' field."
        )
        content = (
            "Summarize these facts into one catch-up entry for the partner "
            f"context: {joined}"
        )
        api_key = await self._get_couple_api_key(user_id)
        result = await self.llm.generate(content, system, api_key_override=api_key)
        reply = (result.get("reply") or "").strip()
        return reply or joined

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
            from pairtner.features.therapy_export import build_therapy_notes

            user = await db_mod.get_user(self.db_path, user_id)
            if not user or not user.get("partner_id"):
                return [BotResponse(t(lang, "not_paired"))]

            report = await build_therapy_notes(self.db_path, user_id)
            if not report:
                return [BotResponse(t(lang, "export_therapy_empty"))]

            from datetime import datetime, timezone
            date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
            filename = f"therapy_report_{date_str}.md"
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

    # ── /goal ────────────────────────────────────────────────────────────

    async def handle_goal(
        self, user_id: int, text: str | None
    ) -> list[BotResponse]:
        """Unified goal command: /goal [add|shared|list|<text>].

        - /goal                → list all goals (personal + shared)
        - /goal list           → same as above
        - /goal <text>         → create a personal goal
        - /goal add <text>     → create a personal goal
        - /goal shared <text>  → create a shared goal (old /together)
        """
        if not is_user_allowed(self.config, user_id):
            return []

        sub = (text or "").strip()
        low = sub.lower()

        # /goal or /goal list → list goals
        if not sub or low == "list":
            return await self._list_goals(user_id)

        # /goal shared <text> → shared goal
        if low.startswith("shared "):
            shared_text = sub[7:].strip()
            if shared_text:
                return await self._create_shared_goal(user_id, shared_text)
            lang = await self._get_lang(user_id)
            return [BotResponse(t(lang, "together_usage"))]

        # /goal add <text> → personal goal (strip "add" prefix)
        if low.startswith("add "):
            sub = sub[4:].strip()

        # Create personal goal
        return await self._create_personal_goal(user_id, sub)

    async def _create_personal_goal(
        self, user_id: int, text: str
    ) -> list[BotResponse]:
        """Create a personal goal."""
        lang = await self._get_lang(user_id)
        if not text:
            return [BotResponse(t(lang, "goal_usage"))]

        await db_mod.add_user_goal(self.db_path, user_id, text)

        user = await db_mod.get_user(self.db_path, user_id)
        if user and user.get("partner_id"):
            couple_id = db_mod.make_couple_id(user_id, user["partner_id"])
            await db_mod.add_timeline_event(
                self.db_path, couple_id, "goal", f"Personal goal: {text}",
            )

        return [BotResponse(t(lang, "goal_created", text=text))]

    async def _create_shared_goal(
        self, user_id: int, text: str
    ) -> list[BotResponse]:
        """Create a shared goal (both partners see it)."""
        lang = await self._get_lang(user_id)
        user = await db_mod.get_user(self.db_path, user_id)
        if not user or not user.get("partner_id"):
            return [BotResponse(t(lang, "together_no_partner"))]

        partner_id = user["partner_id"]
        couple_id = db_mod.make_couple_id(user_id, partner_id)
        await db_mod.add_shared_goal(self.db_path, couple_id, user_id, text)

        await db_mod.add_timeline_event(
            self.db_path, couple_id, "goal", f"Shared goal: {text}",
        )

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

    # ── /goal list (internal) ────────────────────────────────────────────

    async def _list_goals(self, user_id: int) -> list[BotResponse]:
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
        """Show a plain-text summary of current preferences (no buttons)."""
        if not is_user_allowed(self.config, user_id):
            return []

        lang = await self._get_lang(user_id)
        profile = await db_mod.ensure_user_profile(self.db_path, user_id)

        lines = [t(lang, "settings_header")]

        # Preferences remembered as core facts
        try:
            facts = await db_mod.get_facts(self.db_path, user_id)
        except Exception:  # noqa: BLE001 - summary must never break /settings
            logger.warning("settings: could not load facts", exc_info=True)
            facts = []
        prefs = [
            f for f in facts
            if f.get("kind") == "core" and _PREF_RE.search(f.get("text") or "")
        ]
        if prefs:
            lines.append("\n<b>" + t(lang, "settings_prefs_title") + "</b>")
            lines.extend(f"• {f['text']}" for f in prefs[:10])

        # Current tuning
        relay_mode = await db_mod.get_user_preference(
            self.db_path, user_id, "relay_mode", default="review",
        )
        level = profile.get("dampening_base")
        lines.append("\n<b>" + t(lang, "settings_current_title") + "</b>")
        lines.append(f"• relay: {relay_mode}")
        if level is not None:
            lines.append(f"• softening: {level:.1f}")
        user_model = await self._get_user_model(user_id)
        lines.append(
            f"• model: {user_model or self._default_model_name() + ' (default)'}"
        )
        from pairtner.core.memory import SETTINGS_META_PREFIX

        nudge_vals = {}
        for key in (
            "nudge_frequency", "nudge_quiet_start", "nudge_quiet_end",
            "nudge_skip_weekends",
        ):
            nudge_vals[key] = await db_mod.get_user_preference(
                self.db_path, user_id, SETTINGS_META_PREFIX + key, default=None,
            )
        lines.append(f"• nudge_frequency: {nudge_vals['nudge_frequency'] or 'daily'}")
        start, end = nudge_vals["nudge_quiet_start"], nudge_vals["nudge_quiet_end"]
        if start and end:
            lines.append(f"• nudge quiet hours: {start}–{end}")
        if nudge_vals["nudge_skip_weekends"] == "true":
            lines.append("• nudges: skipped on weekends")

        lines.append("\n" + t(lang, "settings_footer"))
        return [BotResponse("\n".join(lines))]

    # ── /mode ──────────────────────────────────────────────────────────

    async def handle_mode(self, user_id: int, arg: str | None) -> list[BotResponse]:
        """Show/switch mode.

        ``/mode solo|couple`` switches the account between personal-journal and
        couple use. ``/mode mediator|voice`` sets the relay drafting style.
        """
        if not is_user_allowed(self.config, user_id):
            return []

        await db_mod.ensure_user(
            self.db_path, user_id, mode=self._default_mode,
        )
        choice = (arg or "").strip().lower()

        if choice in ("solo", "couple"):
            await db_mod.set_user_mode(self.db_path, user_id, choice)
            if choice == "solo":
                return [BotResponse(
                    "🧘 Switched to <b>solo</b> mode. I'm now your personal "
                    "journal and coach. Nudges, relays and partner insights are "
                    "off. Switch back with /mode couple."
                )]
            return [BotResponse(
                "💞 Switched to <b>couple</b> mode. Partner features "
                "(relay, nudges, mediation) are back on."
            )]

        if choice not in ("mediator", "voice"):
            current = await db_mod.get_user_preference(
                self.db_path, user_id, "mode", default="mediator",
            )
            account = await db_mod.get_user_mode(self.db_path, user_id)
            return [BotResponse(
                f"🎭 Account mode: <b>{account}</b> · relay style: <b>{current}</b>\n\n"
                "<b>solo</b> — personal journal/coach, no partner features\n"
                "<b>couple</b> — paired features on\n"
                "<b>mediator</b> — relays written as a neutral third party\n"
                "<b>voice</b> — relays written as you, in first person\n\n"
                "Switch with: /mode solo, /mode couple, /mode mediator or /mode voice"
            )]

        mode = choice
        await db_mod.set_user_preference(self.db_path, user_id, "mode", mode)

        if mode == "voice":
            return [BotResponse(
                "🎭 Switched to <b>voice</b> mode.\n"
                "Relay drafts will now be written in first person, "
                "as if coming directly from you."
            )]
        return [BotResponse(
            "🎭 Switched to <b>mediator</b> mode.\n"
            "I'll speak as a neutral third party when drafting relays."
        )]

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

        query, page = _split_page_arg(query.strip())
        if not query:
            return [BotResponse(t(lang, "search_no_query"))]

        relationship_id = await self._search_relationship_id(user_id)
        data = await paginated_search(
            self.db_path, user_id, query, relationship_id,
            page=page, per_page=SEARCH_PAGE_SIZE,
        )
        results = data["results"]
        if not results:
            return [BotResponse(t(lang, "search_no_results"))]

        total = data["total"]
        pages = max(1, -(-total // SEARCH_PAGE_SIZE))
        parts = [t(lang, "search_results_header_v2", total=total)]
        for fact in results:
            snippet = fact["text"][:120]
            if len(fact["text"]) > 120:
                snippet += "…"
            badge = "🔒 private" if fact["visibility"] == "private" else "👥 shared"
            parts.append(t(
                lang, "search_result_item_v2",
                id=fact["id"], snippet=snippet, kind=fact["kind"], badge=badge,
            ))
        if data["has_more"]:
            parts.append(t(
                lang, "search_page_footer",
                page=data["page"], pages=pages, query=query, next=data["page"] + 1,
            ))
        elif pages > 1:
            parts.append(t(lang, "search_page_footer_last", page=data["page"], pages=pages))

        return [BotResponse("".join(parts))]

    async def _search_relationship_id(self, user_id: int) -> str | None:
        """Relationship id for a paired v2 user, else None (v1 scoping)."""
        user = await db_mod.get_user(self.db_path, user_id)
        partner_id = (user or {}).get("partner_id")
        if not partner_id:
            return None
        rel_id = make_relationship_id(user_id, partner_id)
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT 1 FROM relationships WHERE id = ?", (rel_id,)
            ) as cur:
                return rel_id if await cur.fetchone() else None

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

    # ── /topics ──────────────────────────────────────────────────────────

    async def handle_topics(self, user_id: int) -> list[BotResponse]:
        """List the user's topics (fact tags) by recency."""
        if not is_user_allowed(self.config, user_id):
            return []
        rows = await db_mod.get_topic_index(self.db_path, user_id)
        if not rows:
            return [BotResponse("No topics yet. Keep chatting and I'll build your index.")]
        lines = ["🗂 Your topics:\n"]
        for r in rows:
            last = str(r["last_discussed"] or "")[:10]
            priv = f", {r['private_count']} private" if r["private_count"] else ""
            lines.append(
                f"• {r['topic']} — {r['mention_count']} mention(s){priv}, last {last}"
            )
        return [BotResponse("\n".join(lines))]

    # ── /debug (no args) and /warnings ───────────────────────────────────

    async def handle_memory_debug(self, user_id: int) -> list[BotResponse]:
        """Memory state: fact counts, date span, FTS5, summaries, pack, LLM."""
        if not is_user_allowed(self.config, user_id):
            return []
        from pairtner import pack as pack_mod
        from pairtner.core.diagnostics import collect_debug, format_debug

        data = await collect_debug(self.db_path, user_id)
        p = pack_mod.current()
        cfg = self.config
        provider = getattr(cfg, "llm_provider", "") or "gemini"
        model = getattr(cfg, "llm_model_default", "") or getattr(cfg, "model_default", "")
        return [BotResponse(format_debug(
            data, pack_name=p.name, pack_version=p.version,
            provider=provider, model=model,
        ))]

    async def handle_warnings(self, user_id: int) -> list[BotResponse]:
        """Memory quality issues: contradictions, duplicates, stale, orphans, gaps."""
        if not is_user_allowed(self.config, user_id):
            return []
        from pairtner.core.diagnostics import collect_warnings, format_warnings

        data = await collect_warnings(self.db_path, user_id)
        return [BotResponse(format_warnings(data))]

    # ── /rewrite ─────────────────────────────────────────────────────────

    async def handle_rewrite(
        self, user_id: int, arg: str | None = None
    ) -> list[BotResponse]:
        """Rewrite working facts via LLM for clarity and conciseness.

        /rewrite       — rewrite all working facts (up to 20)
        /rewrite <id>  — rewrite a single fact by ID
        """
        if not is_user_allowed(self.config, user_id):
            return []

        REWRITE_SYSTEM = (
            "You are a fact editor. Rewrite each fact to be clear, concise, "
            "and in third person. Keep the meaning identical. Remove filler. "
            "Respond with ONLY valid JSON: {\"rewrites\": [{\"id\": N, \"text\": \"improved text\"}]}"
        )

        facts = await db_mod.get_facts(self.db_path, user_id)
        working = [f for f in facts if f.get("kind") == "working" and f.get("status") == "active"]

        if arg and arg.strip().isdigit():
            target_id = int(arg.strip())
            working = [f for f in working if f["id"] == target_id]
            if not working:
                return [BotResponse("No working fact found with that ID.")]

        if not working:
            return [BotResponse("No working facts to rewrite.")]

        batch = working[:20]
        lines = [f"[{f['id']}] {f['text']}" for f in batch]
        user_msg = "Rewrite these facts:\n" + "\n".join(lines)

        try:
            result = await self.llm.generate(user_msg, REWRITE_SYSTEM)
            import json
            reply = result.get("reply", "")
            try:
                data = json.loads(reply)
            except (json.JSONDecodeError, TypeError):
                data = result
            rewrites = data.get("rewrites", [])
            if not rewrites:
                return [BotResponse("LLM returned no rewrites. Facts unchanged.")]

            updated = 0
            details = []
            valid_ids = {f["id"] for f in batch}
            for rw in rewrites:
                fid = rw.get("id")
                new_text = (rw.get("text") or "").strip()
                if fid in valid_ids and new_text:
                    await db_mod.update_fact_text(self.db_path, fid, new_text)
                    old = next((f["text"] for f in batch if f["id"] == fid), "?")
                    details.append(f"#{fid}: {old} → {new_text}")
                    updated += 1

            if not updated:
                return [BotResponse("No facts were rewritten (LLM output didn't match).")]

            msg = f"✏️ Rewrote {updated} fact{'s' if updated != 1 else ''}:\n\n"
            msg += "\n".join(details[:10])
            if len(details) > 10:
                msg += f"\n...and {len(details) - 10} more."
            return [BotResponse(msg)]

        except Exception:
            import logging
            logging.getLogger(__name__).exception("rewrite failed")
            return [BotResponse("Could not rewrite facts right now. Try again later.")]
