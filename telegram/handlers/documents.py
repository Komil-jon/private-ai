"""documents.py — File upload handler."""
from __future__ import annotations

import logging
import os

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

from keyboards.menus import login_keyboard, upload_success_keyboard
from services import backend, session as sess
from config import build_login_url, MAX_FILE_MB, ALLOWED_EXTENSIONS

log = logging.getLogger("obelius.tg.documents")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    doc     = update.message.document
    tg_user = update.effective_user

    session = await sess.get_session(tg_user.id)
    if not session or not session.get("jwt_token"):
        from services.session import create_auth_state
        state     = await create_auth_state(tg_user.id)
        login_url = build_login_url(state)
        await update.message.reply_text(
            "🔐 Log in first to upload files.", reply_markup=login_keyboard(login_url)
        )
        return

    filename = doc.file_name or "upload"
    ext      = os.path.splitext(filename)[-1].lower()

    if ext not in ALLOWED_EXTENSIONS:
        await update.message.reply_text(
            f"❌ <b>Unsupported file type:</b> <code>{ext}</code>\n\n"
            f"Supported: <code>.pdf  .docx  .txt  .csv</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    size_mb = (doc.file_size or 0) / (1024 * 1024)
    if size_mb > MAX_FILE_MB:
        await update.message.reply_text(
            f"❌ File too large ({size_mb:.1f} MB). Maximum is {MAX_FILE_MB} MB."
        )
        return

    await update.effective_chat.send_action(ChatAction.UPLOAD_DOCUMENT)
    status = await update.message.reply_text(
        f"⏳ Uploading <b>{filename}</b>…", parse_mode=ParseMode.HTML
    )

    try:
        tg_file  = await context.bot.get_file(doc.file_id)
        content  = bytes(await tg_file.download_as_bytearray())

        jwt      = session["jwt_token"]
        conv_id  = session.get("active_conv_id")
        sess_key = f"sess_tg_{tg_user.id}"

        result   = await backend.upload_file(jwt, sess_key, conv_id, filename, content)
        files    = result.get("files", [filename])
        file_list = "\n".join(f"  • <code>{f}</code>" for f in files)

        await status.edit_text(
            f"✅ <b>Upload complete!</b>\n\n{file_list}\n\n"
            "The file is indexed. Ask me anything about it.",
            parse_mode=ParseMode.HTML,
            reply_markup=upload_success_keyboard(),
        )

    except Exception as exc:
        log.error("Document upload failed: %s", exc, exc_info=True)
        await status.edit_text(f"❌ Upload failed: {exc}\n\nPlease try again.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📷 <b>Photos can't be analysed yet.</b>\n\n"
        "Send your document as a <b>File</b> (📎 → File), not as a photo.",
        parse_mode=ParseMode.HTML,
    )


async def handle_upload_file_btn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📎 <b>Send me a file to analyse</b>\n\n"
        "Supported: <code>PDF · DOCX · TXT · CSV</code>  (max 20 MB)\n\n"
        "Tap the <b>📎 attachment icon</b> → <b>File</b> → choose your document.",
        parse_mode=ParseMode.HTML,
    )
