"""websearch.py — /websearch command: view or toggle live web search."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from services import backend, session as sess

log = logging.getLogger("obelius.tg.websearch")


async def cmd_websearch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /websearch          — show current status
    /websearch on|off    — turn live web search on or off
    Preference is shared with the web UI's toggle button (same account).
    """
    user    = update.effective_user
    session = await sess.get_session(user.id)

    if not session or not session.get("jwt_token"):
        await update.message.reply_text("🔐 Log in first with /login to change this setting.")
        return

    jwt = session["jwt_token"]
    arg = (context.args[0].lower() if context.args else "").strip()

    try:
        if arg in ("on", "off"):
            result  = await backend.set_web_search_pref(jwt, enabled=(arg == "on"))
            enabled = result.get("web_search_enabled", arg == "on")
        elif arg:
            await update.message.reply_text("Usage: <code>/websearch on</code> or <code>/websearch off</code>", parse_mode=ParseMode.HTML)
            return
        else:
            current = await backend.get_web_search_pref(jwt)
            enabled = current.get("web_search_enabled", True)
    except Exception as exc:
        log.warning("websearch pref failed: %s", exc)
        await update.message.reply_text("❌ Could not update the web search setting. Try again later.")
        return

    status = "🟢 ON" if enabled else "🔴 OFF"
    await update.message.reply_text(
        f"🌐 <b>Live web search:</b> {status}\n\n"
        "When off, I'll never search the web — I'll only use uploaded documents "
        "and what I already know.\n\n"
        "Use <code>/websearch on</code> or <code>/websearch off</code> to change it.",
        parse_mode=ParseMode.HTML,
    )
