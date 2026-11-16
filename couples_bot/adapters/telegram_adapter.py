"""Telegram adapter — extracted from __main__.py with zero behavior changes."""

from __future__ import annotations

import asyncio
import logging
from io import BytesIO

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from couples_bot.config import is_user_allowed
from couples_bot.handlers import BotResponse

from .base import BaseAdapter

logger = logging.getLogger(__name__)


class TelegramAdapter(BaseAdapter):
    """Adapter that bridges BotService to the python-telegram-bot library."""

    def __init__(self, service, config) -> None:
        super().__init__(service, config)
        self._app: Application | None = None
        self._stop_event: asyncio.Event | None = None

    # ── public interface ─────────────────────────────────────────────────

    async def start(self) -> None:
        """Build the Telegram Application, register handlers, start polling.

        Uses the async PTB API (initialize / start / start_polling) so the
        adapter cooperates with the caller's event loop.  This allows
        ``asyncio.gather()`` to run Telegram alongside other adapters.
        """
        app = (
            Application.builder()
            .token(self.config.telegram_token)
            .post_init(self._post_init)
            .build()
        )
        self._app = app

        # Register handlers (order matters — commands before generic message)
        app.add_handler(CommandHandler("start", self._cmd_start))
        app.add_handler(CommandHandler("join", self._cmd_join))
        app.add_handler(CommandHandler("tell", self._cmd_tell))
        app.add_handler(CommandHandler("mediate", self._cmd_mediate))
        app.add_handler(CommandHandler("whatdoyouknow", self._cmd_whatdoyouknow))
        app.add_handler(CommandHandler("forget", self._cmd_forget))
        app.add_handler(CommandHandler("export", self._cmd_export))
        app.add_handler(CommandHandler("lang", self._cmd_lang))
        app.add_handler(CommandHandler("setup", self._cmd_setup))
        app.add_handler(CommandHandler("status", self._cmd_status))
        app.add_handler(CommandHandler("history", self._cmd_history))
        app.add_handler(CommandHandler("pause", self._cmd_pause))
        app.add_handler(CommandHandler("resume", self._cmd_resume))
        app.add_handler(CommandHandler("style", self._cmd_style))
        app.add_handler(CommandHandler("vibe", self._cmd_vibe))
        app.add_handler(CommandHandler("mood", self._cmd_mood))
        app.add_handler(CommandHandler("goal", self._cmd_goal))
        app.add_handler(CommandHandler("together", self._cmd_together))
        app.add_handler(CommandHandler("goals", self._cmd_goals))
        app.add_handler(CommandHandler("settings", self._cmd_settings))
        app.add_handler(CommandHandler("skip", self._cmd_skip))
        app.add_handler(CommandHandler("remember", self._cmd_remember))
        app.add_handler(CommandHandler("search", self._cmd_search))
        app.add_handler(CommandHandler("conflicts", self._cmd_conflicts))
        app.add_handler(CommandHandler("timeline", self._cmd_timeline))
        app.add_handler(CommandHandler("help", self._cmd_help))
        app.add_handler(CallbackQueryHandler(self._on_callback))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message))

        logger.info("pAIrtner starting long-polling…")

        # Async PTB lifecycle — no run_polling(), no nested event loops
        await app.initialize()
        await app.start()
        await app.updater.start_polling()

        self._stop_event = asyncio.Event()
        try:
            await self._stop_event.wait()
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()

    async def stop(self) -> None:
        await self._cancel_background_tasks()
        if self._stop_event:
            self._stop_event.set()

    async def send_message(
        self,
        user_id: int | str,
        text: str,
        buttons: list[list[tuple[str, str]]] | None = None,
        document: tuple[str, str] | None = None,
    ) -> None:
        if self._app is None:
            return
        markup = None
        if buttons:
            rows = [
                [InlineKeyboardButton(btn_text, callback_data=btn_data)
                 for btn_text, btn_data in row]
                for row in buttons
            ]
            markup = InlineKeyboardMarkup(rows)

        if document:
            fname, content = document
            doc = BytesIO(content.encode("utf-8"))
            doc.name = fname
            await self._app.bot.send_document(
                chat_id=int(user_id), document=doc, caption=text[:1024],
            )
        else:
            await self._app.bot.send_message(
                chat_id=int(user_id),
                text=text,
                reply_markup=markup,
                parse_mode="HTML",
            )

    async def send_typing(self, user_id: int | str) -> None:
        if self._app:
            await self._app.bot.send_chat_action(
                chat_id=int(user_id), action=ChatAction.TYPING,
            )

    # ── response conversion ──────────────────────────────────────────────

    def _build_markup(
        self, response: BotResponse
    ) -> InlineKeyboardMarkup | None:
        if not response.buttons:
            return None
        rows = [
            [InlineKeyboardButton(b.text, callback_data=b.callback_data) for b in row]
            for row in response.buttons
        ]
        return InlineKeyboardMarkup(rows)

    async def _send_response(
        self, bot, chat_id: int, response: BotResponse
    ) -> None:
        target = response.target_user_id or chat_id
        markup = self._build_markup(response)

        if response.document:
            fname, content = response.document
            doc = BytesIO(content.encode("utf-8"))
            doc.name = fname
            await bot.send_document(
                chat_id=target, document=doc, caption=response.text[:1024],
            )
        else:
            await bot.send_message(
                chat_id=target,
                text=response.text,
                reply_markup=markup,
                parse_mode="HTML",
            )

    # ── background tasks ─────────────────────────────────────────────────

    async def _post_init(self, app: Application) -> None:
        """Start background tasks after the bot application is initialized."""
        # Use the Telegram Application's create_task for proper lifecycle
        async def _send(user_id: int, text: str) -> None:
            await app.bot.send_message(
                chat_id=user_id, text=text, parse_mode="HTML",
            )

        async def _send_with_buttons(
            user_id: int, text: str, buttons: list[list[tuple[str, str]]]
        ) -> None:
            rows = [
                [InlineKeyboardButton(btn_text, callback_data=btn_data)
                 for btn_text, btn_data in row]
                for row in buttons
            ]
            markup = InlineKeyboardMarkup(rows)
            await app.bot.send_message(
                chat_id=user_id, text=text, reply_markup=markup,
                parse_mode="HTML",
            )

        from couples_bot.approval import delivery_loop
        from couples_bot.backup import backup_loop
        from couples_bot.checkin import checkin_loop
        from couples_bot.digest import digest_loop
        from couples_bot.nudges import nudge_loop

        app.create_task(delivery_loop(
            self.service.db_path, _send,
            send_buttons_func=_send_with_buttons,
            poll_interval=self.config.delivery_check_interval_seconds,
            review_before_send=self.config.review_before_send,
        ))
        logger.info("Delivery loop started")

        if self.config.backup_enabled:
            app.create_task(backup_loop(
                self.config.db_path,
                self.config.backup_directory,
                daily_count=self.config.backup_daily_count,
                weekly_count=self.config.backup_weekly_count,
            ))
            logger.info("Backup loop started")

        if self.config.digest_enabled:
            app.create_task(digest_loop(
                self.service.db_path,
                self.service.llm,
                _send,
                digest_time=self.config.digest_time,
                digest_tz=self.config.digest_timezone,
            ))
            logger.info("Digest loop started")

        if self.config.checkin_enabled:
            app.create_task(checkin_loop(
                self.service.db_path,
                self.service.llm,
                _send,
                checkin_day=self.config.checkin_day,
                checkin_time=self.config.checkin_time,
                checkin_tz=self.config.checkin_timezone,
                checkin_questions=self.config.checkin_questions,
            ))
            logger.info("Check-in loop started")

        if self.config.nudges_enabled or self.config.appreciation_enabled:
            app.create_task(nudge_loop(
                self.service.db_path,
                self.service.llm,
                _send,
                config=self.config,
                poll_interval=self.config.nudges_interval_hours * 3600,
            ))
            logger.info("Nudge loop started")

    # ── command adapters ─────────────────────────────────────────────────

    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        responses = await self.service.handle_start(user.id, user.first_name)
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _cmd_join(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        args = ctx.args
        code = args[0] if args else None
        responses = await self.service.handle_join(user.id, user.first_name, code)
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _cmd_tell(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        text = " ".join(ctx.args) if ctx.args else None
        await update.effective_chat.send_action(ChatAction.TYPING)
        responses = await self.service.handle_tell(user.id, text)
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _cmd_mediate(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        await update.effective_chat.send_action(ChatAction.TYPING)
        responses = await self.service.handle_mediate(user.id)
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _cmd_whatdoyouknow(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        responses = await self.service.handle_whatdoyouknow(user.id)
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _cmd_forget(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        arg = " ".join(ctx.args) if ctx.args else None
        responses = await self.service.handle_forget(user.id, arg)
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _cmd_export(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        arg = " ".join(ctx.args) if ctx.args else None
        responses = await self.service.handle_export(user.id, arg)
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        responses = await self.service.handle_help(user.id)
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _cmd_lang(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        arg = ctx.args[0] if ctx.args else None
        responses = await self.service.handle_lang(user.id, arg)
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _cmd_setup(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        arg = " ".join(ctx.args) if ctx.args else None
        responses = await self.service.handle_setup(user.id, arg)
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        responses = await self.service.handle_status(user.id)
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _cmd_history(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        arg = ctx.args[0] if ctx.args else None
        responses = await self.service.handle_history(user.id, arg)
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _cmd_pause(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        responses = await self.service.handle_pause(user.id)
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _cmd_resume(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        responses = await self.service.handle_resume(user.id)
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _cmd_style(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        arg = " ".join(ctx.args) if ctx.args else None
        responses = await self.service.handle_style(user.id, arg)
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _cmd_vibe(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        arg = " ".join(ctx.args) if ctx.args else None
        responses = await self.service.handle_vibe(user.id, arg)
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _cmd_mood(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        arg = " ".join(ctx.args) if ctx.args else None
        responses = await self.service.handle_mood(user.id, arg)
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _cmd_goal(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        text = " ".join(ctx.args) if ctx.args else None
        responses = await self.service.handle_goal(user.id, text)
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _cmd_together(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        text = " ".join(ctx.args) if ctx.args else None
        responses = await self.service.handle_together(user.id, text)
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _cmd_goals(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        responses = await self.service.handle_goals(user.id)
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _cmd_settings(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        arg = " ".join(ctx.args) if ctx.args else None
        responses = await self.service.handle_settings(user.id, arg)
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _cmd_skip(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        # Route through handle_message — onboarding intercept handles /skip
        responses = await self.service.handle_message(user.id, user.first_name, "/skip")
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _cmd_remember(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        responses = await self.service.handle_remember(user.id)
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _cmd_search(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        query = " ".join(ctx.args) if ctx.args else None
        responses = await self.service.handle_search(user.id, query)
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _cmd_conflicts(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        responses = await self.service.handle_conflicts(user.id)
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _cmd_timeline(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        arg = " ".join(ctx.args) if ctx.args else None
        responses = await self.service.handle_timeline(user.id, arg)
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _on_message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not is_user_allowed(self.config, user.id):
            return
        text = update.message.text or ""
        await update.effective_chat.send_action(ChatAction.TYPING)
        responses = await self.service.handle_message(user.id, user.first_name, text)
        for r in responses:
            await self._send_response(ctx.bot, update.effective_chat.id, r)

    async def _on_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        user = query.from_user
        if not is_user_allowed(self.config, user.id):
            return
        responses = await self.service.handle_callback(user.id, query.data)
        for r in responses:
            await self._send_response(ctx.bot, query.message.chat_id, r)
