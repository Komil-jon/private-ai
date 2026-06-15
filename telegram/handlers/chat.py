"""
chat.py — Main AI chat handler with progressive streaming.

Streaming strategy:
  1. Send placeholder "⏳ Thinking…" message
  2. Open backend SSE /stream endpoint
  3. Accumulate tokens; edit the placeholder every EDIT_INTERVAL seconds
  4. On "sources" event, build inline keyboard with citations
  5. On "done", final edit + send sources keyboard
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from typing import Optional

from telegram import Message, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from keyboards.menus import login_keyboard, sources_keyboard
from services import backend, session as sess
from config import build_login_url

log = logging.getLogger("obelius.tg.chat")

EDIT_INTERVAL = 1.5   # seconds between progressive edits
MAX_MSG_LEN   = 4000  # Telegram hard cap is 4096; leave headroom


# ── Markdown → Telegram HTML ─────────────────────────────────────────────────

def _md_to_html(text: str) -> str:
    text = html.escape(text)
    # Code blocks
    text = re.sub(
        r"```(?:\w+)?\n?(.*?)```",
        lambda m: f"<pre>{m.group(1).strip()}</pre>",
        text, flags=re.DOTALL,
    )
    # Inline code
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    # Bold **...**
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    # Bold *...*
    text = re.sub(r"\*(.+?)\*", r"<b>\1</b>", text)
    # Italic _..._
    text = re.sub(r"_(.+?)_", r"<i>\1</i>", text)
    # Hyperlinks [text](url)
    text = re.sub(
        r"\[([^\]]+)\]\((https?://[^\)]+)\)",
        r'<a href="\2">\1</a>',
        text,
    )
    # Headers
    text = re.sub(r"^#{1,3} (.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    return text


def _split_message(text: str) -> list[str]:
    """Split a long message into ≤MAX_MSG_LEN chunks at paragraph boundaries."""
    if len(text) <= MAX_MSG_LEN:
        return [text]
    chunks, current = [], ""
    for para in text.split("\n\n"):
        candidate = current + ("\n\n" if current else "") + para
        if len(candidate) > MAX_MSG_LEN:
            if current:
                chunks.append(current)
            current = para if len(para) <= MAX_MSG_LEN else para[:MAX_MSG_LEN]
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [text]


# ── Guard ─────────────────────────────────────────────────────────────────────

async def _require_auth(update: Update) -> Optional[dict]:
    s = await sess.get_session(update.effective_user.id)
    if not s or not s.get("jwt_token"):
        from services.session import create_auth_state
        state     = await create_auth_state(update.effective_user.id)
        login_url = build_login_url(state)
        await update.message.reply_text("🔐 Log in first.", reply_markup=login_keyboard(login_url))
        return None
    return s


# ── Main chat handler ─────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg_text = (update.message.text or "").strip()
    if not msg_text:
        return

    session = await _require_auth(update)
    if not session:
        return

    jwt         = session["jwt_token"]
    conv_id     = session.get("active_conv_id")
    company_id  = session.get("company_id")
    tg_id       = update.effective_user.id
    session_key = f"sess_tg_{tg_id}"

    await update.effective_chat.send_action(ChatAction.TYPING)

    placeholder: Message = await update.message.reply_text(
        "⏳ <i>Thinking…</i>", parse_mode=ParseMode.HTML
    )

    accumulated   = ""
    sources: list = []
    last_edit     = time.monotonic()
    new_conv_id   = conv_id

    try:
        async for event in backend.stream_chat(jwt, session_key, conv_id, msg_text, company_id):
            etype = event.get("type")

            if etype == "search_info" and event.get("triggered"):
                queries = event.get("queries", [])
                if queries:
                    q_list = " · ".join(queries[:3])
                    await _safe_edit(placeholder, f"🔍 <i>Searching: {html.escape(q_list)}…</i>")

            elif etype == "token":
                accumulated += event.get("text", "")
                now = time.monotonic()
                if now - last_edit >= EDIT_INTERVAL and accumulated.strip():
                    await _safe_edit(placeholder, _md_to_html(accumulated) + " ▌")
                    last_edit = now

            elif etype == "sources":
                sources = event.get("sources", [])

            elif etype == "done":
                new_conv_id = event.get("conv_id") or conv_id
                break

            elif etype == "error":
                await _safe_edit(placeholder, f"❌ {event.get('text', 'Something went wrong.')}")
                return

        # Final formatted response
        final_html = _md_to_html(accumulated.strip()) if accumulated.strip() else "—"
        chunks     = _split_message(final_html)

        await _safe_edit(placeholder, chunks[0])
        for extra in chunks[1:]:
            await update.message.reply_text(extra, parse_mode=ParseMode.HTML)

        # Sources inline keyboard
        if sources:
            kb = sources_keyboard(sources)
            if kb:
                await update.message.reply_text(
                    "📚 <b>Sources</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )

        # Persist updated conv_id
        if new_conv_id and new_conv_id != conv_id:
            await sess.set_active_conv(tg_id, new_conv_id)

    except Exception as exc:
        log.error("handle_message error: %s", exc, exc_info=True)
        await _safe_edit(placeholder, "❌ An unexpected error occurred. Please try again.")


async def _safe_edit(msg: Message, text: str) -> None:
    try:
        await msg.edit_text(text[:MAX_MSG_LEN], parse_mode=ParseMode.HTML)
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            log.warning("edit_text failed: %s", e)
    except Exception as exc:
        log.warning("edit_text unexpected: %s", exc)
