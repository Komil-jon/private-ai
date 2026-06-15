"""start.py — /start and /help commands."""
from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from keyboards.menus import login_keyboard, main_menu, web_app_keyboard
from services.session import get_session, create_auth_state
from config import build_login_url


_HELP_TEXT = """
<b>Obelius — Your private AI assistant</b>

<b>Commands</b>
/start   — Welcome + current status
/login   — Log in with your Obelius account
/logout  — Sign out of this device
/newchat — Start a fresh conversation
/history — Browse all your past chats
/company — View or switch your company knowledge base
/memory  — See &amp; manage your AI memory
/clear   — Delete all conversation history
/help    — This message

<b>Bottom menu buttons</b>
💬 New Chat — clear context, start fresh
📋 History  — browse and resume past conversations
📎 Upload File — send a PDF / DOCX / TXT / CSV to analyse
🧠 My Memory — what the AI has learned about you
👤 Account  — login status and account info
❓ Help     — this message

<b>Sending files</b>
Attach a document using the 📎 icon → <b>File</b> (not as a photo).
Supported: PDF · DOCX · TXT · CSV  (max 20 MB)

<b>Live web search</b>
The AI automatically searches the web when you ask about recent events,
news, prices, or anything needing real-time data.

<b>Web App</b>
Open the full Obelius interface inside Telegram using the button below.
"""


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user      = update.effective_user
    session   = await get_session(user.id)
    logged_in = bool(session and session.get("jwt_token"))

    if logged_in:
        name = session.get("full_name") or session.get("username") or user.first_name
        await update.message.reply_text(
            f"👋 Welcome back, <b>{name}</b>!\n\n"
            "I'm <b>Obelius</b> — your private AI with document analysis, "
            "live web search, and memory across sessions.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(logged_in=True),
        )
        webapp_kb = web_app_keyboard()
        if webapp_kb:
            await update.message.reply_text(
                "Open the full web interface inside Telegram:",
                reply_markup=webapp_kb,
            )
    else:
        state     = await create_auth_state(user.id)
        login_url = build_login_url(state)
        await update.message.reply_text(
            f"👋 Hello, <b>{user.first_name}</b>!\n\n"
            "I'm <b>Obelius</b> — a private AI assistant with:\n"
            "• Document upload &amp; RAG analysis\n"
            "• Live web search\n"
            "• AI memory that persists across sessions\n"
            "• Conversation history\n\n"
            "Tap the button to log in — the sheet will close automatically when done.",
            parse_mode=ParseMode.HTML,
            reply_markup=login_keyboard(login_url),
        )
        await update.message.reply_text(
            "Use the menu below:",
            reply_markup=main_menu(logged_in=False),
        )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session   = await get_session(update.effective_user.id)
    logged_in = bool(session and session.get("jwt_token"))
    await update.message.reply_text(
        _HELP_TEXT,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(logged_in=logged_in),
    )
    if logged_in:
        webapp_kb = web_app_keyboard()
        if webapp_kb:
            await update.message.reply_text(
                "Open the full web UI inside Telegram:",
                reply_markup=webapp_kb,
            )
