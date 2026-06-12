"""
process.py  —  /stream  SSE endpoint
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional, List

from bson import ObjectId
from fastapi import APIRouter, Request, Depends
from fastapi.responses import StreamingResponse

from app.services.auth_dep import optional_user, UserContext
from app.services.mongo import conversations, messages
from app.services.memory import get_user_profile, update_user_memory
from app.services.document_store import retrieve_context, session_has_documents
from app.services.llm_service import stream_reply
from app.models.schemas import ProcessRequest, Message

log = logging.getLogger("obelius.process")

router = APIRouter()


# ── helpers ───────────────────────────────────────────────────────────────────

def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def _ensure_conversation(user_id: str, conv_id: Optional[str]) -> str:
    """
    Return an existing conv_id if valid, otherwise create a new conversation
    and return its id. Never returns None.
    """
    if conv_id:
        try:
            doc = await conversations().find_one({
                "_id": ObjectId(conv_id), "user_id": user_id
            })
            if doc:
                return conv_id
        except Exception:
            pass

    now = datetime.now(timezone.utc)
    result = await conversations().insert_one({
        "user_id":    user_id,
        "title":      "New conversation",
        "created_at": now,
        "updated_at": now,
    })
    return str(result.inserted_id)


async def _auto_title(conv_id: str, first_user_message: str) -> None:
    """Set the conversation title from the first user message."""
    try:
        title = first_user_message.strip()[:60]
        if len(first_user_message.strip()) > 60:
            title += "…"
        await conversations().update_one(
            {"_id": ObjectId(conv_id)},
            {"$set": {"title": title}},
        )
    except Exception:
        pass


async def _save_message(
    conv_id: str, user_id: str, role: str, content: str, sources: list
) -> None:
    try:
        await messages().insert_one({
            "conv_id":  conv_id,
            "user_id":  user_id,
            "role":     role,
            "content":  content,
            "sources":  sources,
            "ts":       datetime.now(timezone.utc),
        })
        await conversations().update_one(
            {"_id": ObjectId(conv_id)},
            {"$set": {"updated_at": datetime.now(timezone.utc)}},
        )
    except Exception as exc:
        log.warning("Failed to save message: %s", exc)


async def _load_db_history(conv_id: str) -> List[Message]:
    """Load previous messages for this conversation (oldest first, last 40)."""
    result = []
    async for msg in messages().find(
        {"conv_id": conv_id}, sort=[("ts", 1)], limit=40
    ):
        result.append(Message(role=msg["role"], content=msg["content"]))
    return result


# ── route ─────────────────────────────────────────────────────────────────────

@router.post("/stream")
async def stream(
    request: Request,
    user: Optional[UserContext] = Depends(optional_user),
):
    body = await request.json()

    # session_id used for in-memory document store (guests + logged-in)
    session_id: str = body.get("id", "unknown")

    # conv_id comes from frontend (set after first exchange, or on conversation switch)
    client_conv_id: Optional[str] = body.get("conv_id") or None

    # The raw user message text (used for auto-title and memory)
    raw_message: str = body.get("message", "").strip()

    # ── Logged-in path ────────────────────────────────────────────────────────
    active_conv_id: Optional[str] = None
    user_profile:   str           = ""

    if user:
        # Ensure we always have a valid conversation in DB
        active_conv_id = await _ensure_conversation(user.user_id, client_conv_id)

        # Load user memory for personalisation
        user_profile = await get_user_profile(user.user_id)

        # Build conversation from DB (authoritative — prevents client injection)
        db_history = await _load_db_history(active_conv_id)

        # Append the current user message
        if raw_message:
            db_history.append(Message(role="user", content=raw_message))

        conversation = db_history

        # Save the user message to DB immediately (before streaming starts)
        msg_count = await messages().count_documents({"conv_id": active_conv_id})
        if msg_count == 0 and raw_message:
            # First message — set auto-title
            asyncio.create_task(_auto_title(active_conv_id, raw_message))

        if raw_message:
            await _save_message(
                active_conv_id, user.user_id, "user", raw_message, []
            )

    else:
        # ── Guest path: use client-sent conversation array ────────────────────
        try:
            req = ProcessRequest(**body)
            conversation = req.data
        except Exception:
            conversation = []

    # ── Document context (works for both guests and logged-in users) ──────────
    context_chunks = []
    if session_has_documents(session_id):
        last_user = next(
            (m.content for m in reversed(conversation) if m.role == "user"), ""
        )
        context_chunks = retrieve_context(session_id, last_user)

    sources = [
        {"title": c["filename"], "page": c.get("page"), "chunk": c.get("chunk")}
        for c in context_chunks
    ]

    # ── Stream generator ──────────────────────────────────────────────────────
    async def event_stream():
        accumulated = ""

        try:
            loop = asyncio.get_event_loop()

            def _run_sync():
                return list(stream_reply(conversation, context_chunks, user_profile))

            tokens = await loop.run_in_executor(None, _run_sync)

            for token in tokens:
                accumulated += token
                trimmed = accumulated.strip()

                # Safety short-circuits
                if trimmed in ("IGNORED", "PERSONAL"):
                    yield _sse({"type": "token", "text": token})
                    break

                yield _sse({"type": "token", "text": token})

            # Send sources
            yield _sse({"type": "sources", "sources": sources})

            # Persist AI reply and update memory (logged-in only)
            reply_text = accumulated.strip()
            if user and active_conv_id and reply_text not in ("IGNORED", "PERSONAL") and reply_text:
                await _save_message(
                    active_conv_id, user.user_id, "assistant", accumulated, sources
                )
                asyncio.create_task(
                    update_user_memory(user.user_id, raw_message, accumulated)
                )

            # Always send done with conv_id so frontend can track it
            yield _sse({
                "type":    "done",
                "conv_id": active_conv_id or session_id,
            })

        except Exception as exc:
            log.error("Stream error: %s", exc, exc_info=True)
            yield _sse({"type": "error", "text": "Something went wrong. Please try again."})

    return StreamingResponse(event_stream(), media_type="text/event-stream")