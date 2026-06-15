"""company.py — /company command: view & change company knowledge base."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from keyboards.menus import company_picker_keyboard
from services import backend, session as sess

log = logging.getLogger("obelius.tg.company")


async def cmd_company(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the current company and let the user switch."""
    user    = update.effective_user
    session = await sess.get_session(user.id)

    if not session or not session.get("jwt_token"):
        await update.message.reply_text("🔐 Log in first with /login to select a company.")
        return

    jwt = session["jwt_token"]

    try:
        current  = await backend.get_user_company(jwt)
        companies = await backend.get_companies()
    except Exception as exc:
        log.warning("company fetch failed: %s", exc)
        await update.message.reply_text("❌ Could not load company list. Try again later.")
        return

    current_name = current.get("name") if current.get("company_id") else None
    email        = session.get("email", "")

    header = (
        f"🏢 <b>Current company:</b> {current_name}\n\nSwitch to a different company:"
        if current_name
        else "⚠️ <b>No company selected yet.</b>\n\nChoose the company you work for:"
    )

    await update.message.reply_text(
        header,
        parse_mode=ParseMode.HTML,
        reply_markup=company_picker_keyboard(companies, email),
    )


async def send_company_picker(chat_id: int, jwt: str, email: str, bot) -> None:
    """
    Proactively send a company picker to chat_id.
    Called by telegram_router after login when no company is set.
    """
    try:
        companies = await backend.get_companies()
    except Exception as exc:
        log.warning("could not fetch companies for picker: %s", exc)
        return

    kb = company_picker_keyboard(companies, email)
    await bot.send_message(
        chat_id=chat_id,
        text=(
            "🏢 <b>One more step — select your company</b>\n\n"
            "Choose the company you work for to access the right knowledge base:"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )
