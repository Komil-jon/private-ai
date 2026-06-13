"""auth.py — /login, /logout, /account commands."""
from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from keyboards.menus import login_keyboard, main_menu
from services.session import get_session, clear_session, create_auth_state
from config import build_login_url


async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    session = await get_session(user.id)

    if session and session.get("jwt_token"):
        name = session.get("full_name") or session.get("username") or user.first_name
        await update.message.reply_text(
            f"✅ You're already logged in as <b>{name}</b>.\n\nUse /logout to sign out.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(logged_in=True),
        )
        return

    state     = await create_auth_state(user.id)
    login_url = build_login_url(state)
    await update.message.reply_text(
        "🔐 <b>Login to Obelius</b>\n\n"
        "Tap the button — it opens as a sheet inside Telegram.\n"
        "After logging in the sheet closes automatically.\n\n"
        "<i>This link expires in 10 minutes.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=login_keyboard(login_url),
    )


async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    session = await get_session(user.id)

    if not session:
        await update.message.reply_text(
            "You're not logged in.", reply_markup=main_menu(logged_in=False)
        )
        return

    name = session.get("full_name") or session.get("username") or user.first_name
    await clear_session(user.id)
    await update.message.reply_text(
        f"👋 Logged out, <b>{name}</b>.\n\n"
        "Your chat history and AI memory remain saved.\nUse /login to sign back in.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(logged_in=False),
    )


async def cmd_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user    = update.effective_user
    session = await get_session(user.id)

    if not session or not session.get("jwt_token"):
        state     = await create_auth_state(user.id)
        login_url = build_login_url(state)
        await update.message.reply_text(
            "🔐 You're not logged in.",
            reply_markup=login_keyboard(login_url),
        )
        return

    full_name = session.get("full_name") or "—"
    username  = session.get("username")  or "—"
    email     = session.get("email")     or "—"
    conv_id   = session.get("active_conv_id") or "none (new chat)"

    await update.message.reply_text(
        f"<b>👤 Account</b>\n\n"
        f"Name:        <b>{full_name}</b>\n"
        f"Username:    <code>{username}</code>\n"
        f"Email:       <code>{email}</code>\n"
        f"Active chat: <code>{conv_id}</code>\n\n"
        f"/logout to sign out.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(logged_in=True),
    )
