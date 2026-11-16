"""Transport-agnostic bot handlers.

All handler methods return ``list[BotResponse]``.  The thin adapter layer
in ``__main__`` converts these into Telegram-specific API calls.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from couples_bot import db as db_mod
from couples_bot.config import Config, is_user_allowed
from couples_bot.i18n import t
from couples_bot.llm import LLMClient
from couples_bot.prompts import (
    UserContext,
    build_chat_prompt,
    build_keep_prompt,
    build_mediate_prompt,
    build_tell_prompt,
)


# ---------------------------------------------------------------------------
# Response types
# ---------------------------------------------------------------------------

@dataclass
class InlineButton:
    text: str
    callback_data: str


@dataclass
class BotResponse:
    text: str
    buttons: list[list[InlineButton]] | None = None
    document: tuple[str, str] | None = None  # (filename, text_content)
    target_user_id: int | None = None  # None → reply to sender


# ---------------------------------------------------------------------------
# Tag parsing
# ---------------------------------------------------------------------------

_TAGS = {"#keep", "#send", "#deep", "#now"}


def parse_tags(text: str) -> tuple[set[str], str]:
    """Extract known tags and return (tags, cleaned_text)."""
    found: set[str] = set()
    clean = text
    for tag in _TAGS:
        if tag in clean.lower():
            found.add(tag)
            # Remove tag (case-insensitive)
            idx = clean.lower().find(tag)
            clean = clean[:idx] + clean[idx + len(tag):]
    return found, clean.strip()


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class BotService:
    """Stateful bot logic — transport-agnostic."""

    def __init__(
        self, config: Config, db_path: str, llm: LLMClient
    ) -> None:
        self.config = config
        self.db_path = db_path
        self.llm = llm

        # Ephemeral in-memory state (lost on restart — acceptable for MVP)
        self._pairing_codes: dict[str, int] = {}   # code → user tg_id
        self._edit_state: dict[int, int] = {}       # user_id → pending_id
        self._relay_choices: dict[str, dict[str, Any]] = {}  # choice_id → data
        self._forget_confirm: set[int] = set()      # users who got the "are you sure?" prompt

    # ── helpers ──────────────────────────────────────────────────────────

    async def _get_lang(self, user_id: int) -> str:
        user = await db_mod.get_user(self.db_path, user_id)
        return (user or {}).get("lang") or "en"

    async def _build_ctx(self, user_id: int) -> UserContext:
        data = await db_mod.get_user_context_data(self.db_path, user_id)
        return UserContext(**data)

    def _approval_buttons(
        self, pending_id: int, lang: str, skip_cooldown: bool = False
    ) -> list[list[InlineButton]]:
        ap_data = f"apn:{pending_id}" if skip_cooldown else f"ap:{pending_id}"
        return [[
            InlineButton(t(lang, "btn_approve"), ap_data),
            InlineButton(t(lang, "btn_edit"), f"ed:{pending_id}"),
            InlineButton(t(lang, "btn_discard"), f"di:{pending_id}"),
        ]]

    def _choice_buttons(
        self, choice_id: str, lang: str
    ) -> list[list[InlineButton]]:
        return [[
            InlineButton(t(lang, "btn_save"), f"cs:{choice_id}"),
            InlineButton(t(lang, "btn_relay"), f"cr:{choice_id}"),
        ]]

    async def _save_facts(
        self, user_id: int, new_facts: list[dict[str, str]]
    ) -> int:
        count = 0
        for fact in new_facts:
            text = fact.get("text", "").strip()
            vis = fact.get("visibility", "private")
            if vis not in ("private", "shared"):
                vis = "private"
            if text:
                await db_mod.add_fact(
                    self.db_path, user_id, text, vis, source="llm"
                )
                count += 1
        return count

    # ── /start ───────────────────────────────────────────────────────────

    async def handle_start(
        self, user_id: int, user_name: str | None
    ) -> list[BotResponse]:
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

        # Pair them
        del self._pairing_codes[code]
        await db_mod.set_partner(self.db_path, user_id, partner_id)

        partner = await db_mod.get_user(self.db_path, partner_id)
        pname = (partner or {}).get("name") or "your partner"
        uname = user_name or user.get("name") or "your partner"

        responses: list[BotResponse] = [
            BotResponse(t(lang, "join_success", partner_name=pname)),
        ]
        # Notify partner
        plang = (partner or {}).get("lang") or "en"
        responses.append(BotResponse(
            t(plang, "join_partner_notify", partner_name=uname),
            target_user_id=partner_id,
        ))
        return responses

    # ── /tell ────────────────────────────────────────────────────────────

    async def handle_tell(
        self, user_id: int, text: str | None
    ) -> list[BotResponse]:
        if not is_user_allowed(self.config, user_id):
            return []

        lang = await self._get_lang(user_id)
        user = await db_mod.get_user(self.db_path, user_id)
        if not user or not user.get("partner_id"):
            return [BotResponse(t(lang, "tell_no_partner"))]
        if not text or not text.strip():
            return [BotResponse(t(lang, "tell_need_message"))]

        tags, clean = parse_tags(text)
        skip_cooldown = "#now" in tags

        ctx = await self._build_ctx(user_id)
        system, content = build_tell_prompt(ctx, clean)

        await db_mod.log_message(self.db_path, user_id, "user", clean)
        result = await self.llm.generate(content, system)
        await db_mod.log_message(self.db_path, user_id, "assistant", result["reply"])

        # Save any extracted facts
        await self._save_facts(user_id, result.get("new_facts", []))

        draft = result.get("relay_draft") or result["reply"]

        # Create pending
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

        # Use heavy model
        model = self._config.model_heavy[0] if self._config.model_heavy else None
        result = await self.llm.generate(content, system, model=model)

        await db_mod.log_message(self.db_path, user_id, "assistant", result["reply"])

        # Check usage warning (duck-typed — not all clients have this)
        if model and hasattr(self.llm, "check_usage_warning"):
            warning = await self.llm.check_usage_warning(model)
            if warning:
                result["reply"] += "\n\n" + warning

        return [BotResponse(result["reply"])]

    @property
    def _config(self) -> Config:
        return self.config

    # ── /whatdoyouknow ───────────────────────────────────────────────────

    async def handle_whatdoyouknow(self, user_id: int) -> list[BotResponse]:
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
            for i, f in enumerate(private_facts, 1):
                parts.append(t(lang, "fact_item", idx=i, text=f["text"], id=f["id"]))

        if shared_facts:
            parts.append(t(lang, "whatdoyouknow_shared"))
            for i, f in enumerate(shared_facts, 1):
                parts.append(t(lang, "fact_item", idx=i, text=f["text"], id=f["id"]))

        return [BotResponse("".join(parts))]

    # ── /forget ──────────────────────────────────────────────────────────

    async def handle_forget(
        self, user_id: int, arg: str | None
    ) -> list[BotResponse]:
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

        # Delete by ID
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

    # ── /help ────────────────────────────────────────────────────────────

    async def handle_help(self, user_id: int) -> list[BotResponse]:
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
        if not is_user_allowed(self.config, user_id):
            return []

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
        if not is_user_allowed(self.config, user_id):
            return []

        await db_mod.ensure_user(self.db_path, user_id, user_name)

        # Clear any stale forget-confirm state on new message
        self._forget_confirm.discard(user_id)

        # Check if user is in edit-pending state
        if user_id in self._edit_state:
            return await self._handle_edit_input(user_id, text)

        tags, clean = parse_tags(text)

        if "#keep" in tags:
            return await self._handle_keep(user_id, clean)
        if "#send" in tags:
            return await self._handle_send(user_id, clean, "#now" in tags)
        if "#deep" in tags:
            return await self._handle_deep(user_id, clean)

        # No tag → regular chat, then ask
        return await self._handle_plain(user_id, clean)

    # ── callback queries (inline buttons) ────────────────────────────────

    async def handle_callback(
        self, user_id: int, callback_data: str
    ) -> list[BotResponse]:
        if not is_user_allowed(self.config, user_id):
            return []

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

        return [BotResponse(t(lang, "error_generic"))]

    # ── internal: tag handlers ───────────────────────────────────────────

    async def _handle_keep(
        self, user_id: int, text: str
    ) -> list[BotResponse]:
        lang = await self._get_lang(user_id)
        ctx = await self._build_ctx(user_id)
        system, content = build_keep_prompt(ctx, text)

        await db_mod.log_message(self.db_path, user_id, "user", text)
        result = await self.llm.generate(content, system)
        await db_mod.log_message(self.db_path, user_id, "assistant", result["reply"])

        count = await self._save_facts(user_id, result.get("new_facts", []))

        reply = result["reply"]
        if count:
            reply += "\n\n" + t(lang, "fact_saved", count=count)
        return [BotResponse(reply)]

    async def _handle_send(
        self, user_id: int, text: str, skip_cooldown: bool
    ) -> list[BotResponse]:
        lang = await self._get_lang(user_id)
        user = await db_mod.get_user(self.db_path, user_id)
        if not user or not user.get("partner_id"):
            return [BotResponse(t(lang, "not_paired"))]

        ctx = await self._build_ctx(user_id)
        system, content = build_tell_prompt(ctx, text)

        await db_mod.log_message(self.db_path, user_id, "user", text)
        result = await self.llm.generate(content, system)
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

    async def _handle_deep(
        self, user_id: int, text: str
    ) -> list[BotResponse]:
        """Chat using the heavy model."""
        lang = await self._get_lang(user_id)
        ctx = await self._build_ctx(user_id)
        system, content = build_chat_prompt(ctx, text)

        model = self._config.model_heavy[0] if self._config.model_heavy else None
        await db_mod.log_message(self.db_path, user_id, "user", text)
        result = await self.llm.generate(content, system, model=model)
        await db_mod.log_message(self.db_path, user_id, "assistant", result["reply"])

        # Check usage warning (duck-typed — not all clients have this)
        if model and hasattr(self.llm, "check_usage_warning"):
            warning = await self.llm.check_usage_warning(model)
            if warning:
                result["reply"] += "\n\n" + warning

        count = await self._save_facts(user_id, result.get("new_facts", []))

        replies: list[BotResponse] = [BotResponse(result["reply"])]
        if count:
            replies[0].text += "\n\n" + t(lang, "fact_saved", count=count)
        return replies

    async def _handle_plain(
        self, user_id: int, text: str
    ) -> list[BotResponse]:
        lang = await self._get_lang(user_id)
        ctx = await self._build_ctx(user_id)
        system, content = build_chat_prompt(ctx, text)

        await db_mod.log_message(self.db_path, user_id, "user", text)
        result = await self.llm.generate(content, system)
        await db_mod.log_message(self.db_path, user_id, "assistant", result["reply"])

        count = await self._save_facts(user_id, result.get("new_facts", []))

        reply_text = result["reply"]
        if count:
            reply_text += "\n\n" + t(lang, "fact_saved", count=count)

        # If user is paired, ask what to do
        user = await db_mod.get_user(self.db_path, user_id)
        if user and user.get("partner_id") and result.get("relay_draft"):
            choice_id = uuid.uuid4().hex[:8]
            self._relay_choices[choice_id] = {
                "user_id": user_id,
                "relay_draft": result["relay_draft"],
            }
            buttons = self._choice_buttons(choice_id, lang)
            return [BotResponse(
                reply_text + "\n\n" + t(lang, "ask_action"),
                buttons=buttons,
            )]

        return [BotResponse(reply_text)]

    # ── internal: callback handlers ──────────────────────────────────────

    async def _cb_approve(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        skip_cooldown = data.startswith("apn:")
        pending_id = int(data.split(":")[1])

        # Verify sender owns this pending row
        pending = await db_mod.get_pending(self.db_path, pending_id)
        if not pending or pending["sender_id"] != user_id:
            return [BotResponse(t(lang, "error_generic"))]

        if skip_cooldown:
            send_after = datetime.now(timezone.utc).isoformat()
            msg = t(lang, "approved_now")
        else:
            send_after = (
                datetime.now(timezone.utc)
                + timedelta(minutes=self.config.cooldown_minutes)
            ).isoformat()
            msg = t(lang, "approved", minutes=self.config.cooldown_minutes)

        await db_mod.update_pending_status(
            self.db_path, pending_id, "approved", send_after
        )
        return [BotResponse(msg)]

    async def _cb_edit(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        pending_id = int(data.split(":")[1])

        # Verify sender owns this pending row
        pending = await db_mod.get_pending(self.db_path, pending_id)
        if not pending or pending["sender_id"] != user_id:
            return [BotResponse(t(lang, "error_generic"))]

        self._edit_state[user_id] = pending_id
        return [BotResponse(t(lang, "edit_prompt"))]

    async def _cb_discard(
        self, user_id: int, data: str, lang: str
    ) -> list[BotResponse]:
        pending_id = int(data.split(":")[1])

        # Verify sender owns this pending row
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
        choice_id = data.split(":")[1]
        self._relay_choices.pop(choice_id, None)
        return [BotResponse(t(lang, "save_done"))]

    # ── internal: edit flow ──────────────────────────────────────────────

    async def _handle_edit_input(
        self, user_id: int, text: str
    ) -> list[BotResponse]:
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
