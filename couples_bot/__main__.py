"""Entry point — wire handlers to python-telegram-bot and run long-polling."""

from __future__ import annotations

import asyncio
import logging
import os
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

from couples_bot.approval import delivery_loop
from couples_bot.backup import backup_loop
from couples_bot.checkin import checkin_loop
from couples_bot.config import Config, is_user_allowed, load_config
from couples_bot.db import init_db
from couples_bot.digest import digest_loop
from couples_bot.handlers import BotResponse, BotService
from couples_bot.llm import create_llm_client
from couples_bot.nudges import nudge_loop
from couples_bot.prompts import load_persona

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Adapter: convert BotResponse → Telegram API calls
# ---------------------------------------------------------------------------

_service: BotService  # module-level; set in main()
_config: Config


def _build_markup(
    response: BotResponse,
) -> InlineKeyboardMarkup | None:
    if not response.buttons:
        return None
    rows = [
        [InlineKeyboardButton(b.text, callback_data=b.callback_data) for b in row]
        for row in response.buttons
    ]
    return InlineKeyboardMarkup(rows)


async def _send_response(
    bot, chat_id: int, response: BotResponse
) -> None:
    target = response.target_user_id or chat_id
    markup = _build_markup(response)

    if response.document:
        fname, content = response.document
        doc = BytesIO(content.encode("utf-8"))
        doc.name = fname
        await bot.send_document(chat_id=target, document=doc, caption=response.text[:1024])
    else:
        await bot.send_message(
            chat_id=target,
            text=response.text,
            reply_markup=markup,
            parse_mode="HTML",
        )


# ---------------------------------------------------------------------------
# Command adapters
# ---------------------------------------------------------------------------


async def _cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_user_allowed(_config, user.id):
        return
    responses = await _service.handle_start(user.id, user.first_name)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_join(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_user_allowed(_config, user.id):
        return
    args = ctx.args
    code = args[0] if args else None
    responses = await _service.handle_join(user.id, user.first_name, code)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_tell(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_user_allowed(_config, user.id):
        return
    text = " ".join(ctx.args) if ctx.args else None
    await update.effective_chat.send_action(ChatAction.TYPING)
    responses = await _service.handle_tell(user.id, text)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_mediate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_user_allowed(_config, user.id):
        return
    await update.effective_chat.send_action(ChatAction.TYPING)
    responses = await _service.handle_mediate(user.id)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_whatdoyouknow(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_user_allowed(_config, user.id):
        return
    responses = await _service.handle_whatdoyouknow(user.id)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_forget(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_user_allowed(_config, user.id):
        return
    arg = " ".join(ctx.args) if ctx.args else None
    responses = await _service.handle_forget(user.id, arg)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_export(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_user_allowed(_config, user.id):
        return
    arg = " ".join(ctx.args) if ctx.args else None
    responses = await _service.handle_export(user.id, arg)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_user_allowed(_config, user.id):
        return
    responses = await _service.handle_help(user.id)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_lang(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_user_allowed(_config, user.id):
        return
    arg = ctx.args[0] if ctx.args else None
    responses = await _service.handle_lang(user.id, arg)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_setup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_user_allowed(_config, user.id):
        return
    arg = " ".join(ctx.args) if ctx.args else None
    responses = await _service.handle_setup(user.id, arg)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_user_allowed(_config, user.id):
        return
    responses = await _service.handle_status(user.id)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_user_allowed(_config, user.id):
        return
    arg = ctx.args[0] if ctx.args else None
    responses = await _service.handle_history(user.id, arg)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_user_allowed(_config, user.id):
        return
    responses = await _service.handle_pause(user.id)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_user_allowed(_config, user.id):
        return
    responses = await _service.handle_resume(user.id)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_style(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_user_allowed(_config, user.id):
        return
    arg = " ".join(ctx.args) if ctx.args else None
    responses = await _service.handle_style(user.id, arg)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_mood(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_user_allowed(_config, user.id):
        return
    arg = " ".join(ctx.args) if ctx.args else None
    responses = await _service.handle_mood(user.id, arg)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_goal(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_user_allowed(_config, user.id):
        return
    text = " ".join(ctx.args) if ctx.args else None
    responses = await _service.handle_goal(user.id, text)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_together(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_user_allowed(_config, user.id):
        return
    text = " ".join(ctx.args) if ctx.args else None
    responses = await _service.handle_together(user.id, text)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_goals(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_user_allowed(_config, user.id):
        return
    responses = await _service.handle_goals(user.id)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_user_allowed(_config, user.id):
        return
    arg = " ".join(ctx.args) if ctx.args else None
    responses = await _service.handle_settings(user.id, arg)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_user_allowed(_config, user.id):
        return
    # Route through handle_message — onboarding intercept handles /skip
    responses = await _service.handle_message(user.id, user.first_name, "/skip")
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_remember(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_user_allowed(_config, user.id):
        return
    responses = await _service.handle_remember(user.id)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_user_allowed(_config, user.id):
        return
    query = " ".join(ctx.args) if ctx.args else None
    responses = await _service.handle_search(user.id, query)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_conflicts(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_user_allowed(_config, user.id):
        return
    responses = await _service.handle_conflicts(user.id)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_timeline(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_user_allowed(_config, user.id):
        return
    arg = " ".join(ctx.args) if ctx.args else None
    responses = await _service.handle_timeline(user.id, arg)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_user_allowed(_config, user.id):
        return
    text = update.message.text or ""
    await update.effective_chat.send_action(ChatAction.TYPING)
    responses = await _service.handle_message(user.id, user.first_name, text)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = query.from_user
    if not is_user_allowed(_config, user.id):
        return
    responses = await _service.handle_callback(user.id, query.data)
    for r in responses:
        await _send_response(ctx.bot, query.message.chat_id, r)


# ---------------------------------------------------------------------------
# Background tasks
# ---------------------------------------------------------------------------

async def _post_init(app: Application) -> None:
    """Start background tasks after the bot application is initialized."""
    async def _send(user_id: int, text: str) -> None:
        await app.bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")

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
            chat_id=user_id, text=text, reply_markup=markup, parse_mode="HTML",
        )

    app.create_task(delivery_loop(
        _service.db_path, _send,
        send_buttons_func=_send_with_buttons,
        poll_interval=_config.delivery_check_interval_seconds,
        review_before_send=_config.review_before_send,
    ))
    logger.info("Delivery loop started")

    if _config.backup_enabled:
        app.create_task(backup_loop(
            _config.db_path,
            _config.backup_directory,
            daily_count=_config.backup_daily_count,
            weekly_count=_config.backup_weekly_count,
        ))
        logger.info("Backup loop started")

    if _config.digest_enabled:
        app.create_task(digest_loop(
            _service.db_path,
            _service.llm,
            _send,
            digest_time=_config.digest_time,
            digest_tz=_config.digest_timezone,
        ))
        logger.info("Digest loop started")

    if _config.checkin_enabled:
        app.create_task(checkin_loop(
            _service.db_path,
            _service.llm,
            _send,
            checkin_day=_config.checkin_day,
            checkin_time=_config.checkin_time,
            checkin_tz=_config.checkin_timezone,
            checkin_questions=_config.checkin_questions,
        ))
        logger.info("Check-in loop started")

    if _config.nudges_enabled or _config.appreciation_enabled:
        app.create_task(nudge_loop(
            _service.db_path,
            _service.llm,
            _send,
            config=_config,
            poll_interval=_config.nudges_interval_hours * 3600,
        ))
        logger.info("Nudge loop started")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    global _service, _config

    config = load_config()
    _config = config
    logger.info("Config loaded — provider: %s, default model: %s", config.llm_provider, config.model_default)

    # Load persona (warm the cache; fallback used if file missing)
    persona_path = os.path.join(config.prompts_dir, "persona.md")
    if os.path.isfile(persona_path):
        load_persona(persona_path)
        logger.info("Persona loaded from %s", persona_path)
    else:
        load_persona(config.persona_file)
        logger.info("Persona loaded from legacy path %s", config.persona_file)

    # Init DB synchronously before the event loop starts
    asyncio.run(init_db(config.db_path))
    logger.info("Database initialized at %s", config.db_path)

    llm = create_llm_client(config, config.db_path)
    _service = BotService(config, config.db_path, llm)

    app = (
        Application.builder()
        .token(config.telegram_token)
        .post_init(_post_init)
        .build()
    )

    # Register handlers (order matters — commands before generic message)
    app.add_handler(CommandHandler("start", _cmd_start))
    app.add_handler(CommandHandler("join", _cmd_join))
    app.add_handler(CommandHandler("tell", _cmd_tell))
    app.add_handler(CommandHandler("mediate", _cmd_mediate))
    app.add_handler(CommandHandler("whatdoyouknow", _cmd_whatdoyouknow))
    app.add_handler(CommandHandler("forget", _cmd_forget))
    app.add_handler(CommandHandler("export", _cmd_export))
    app.add_handler(CommandHandler("lang", _cmd_lang))
    app.add_handler(CommandHandler("setup", _cmd_setup))
    app.add_handler(CommandHandler("status", _cmd_status))
    app.add_handler(CommandHandler("history", _cmd_history))
    app.add_handler(CommandHandler("pause", _cmd_pause))
    app.add_handler(CommandHandler("resume", _cmd_resume))
    app.add_handler(CommandHandler("style", _cmd_style))
    app.add_handler(CommandHandler("mood", _cmd_mood))
    app.add_handler(CommandHandler("goal", _cmd_goal))
    app.add_handler(CommandHandler("together", _cmd_together))
    app.add_handler(CommandHandler("goals", _cmd_goals))
    app.add_handler(CommandHandler("settings", _cmd_settings))
    app.add_handler(CommandHandler("skip", _cmd_skip))
    app.add_handler(CommandHandler("remember", _cmd_remember))
    app.add_handler(CommandHandler("search", _cmd_search))
    app.add_handler(CommandHandler("conflicts", _cmd_conflicts))
    app.add_handler(CommandHandler("timeline", _cmd_timeline))
    app.add_handler(CommandHandler("help", _cmd_help))
    app.add_handler(CallbackQueryHandler(_on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_message))

    logger.info("pAIrtner starting long-polling…")
    app.run_polling()


if __name__ == "__main__":
    main()
