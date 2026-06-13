"""
history.py — Conversation history browser + management.

Commands:  /history, /newchat, /clear
Callbacks: hpage:<n>, copen:<id>, cdel:<id>, cresume:<id>,
           hist:new, hist:clearall, confirm:<action>, confirm:cancel,
           src:<i>, ask:doc, memory:clear
"""
from __future__ import annotations

import logging
from datetime import datetime

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from keyboards.menus import (
    confirm_keyboard,
    conversation_detail_keyboard,
    conversations_keyboard,
    login_keyboard,
    main_menu,
)
from services import backend, session as sess
from config import build_login_url

log = logging.getLogger("obelius.tg.history")


def _fmt_ts(ts_str: str) -> str:
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y, %H:%M")
    except Exception:
        return ts_str


async def _require_auth(update: Update) -> dict | None:
    s = await sess.get_session(update.effective_user.id)
    if not s or not s.get("jwt_token"):
        from services.session import create_auth_state
        state     = await create_auth_state(update.effective_user.id)
        login_url = build_login_url(state)
        target    = update.message or update.callback_query.message
        await target.reply_text("🔐 Log in first.", reply_markup=login_keyboard(login_url))
        return None
    return s


# ── Commands ──────────────────────────────────────────────────────────────────

async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_auth(update)
    if not session:
        return
    try:
        convs = await backend.list_conversations(session["jwt_token"])
    except Exception as exc:
        await update.message.reply_text(f"❌ Could not load history: {exc}")
        return
    if not convs:
        await update.message.reply_text(
            "📋 No conversations yet. Send a message to start!",
            reply_markup=main_menu(logged_in=True),
        )
        return
    await update.message.reply_text(
        f"📋 <b>Your Conversations</b>  ({len(convs)} total)\n\nTap to open · 🗑️ to delete.",
        parse_mode=ParseMode.HTML,
        reply_markup=conversations_keyboard(convs, page=0),
    )


async def cmd_newchat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_auth(update)
    if not session:
        return
    await sess.set_active_conv(update.effective_user.id, None)
    await update.message.reply_text(
        "✏️ <b>New conversation started.</b>\n\nWhat would you like to talk about?",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(logged_in=True),
    )


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = await _require_auth(update)
    if not session:
        return
    await update.message.reply_text(
        "⚠️ <b>Delete ALL conversations?</b>\n\nThis cannot be undone. Your AI memory will be kept.",
        parse_mode=ParseMode.HTML,
        reply_markup=confirm_keyboard("clearall"),
    )


# ── Callback query dispatcher ─────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data    = query.data or ""
    tg_user = update.effective_user
    session = await sess.get_session(tg_user.id)

    if not session or not session.get("jwt_token"):
        await query.edit_message_text("🔐 Session expired. Use /login to log back in.")
        return

    jwt = session["jwt_token"]

    # ── Pagination ────────────────────────────────────────────────────────────
    if data.startswith("hpage:"):
        page = int(data.split(":")[1])
        try:
            convs = await backend.list_conversations(jwt)
        except Exception as exc:
            await query.edit_message_text(f"❌ {exc}")
            return
        if not convs:
            await query.edit_message_text("No conversations yet.")
            return
        await query.edit_message_text(
            f"📋 <b>Your Conversations</b>  ({len(convs)} total)",
            parse_mode=ParseMode.HTML,
            reply_markup=conversations_keyboard(convs, page=page),
        )

    # ── Open conversation ─────────────────────────────────────────────────────
    elif data.startswith("copen:"):
        conv_id = data.split(":", 1)[1]
        try:
            msgs = await backend.get_messages(jwt, conv_id)
        except Exception as exc:
            await query.edit_message_text(f"❌ {exc}")
            return

        lines = []
        for m in msgs[-5:]:
            role    = "You" if m["role"] == "user" else "🤖 AI"
            snippet = (m.get("content") or "")[:100].replace("\n", " ")
            if len(m.get("content", "")) > 100:
                snippet += "…"
            lines.append(f"<b>{role}</b>  <i>{_fmt_ts(m.get('ts',''))}</i>\n{snippet}")

        preview = "\n\n".join(lines) or "No messages yet."
        await query.edit_message_text(
            f"💬 <b>Conversation</b>  ({len(msgs)} messages)\n\n{preview}",
            parse_mode=ParseMode.HTML,
            reply_markup=conversation_detail_keyboard(conv_id),
        )

    # ── Resume conversation ───────────────────────────────────────────────────
    elif data.startswith("cresume:"):
        conv_id = data.split(":", 1)[1]
        await sess.set_active_conv(tg_user.id, conv_id)
        await query.edit_message_text(
            f"▶ <b>Resumed.</b>  <code>{conv_id[:16]}…</code>\n\nSend your next message.",
            parse_mode=ParseMode.HTML,
        )

    # ── Delete one conversation (ask confirmation) ────────────────────────────
    elif data.startswith("cdel:"):
        conv_id = data.split(":", 1)[1]
        await query.edit_message_text(
            "⚠️ Delete this conversation?",
            reply_markup=confirm_keyboard(f"cdel_{conv_id}"),
        )

    # ── New chat ──────────────────────────────────────────────────────────────
    elif data == "hist:new":
        await sess.set_active_conv(tg_user.id, None)
        await query.edit_message_text("✏️ New conversation started. Send your first message!")

    # ── Clear all (ask confirmation) ──────────────────────────────────────────
    elif data == "hist:clearall":
        await query.edit_message_text(
            "⚠️ <b>Delete ALL conversations?</b>\n\nThis cannot be undone.",
            parse_mode=ParseMode.HTML,
            reply_markup=confirm_keyboard("clearall"),
        )

    # ── Confirmation dispatch ─────────────────────────────────────────────────
    elif data.startswith("confirm:"):
        action = data.split(":", 1)[1]

        if action == "cancel":
            await query.edit_message_text("❌ Cancelled.")

        elif action == "clearall":
            try:
                n = await backend.delete_all_conversations(jwt)
                await sess.set_active_conv(tg_user.id, None)
                await query.edit_message_text(f"🗑️ Deleted <b>{n}</b> conversation(s).",
                                              parse_mode=ParseMode.HTML)
            except Exception as exc:
                await query.edit_message_text(f"❌ {exc}")

        elif action.startswith("cdel_"):
            conv_id = action[5:]
            try:
                await backend.delete_conversation(jwt, conv_id)
                if session.get("active_conv_id") == conv_id:
                    await sess.set_active_conv(tg_user.id, None)
                await query.edit_message_text("🗑️ Conversation deleted.")
            except Exception as exc:
                await query.edit_message_text(f"❌ {exc}")

        elif action == "memory_clear":
            from services.session import _get_db
            db = _get_db()
            await db["user_memory"].delete_one({"user_id": session.get("user_id")})
            await query.edit_message_text("🧠 Memory cleared. The AI will start fresh.")

    # ── Source doc chunk info ─────────────────────────────────────────────────
    elif data.startswith("src:"):
        await query.answer("Source referenced from uploaded document.", show_alert=True)

    # ── "Ask about this file" after upload ────────────────────────────────────
    elif data == "ask:doc":
        await query.edit_message_text("💬 Go ahead — type your question about the uploaded file!")

    # ── Memory clear (from /memory keyboard) ─────────────────────────────────
    elif data == "memory:clear":
        await query.edit_message_text(
            "⚠️ <b>Clear your AI memory?</b>\n\n"
            "The AI will forget everything it has learned about you.",
            parse_mode=ParseMode.HTML,
            reply_markup=confirm_keyboard("memory_clear"),
        )
