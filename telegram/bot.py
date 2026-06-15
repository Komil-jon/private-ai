"""
bot.py — Telegram bot entry point.

IMPORT NOTE:
  Our folder is named `telegram/` which would shadow python-telegram-bot's `telegram`
  package if we made it a Python package (with __init__.py).
  Solution: no __init__.py here. When run as `python telegram/bot.py`, Python adds
  `telegram/` to sys.path[0], so `from config import ...` finds our config.py, and
  `from telegram import Update` correctly finds the library in site-packages.

Dev  (BOT_MODE=polling):
    cd /path/to/private-ai && python telegram/bot.py

Prod (BOT_MODE=webhook):
    FastAPI backend calls get_application() from this file.
    Telegram POSTs updates to {WEBHOOK_URL}/telegram/webhook.
    FastAPI route feeds them to application.process_update(update).

Telegram Bot API features used:
  • Commands with BotCommandScope (private chats)
  • ReplyKeyboard (persistent bottom menu)
  • InlineKeyboard with URL, callback, and WebApp buttons
  • Callback query routing (history, sources, confirmations)
  • ChatAction (typing, upload_document)
  • Progressive message editing (streaming AI effect)
  • HTML parse mode with markdown→HTML conversion
  • WebAppInfo (open full web UI inside Telegram)
  • Document & photo handlers
  • Inline query handler (@bot quick-ask)
  • Error handler with logging
  • Webhook with secret token validation (prod)
  • Long polling with drop_pending_updates (dev)
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

# ── Path setup (MUST be first) ────────────────────────────────────────────────
# Add telegram/ dir to sys.path so our modules resolve without the 'telegram.' prefix.
# This does NOT shadow the library because our dir contains no 'telegram/' subdirectory.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ── Library imports (python-telegram-bot) ─────────────────────────────────────
from telegram import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

# ── Our modules (imported without 'telegram.' prefix) ─────────────────────────
from config import BOT_TOKEN, BOT_MODE, WEBHOOK_URL, WEBHOOK_SECRET, WEBHOOK_PATH
from handlers.start import cmd_start, cmd_help
from handlers.auth import cmd_login, cmd_logout, cmd_account
from handlers.company import cmd_company
from handlers.chat import handle_message
from handlers.documents import handle_document, handle_photo, handle_upload_file_btn
from handlers.history import cmd_history, cmd_newchat, cmd_clear, handle_callback
from handlers.memory import cmd_memory
from services.session import init_indexes

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
log = logging.getLogger("obelius.tg.bot")


# ── Bot commands menu (shown in Telegram's / autocomplete) ────────────────────

_COMMANDS = [
    BotCommand("start",   "👋 Welcome + current status"),
    BotCommand("help",    "❓ Show all commands"),
    BotCommand("login",   "🔐 Log in to Obelius"),
    BotCommand("logout",  "🚪 Sign out"),
    BotCommand("newchat", "✏️ Start a new conversation"),
    BotCommand("history", "📋 Browse conversation history"),
    BotCommand("clear",   "🗑️ Delete all conversations"),
    BotCommand("memory",  "🧠 View AI memory about you"),
    BotCommand("account", "👤 Account info"),
    BotCommand("company", "🏢 View / switch company"),
]


# ── Inline query handler ──────────────────────────────────────────────────────

async def handle_inline_query(update: Update, context) -> None:
    """
    @ObeliusBot <question> in any chat — returns a tappable result that sends
    the question to the bot as a regular message.
    """
    query_text = update.inline_query.query.strip()
    if not query_text:
        await update.inline_query.answer([], cache_time=0)
        return

    result = InlineQueryResultArticle(
        id="ask",
        title=f"Ask Obelius: {query_text[:60]}",
        description="Tap to send this question to Obelius AI",
        input_message_content=InputTextMessageContent(query_text),
    )
    await update.inline_query.answer([result], cache_time=0)


# ── Error handler ─────────────────────────────────────────────────────────────

async def error_handler(update: object, context) -> None:
    log.error("Unhandled exception for update %s: %s", update, context.error, exc_info=context.error)


# ── Reply keyboard button router ──────────────────────────────────────────────

async def route_reply_button(update: Update, context) -> None:
    """Dispatch persistent reply-keyboard buttons to the right handler."""
    text = (update.message.text or "").strip()
    dispatch = {
        "💬 New Chat":    cmd_newchat,
        "📋 History":     cmd_history,
        "📎 Upload File": handle_upload_file_btn,
        "🧠 My Memory":   cmd_memory,
        "👤 Account":     cmd_account,
        "🏢 Company":     cmd_company,
        "❓ Help":        cmd_help,
        "🔐 Login":       cmd_login,
    }
    handler = dispatch.get(text)
    if handler:
        await handler(update, context)
    else:
        await handle_message(update, context)


# ── Application builder ───────────────────────────────────────────────────────

def build_application() -> Application:
    if not BOT_TOKEN:
        raise ValueError(
            "BOT_TOKEN is not set.\n"
            "Add it to telegram/.env  OR  backend/app/.env:\n"
            "  BOT_TOKEN = 123456789:AAAA..."
        )

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("login",   cmd_login))
    app.add_handler(CommandHandler("logout",  cmd_logout))
    app.add_handler(CommandHandler("account", cmd_account))
    app.add_handler(CommandHandler("newchat", cmd_newchat))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("clear",   cmd_clear))
    app.add_handler(CommandHandler("memory",  cmd_memory))
    app.add_handler(CommandHandler("company", cmd_company))

    # Inline keyboard callbacks (history, sources, confirm, memory actions)
    app.add_handler(CallbackQueryHandler(handle_callback))

    # File uploads
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Text messages (reply-keyboard buttons + free-form AI chat)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_reply_button))

    # Inline queries (@ObeliusBot <text> in any chat)
    app.add_handler(InlineQueryHandler(handle_inline_query))

    # Global error handler
    app.add_error_handler(error_handler)

    return app


# ── Webhook helpers (used by FastAPI telegram_router.py) ──────────────────────

_application: Application | None = None


async def get_application() -> Application:
    """Return the singleton Application (initialised once for webhook mode)."""
    global _application
    if _application is None:
        _application = build_application()
        await _application.initialize()
        await _application.start()
        log.info("Telegram Application started (webhook mode).")
    return _application


async def shutdown_application() -> None:
    global _application
    if _application:
        await _application.stop()
        await _application.shutdown()
        _application = None
        log.info("Telegram Application shut down.")


# ── Standalone polling entry point ────────────────────────────────────────────
#
# IMPORTANT: In python-telegram-bot 20.x, `app.run_polling()` is NOT a coroutine.
# It manages its own event loop internally. Calling it inside `asyncio.run()` causes
# "This event loop is already running" because two loops fight over the same thread.
#
# Correct pattern:
#   1. Use `post_init` for any async setup (indexes, set_my_commands).
#   2. Call `app.run_polling()` directly — no asyncio.run() wrapper.

async def _post_init(application: Application) -> None:
    """Async setup that runs inside PTB's own event loop, before polling starts."""
    await init_indexes()
    await application.bot.set_my_commands(
        _COMMANDS, scope=BotCommandScopeAllPrivateChats()
    )
    log.info("Bot post-init complete (commands registered, session indexes ensured).")


def _build_polling_application() -> Application:
    """Build application with post_init hook for polling mode."""
    if not BOT_TOKEN:
        raise ValueError(
            "BOT_TOKEN is not set.\n"
            "Add it to backend/app/.env:  BOT_TOKEN = 123456789:AAAA..."
        )

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("login",   cmd_login))
    app.add_handler(CommandHandler("logout",  cmd_logout))
    app.add_handler(CommandHandler("account", cmd_account))
    app.add_handler(CommandHandler("newchat", cmd_newchat))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("clear",   cmd_clear))
    app.add_handler(CommandHandler("memory",  cmd_memory))
    app.add_handler(CommandHandler("company", cmd_company))

    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_reply_button))
    app.add_handler(InlineQueryHandler(handle_inline_query))
    app.add_error_handler(error_handler)

    return app


if __name__ == "__main__":
    if BOT_MODE == "webhook":
        print(
            "BOT_MODE=webhook: run the FastAPI backend instead.\n"
            "The webhook lifecycle is managed by backend/app/main.py.\n"
            "Set BOT_MODE=polling for local development."
        )
        sys.exit(0)

    log.info("Starting in POLLING mode (drop_pending_updates=True).")
    polling_app = _build_polling_application()
    # run_polling() manages its own event loop — do NOT wrap in asyncio.run()
    polling_app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )
