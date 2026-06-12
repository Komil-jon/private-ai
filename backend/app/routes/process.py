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
from app.services.llm_service import stream_reply, rewrite_search_query
from app.services import web_search as ws
from app.models.schemas import ProcessRequest, Message

log = logging.getLogger("obelius.process")

router = APIRouter()

# Signals that indicate the user wants/needs live information
_WEB_SIGNALS = [
    # explicit requests
    "search", "look up", "find online", "browse", "google",
    # time-awareness
    "what time", "what date", "what day", "today", "right now", "currently",
    "current time", "current date", "this year", "this month", "this week",
    # recency / news
    "latest", "recent", "news", "just happened", "just released",
    "new version", "update", "announced", "breaking",
    # weather
    "weather", "temperature", "forecast",
    # prices / markets
    "price of", "stock price", "exchange rate", "bitcoin", "crypto",
]


def _needs_web_search(query: str, has_doc_context: bool) -> bool:
    """
    Return True when the query should be enriched with live web results.
    - Always True when no document context was found for this query.
    - True even with docs when the query clearly needs real-time info.
    """
    if not has_doc_context:
        return True
    q = query.lower()
    return any(signal in q for signal in _WEB_SIGNALS)


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

    # ── Document context ──────────────────────────────────────────────────────
    # Key: use conv_id when available so docs are scoped to this conversation only.
    doc_key = active_conv_id if active_conv_id else session_id
    context_chunks = []
    if session_has_documents(doc_key):
        last_user = next(
            (m.content for m in reversed(conversation) if m.role == "user"), ""
        )
        context_chunks = retrieve_context(doc_key, last_user)

    doc_sources = [
        {
            "type":  "doc",
            "title": c["filename"],
            "page":  c.get("page"),
            "chunk": c.get("chunk"),
            "score": c.get("score"),   # semantic similarity — proof Qdrant is active
        }
        for c in context_chunks
    ]

    # ── Agentic search pipeline ───────────────────────────────────────────────
    # Step 1: LLM rewrites the user message into an optimised search query.
    # Step 2: DuckDuckGo search with the optimised query.
    # Step 3: stream_reply (below) synthesises the final answer from all context.
    web_results      = []
    web_attempted    = False
    search_query     = raw_message
    if raw_message and _needs_web_search(raw_message, bool(context_chunks)):
        web_attempted = True
        loop_ref = asyncio.get_running_loop()
        search_query = await loop_ref.run_in_executor(
            None, lambda: rewrite_search_query(raw_message)
        )
        web_results = await loop_ref.run_in_executor(
            None, lambda: ws.search(search_query, max_results=5, original_query=raw_message)
        )
        log.info("Agentic search: original=%r rewritten=%r results=%d",
                 raw_message[:60], search_query, len(web_results))

    web_sources = [
        {"type": "web", "title": r.get("title", "Web result"), "url": r.get("url", "")}
        for r in web_results
    ]

    # When web search is the primary answer, only cite doc chunks that are
    # highly relevant (score ≥ 0.70) — prevents irrelevant document chips
    # from cluttering the response alongside web results.
    if web_results:
        shown_doc_sources = [s for s in doc_sources if (s.get("score") or 0) >= 0.70]
    else:
        shown_doc_sources = doc_sources

    all_sources = shown_doc_sources + web_sources

    # ── Stream generator ──────────────────────────────────────────────────────
    current_utc = datetime.now(timezone.utc).strftime("%A %d %B %Y, %H:%M UTC")

    async def event_stream():
        accumulated = ""

        try:
            # Always emit search debug info first so UI can show it immediately
            yield _sse({
                "type":          "search_info",
                "triggered":     web_attempted,
                "query":         search_query if web_attempted else None,
                "results_count": len(web_results),
            })

            loop = asyncio.get_running_loop()

            def _run_sync():
                return list(stream_reply(
                    conversation, context_chunks, user_profile,
                    web_results, current_utc, web_attempted,
                ))

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
            yield _sse({"type": "sources", "sources": all_sources})

            # Persist AI reply and update memory (logged-in only)
            reply_text = accumulated.strip()
            if user and active_conv_id and reply_text not in ("IGNORED", "PERSONAL") and reply_text:
                await _save_message(
                    active_conv_id, user.user_id, "assistant", accumulated, all_sources
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