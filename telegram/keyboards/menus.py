"""
menus.py — All Telegram keyboard builders for Obelius bot.
"""
from __future__ import annotations

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)

from config import BACKEND_URL, WEBHOOK_URL  # our config, no 'telegram.' prefix

# Public HTTPS base — required for web_app buttons (Telegram rejects http://)
_PUBLIC_URL = (WEBHOOK_URL or BACKEND_URL).rstrip("/")


# ── Persistent reply keyboards ────────────────────────────────────────────────

def main_menu(logged_in: bool = True) -> ReplyKeyboardMarkup:
    if logged_in:
        return ReplyKeyboardMarkup(
            [
                [KeyboardButton("💬 New Chat"), KeyboardButton("📋 History")],
                [KeyboardButton("📎 Upload File"), KeyboardButton("🧠 My Memory")],
                [KeyboardButton("👤 Account"), KeyboardButton("❓ Help")],
            ],
            resize_keyboard=True,
            is_persistent=True,
        )
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🔐 Login")],
            [KeyboardButton("❓ Help")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def remove_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


# ── Inline keyboards ──────────────────────────────────────────────────────────

def login_keyboard(login_url: str) -> InlineKeyboardMarkup:
    """
    Opens auth.eternal.uz as a Telegram Mini App (bottom sheet).
    After login the success page calls window.Telegram.WebApp.close() — sheet
    auto-dismisses and the user lands back in the chat.
    """
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔐 Login to Obelius", web_app=WebAppInfo(url=login_url)),
    ]])


def web_app_keyboard() -> InlineKeyboardMarkup | None:
    """Open the full Obelius web UI inside Telegram. Requires HTTPS — omitted on localhost."""
    if not _PUBLIC_URL.startswith("https://"):
        return None
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🌐 Open Obelius Web App",
            web_app=WebAppInfo(url=_PUBLIC_URL),
        ),
    ]])


def sources_keyboard(sources: list) -> InlineKeyboardMarkup | None:
    """
    Inline buttons for source citations.
    Web sources → URL buttons (tap to open).
    Doc sources → callback buttons (show info alert).
    """
    buttons: list[list[InlineKeyboardButton]] = []
    for i, src in enumerate(sources[:6]):
        label_base = (src.get("title") or "Source")[:38]
        if src.get("type") == "web":
            url = src.get("url", "")
            if url:
                buttons.append([InlineKeyboardButton(f"🌐 {label_base}", url=url)])
        else:
            page = src.get("page")
            suffix = f" p.{page + 1}" if page is not None else ""
            buttons.append([InlineKeyboardButton(
                f"📄 {label_base}{suffix}",
                callback_data=f"src:{i}",
            )])
    return InlineKeyboardMarkup(buttons) if buttons else None


def conversations_keyboard(convs: list, page: int = 0) -> InlineKeyboardMarkup:
    """Paginated conversation list with open/delete buttons."""
    PAGE = 5
    start  = page * PAGE
    slice_ = convs[start : start + PAGE]

    buttons: list[list[InlineKeyboardButton]] = []
    for conv in slice_:
        title = (conv.get("title") or "Untitled")[:34]
        cid   = conv["id"]
        cnt   = conv.get("message_count", 0)
        buttons.append([
            InlineKeyboardButton(f"💬 {title} ({cnt})", callback_data=f"copen:{cid}"),
            InlineKeyboardButton("🗑️", callback_data=f"cdel:{cid}"),
        ])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"hpage:{page - 1}"))
    if start + PAGE < len(convs):
        nav.append(InlineKeyboardButton("Next ▶", callback_data=f"hpage:{page + 1}"))
    if nav:
        buttons.append(nav)

    buttons.append([
        InlineKeyboardButton("✏️ New Chat",  callback_data="hist:new"),
        InlineKeyboardButton("🗑️ Clear All", callback_data="hist:clearall"),
    ])
    return InlineKeyboardMarkup(buttons)


def conversation_detail_keyboard(conv_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("▶ Resume this chat", callback_data=f"cresume:{conv_id}"),
            InlineKeyboardButton("🗑️ Delete",           callback_data=f"cdel:{conv_id}"),
        ],
        [InlineKeyboardButton("◀ Back to History", callback_data="hpage:0")],
    ])


def confirm_keyboard(action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes",    callback_data=f"confirm:{action}"),
        InlineKeyboardButton("❌ Cancel", callback_data="confirm:cancel"),
    ]])


def upload_success_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("💬 Ask about this file", callback_data="ask:doc"),
    ]])


def memory_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🗑️ Clear my memory", callback_data="memory:clear"),
        InlineKeyboardButton("❌ Cancel",          callback_data="confirm:cancel"),
    ]])
