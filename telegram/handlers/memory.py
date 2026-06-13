"""memory.py — /memory command."""
from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from keyboards.menus import login_keyboard, main_menu, memory_keyboard
from services.session import get_session, create_auth_state, _get_db
from config import build_login_url


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user = update.effective_user
    session = await get_session(tg_user.id)

    if not session or not session.get("jwt_token"):
        state     = await create_auth_state(tg_user.id)
        login_url = build_login_url(state)
        await update.message.reply_text("🔐 Log in first.", reply_markup=login_keyboard(login_url))
        return

    db  = _get_db()
    doc = await db["user_memory"].find_one({"user_id": session.get("user_id")})

    if not doc:
        await update.message.reply_text(
            "🧠 <b>Your AI Memory</b>\n\n"
            "No memory yet. Chat with the AI and it will start learning about you — "
            "your preferences, goals, and background.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(logged_in=True),
        )
        return

    summary    = doc.get("summary", "")
    facts      = doc.get("facts", [])
    updated    = doc.get("updated_at")
    updated_str = updated.strftime("%d %b %Y, %H:%M") if updated else "—"

    parts = [f"🧠 <b>Your AI Memory</b>  <i>(updated {updated_str})</i>"]

    if summary:
        parts.append(f"\n<b>Summary</b>\n{summary}")

    if facts:
        parts.append(f"\n<b>Known facts ({len(facts)})</b>")
        for f in facts[-20:]:
            parts.append(f"  • {f}")
        if len(facts) > 20:
            parts.append(f"  <i>… and {len(facts) - 20} more</i>")

    await update.message.reply_text(
        "\n".join(parts),
        parse_mode=ParseMode.HTML,
        reply_markup=memory_keyboard(),
    )
