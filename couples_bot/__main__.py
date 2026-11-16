"""Entry point — wire handlers to python-telegram-bot and run long-polling."""

from __future__ import annotations

import asyncio
import logging
from io import BytesIO

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from couples_bot.approval import delivery_loop
from couples_bot.config import load_config
from couples_bot.db import init_db
from couples_bot.handlers import BotResponse, BotService
from couples_bot.llm import GeminiClient

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Adapter: convert BotResponse → Telegram API calls
# ---------------------------------------------------------------------------


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

_service: BotService  # module-level; set in main()


async def _cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    responses = await _service.handle_start(user.id, user.first_name)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_join(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    args = ctx.args
    code = args[0] if args else None
    responses = await _service.handle_join(user.id, user.first_name, code)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_tell(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = " ".join(ctx.args) if ctx.args else None
    responses = await _service.handle_tell(user.id, text)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_mediate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    responses = await _service.handle_mediate(user.id)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_whatdoyouknow(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    responses = await _service.handle_whatdoyouknow(user.id)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_forget(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    arg = " ".join(ctx.args) if ctx.args else None
    responses = await _service.handle_forget(user.id, arg)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_export(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    responses = await _service.handle_export(user.id)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    responses = await _service.handle_help(user.id)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = update.message.text or ""
    responses = await _service.handle_message(user.id, user.first_name, text)
    for r in responses:
        await _send_response(ctx.bot, update.effective_chat.id, r)


async def _on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = query.from_user
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

    app.create_task(delivery_loop(_service.db_path, _send))
    logger.info("Delivery loop started")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    global _service

    config = load_config()
    logger.info("Config loaded — default model: %s", config.model_default)

    # Init DB synchronously before the event loop starts
    asyncio.run(init_db(config.db_path))
    logger.info("Database initialized at %s", config.db_path)

    llm = GeminiClient(config, config.db_path)
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
    app.add_handler(CommandHandler("help", _cmd_help))
    app.add_handler(CallbackQueryHandler(_on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_message))

    logger.info("Starting long-polling…")
    app.run_polling()


if __name__ == "__main__":
    main()
