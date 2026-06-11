"""
process.py
----------
Two routes:

  POST /process  — kept for safety-filter short-circuits (IGNORED / PERSONAL)
                   that need to return before any streaming starts.
                   Also used as a non-streaming fallback.

  GET  /stream   — SSE stream. Query params: session_id.
                   Body is sent as JSON in the request body via POST with
                   content-type application/json.
                   Emits three event types:
                     data: {"type":"token",   "text":"..."}
                     data: {"type":"sources", "sources":[...]}
                     data: {"type":"done"}
                   On error:
                     data: {"type":"error",   "text":"..."}
"""

import json
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from app.models.schemas import ProcessRequest, ProcessResponse, SourceChip
from app.services.llm_service import stream_reply, generate_reply
from app.services.document_store import retrieve_context, session_has_documents

router = APIRouter()


# ── helpers ──────────────────────────────────────────────────────────────────

def _build_sources(context_chunks):
    seen    = set()
    sources = []
    for chunk in context_chunks:
        key = (chunk["filename"], chunk["page"])
        if key not in seen:
            seen.add(key)
            sources.append(SourceChip(
                title=chunk["filename"],
                page=chunk["page"] if chunk["page"] else None,
                chunk=chunk["chunk"],
            ))
    return sources


def _sse(obj: dict) -> str:
    """Format a dict as a single SSE data line."""
    return f"data: {json.dumps(obj)}\n\n"


# ── SSE streaming route ───────────────────────────────────────────────────────

@router.post("/stream")
async def stream_chat(request: ProcessRequest):
    """
    Primary chat endpoint. Returns a text/event-stream SSE response.
    The frontend connects with fetch() + ReadableStream.
    """
    conversation = request.data
    session_id   = request.id

    # Safety filters — short-circuit before opening the stream
    last_user_msg = conversation[-1].content.lower()

    if "password" in last_user_msg or "hack" in last_user_msg:
        # Return a tiny SSE stream that just delivers IGNORED then closes
        async def ignored_stream():
            yield _sse({"type": "token",  "text": "IGNORED"})
            yield _sse({"type": "done"})
        return StreamingResponse(ignored_stream(), media_type="text/event-stream")

    if "who am i" in last_user_msg or "my name" in last_user_msg:
        async def personal_stream():
            yield _sse({"type": "token",  "text": "PERSONAL"})
            yield _sse({"type": "done"})
        return StreamingResponse(personal_stream(), media_type="text/event-stream")

    # RAG context
    context_chunks = []
    if session_has_documents(session_id):
        context_chunks = retrieve_context(session_id, last_user_msg)

    sources = _build_sources(context_chunks)

    # Generator that streams tokens then appends sources + done
    def event_generator():
        try:
            for token in stream_reply(conversation, context_chunks):
                yield _sse({"type": "token", "text": token})

            # After all tokens, send sources
            yield _sse({
                "type":    "sources",
                "sources": [s.model_dump() for s in sources],
            })
            yield _sse({"type": "done"})

        except Exception as e:
            print(f"[stream] Error: {e}")
            yield _sse({"type": "error", "text": "Something went wrong."})
            yield _sse({"type": "done"})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":   "no-cache",
            "X-Accel-Buffering": "no",   # disables nginx buffering if behind a proxy
        },
    )


# ── non-streaming fallback (kept for compatibility) ──────────────────────────

@router.post("/process", response_model=ProcessResponse)
async def process_chat(request: ProcessRequest):
    conversation = request.data
    session_id   = request.id

    last_user_msg = conversation[-1].content.lower()

    if "password" in last_user_msg or "hack" in last_user_msg:
        return ProcessResponse(response="IGNORED")

    if "who am i" in last_user_msg or "my name" in last_user_msg:
        return ProcessResponse(response="PERSONAL")

    context_chunks = []
    if session_has_documents(session_id):
        context_chunks = retrieve_context(session_id, last_user_msg)

    reply   = generate_reply(conversation, context_chunks)
    sources = _build_sources(context_chunks)

    return ProcessResponse(response=reply, sources=sources)