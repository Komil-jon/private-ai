"""
process.py  —  /stream  SSE endpoint
======================================
Handles chat requests with streaming.

For logged-in users:
  - Loads/creates a conversation in MongoDB
  - Injects user profile (memory) into the system prompt
  - Saves each exchange (user message + AI reply) to MongoDB
  - Triggers background memory update after each reply

For guests:
  - Works exactly as before (session-based, in-memory document store)
  - No persistence, no memory
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
from app.models.schemas import ProcessRequest

log = logging.getLogger("obelius.process")

router = APIRouter()


# ── helpers ───────────────────────────────────────────────────────────────────

def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def _ensure_conversation(user_id: str, conv_id: Optional[str]) -> str:
    """
    Return conv_id.  Creates a new conversation if conv_id is None or invalid.
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

    # Create a fresh conversation
    now = datetime.now(timezone.utc)
    result = await conversations().insert_one({
        "user_id":    user_id,
        "title":      "New conversation",
        "created_at": now,
        "updated_at": now,
    })
    return str(result.inserted_id)


async def _auto_title(conv_id: str, first_user_message: str) -> None:
    """Set the conversation title from the first user message (async, best-effort)."""
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


async def _save_message(conv_id: str, user_id: str, role: str, content: str, sources: list) -> None:
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


async def _load_history_messages(conv_id: str) -> List[dict]:
    """Load previous messages for this conversation to give the AI context."""
    result = []
    async for msg in messages().find(
        {"conv_id": conv_id}, sort=[("ts", 1)], limit=40
    ):
        result.append({"role": msg["role"], "content": msg["content"]})
    return result


# ── main route ────────────────────────────────────────────────────────────────

@router.post("/stream")
async def stream(
    request:    Request,
    user:       Optional[UserContext] = Depends(optional_user),
):
    body = await request.json()
    req  = ProcessRequest(**body)

    # conv_id is passed by the frontend for logged-in users
    conv_id: Optional[str] = body.get("conv_id")

    # ── For logged-in users: set up conversation and load memory ─────────────
    active_conv_id: Optional[str] = None
    user_profile:   str = ""
    is_new_conv:    bool = False

    if user:
        active_conv_id = await _ensure_conversation(user.user_id, conv_id)
        is_new_conv    = (conv_id != active_conv_id)

        # Load user profile for personalisation
        user_profile = await get_user_profile(user.user_id)

        # Replace conversation data from DB (authoritative) instead of the
        # client-sent array — prevents message injection
        db_history = await _load_history_messages(active_conv_id)
        if db_history:
            from schemas import Message
            req.data = [Message(role=m["role"], content=m["content"]) for m in db_history]
            # Re-append the latest user message if not already in DB
            latest = req.data[-1] if req.data else None
            if not latest or latest.role != "user":
                from schemas import Message
                req.data.append(Message(role="user", content=body.get("message", "")))

    # ── Retrieve document context (works for both guests and users) ───────────
    context_chunks = []
    if session_has_documents(req.id):
        last_user = next(
            (m.content for m in reversed(req.data) if m.role == "user"), ""
        )
        context_chunks = retrieve_context(req.id, last_user)

    # ── Extract the latest user message text ─────────────────────────────────
    latest_user_msg = next(
        (m.content for m in reversed(req.data) if m.role == "user"), ""
    )

    # ── Save user message to DB ───────────────────────────────────────────────
    if user and active_conv_id and latest_user_msg:
        # Auto-title on first message
        msg_count = await messages().count_documents({"conv_id": active_conv_id})
        if msg_count == 0:
            asyncio.create_task(_auto_title(active_conv_id, latest_user_msg))

        await _save_message(
            active_conv_id, user.user_id, "user", latest_user_msg, []
        )

    # ── Stream generator ──────────────────────────────────────────────────────
    async def event_stream():
        accumulated = ""
        sources     = [
            {"title": c["filename"], "page": c.get("page"), "chunk": c.get("chunk")}
            for c in context_chunks
        ]

        try:
            # Run synchronous generator in a thread so we don't block the loop
            loop = asyncio.get_event_loop()

            def _sync_gen():
                return list(stream_reply(req.data, context_chunks, user_profile))

            tokens = await loop.run_in_executor(None, _sync_gen)

            for token in tokens:
                accumulated += token

                # Safety short-circuits — check after accumulating a bit
                trimmed = accumulated.strip()
                if trimmed in ("IGNORED", "PERSONAL"):
                    yield _sse({"type": "token", "text": token})
                    break

                yield _sse({"type": "token", "text": token})

            # Send sources event
            yield _sse({"type": "sources", "sources": sources})

            # Persist AI reply to DB
            if user and active_conv_id and accumulated.strip() not in ("IGNORED", "PERSONAL"):
                await _save_message(
                    active_conv_id, user.user_id, "assistant", accumulated, sources
                )
                # Background memory update — don't await, fire and forget
                asyncio.create_task(
                    update_user_memory(user.user_id, latest_user_msg, accumulated)
                )

            # Send conv_id so frontend can track the conversation
            yield _sse({
                "type":    "done",
                "conv_id": active_conv_id or req.id,
            })

        except Exception as exc:
            log.error("Stream error: %s", exc)
            yield _sse({"type": "error", "text": "Something went wrong."})

    return StreamingResponse(event_stream(), media_type="text/event-stream")