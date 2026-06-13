"""
history.py  —  Conversation history API
========================================
All routes require authentication.

GET  /api/conversations
     → [ { id, title, updated_at, message_count } ]

POST /api/conversations
     Body: { title? }
     → { id, title, created_at }

DELETE /api/conversations/{conv_id}
     → { ok: true }

GET  /api/conversations/{conv_id}/messages
     → [ { id, role, content, sources, ts } ]

DELETE /api/conversations/all
     → { deleted: N }
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.services.auth_dep import require_user, UserContext
from app.services.mongo import conversations, messages

router = APIRouter(prefix="/api", tags=["history"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class ConversationOut(BaseModel):
    id:            str
    title:         str
    updated_at:    str
    message_count: int = 0


class MessageOut(BaseModel):
    id:          str
    role:        str
    content:     str
    sources:     list = []
    search_info: Optional[dict] = None
    ts:          str


class NewConvBody(BaseModel):
    title: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ts(dt: datetime) -> str:
    return dt.isoformat() if dt else ""

def _str_id(doc: dict) -> str:
    return str(doc["_id"])


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/conversations", response_model=List[ConversationOut])
async def list_conversations(user: UserContext = Depends(require_user)):
    """Return all conversations for the logged-in user, newest first."""
    cursor = conversations().find(
        {"user_id": user.user_id},
        sort=[("updated_at", -1)],
        limit=100,
    )
    result = []
    async for doc in cursor:
        count = await messages().count_documents({"conv_id": str(doc["_id"])})
        result.append(ConversationOut(
            id=_str_id(doc),
            title=doc.get("title", "New conversation"),
            updated_at=_ts(doc.get("updated_at")),
            message_count=count,
        ))
    return result


@router.post("/conversations", response_model=ConversationOut, status_code=201)
async def create_conversation(
    body: NewConvBody,
    user: UserContext = Depends(require_user),
):
    """Create a new empty conversation."""
    now = datetime.now(timezone.utc)
    doc = {
        "user_id":    user.user_id,
        "title":      body.title or "New conversation",
        "created_at": now,
        "updated_at": now,
    }
    result = await conversations().insert_one(doc)
    return ConversationOut(
        id=str(result.inserted_id),
        title=doc["title"],
        updated_at=_ts(now),
        message_count=0,
    )


@router.delete("/conversations/all")
async def delete_all_conversations(user: UserContext = Depends(require_user)):
    """Delete all conversations and messages for the current user."""
    conv_ids = []
    async for doc in conversations().find({"user_id": user.user_id}, {"_id": 1}):
        conv_ids.append(str(doc["_id"]))

    if conv_ids:
        await messages().delete_many({"conv_id": {"$in": conv_ids}})
    result = await conversations().delete_many({"user_id": user.user_id})
    return {"deleted": result.deleted_count}


@router.delete("/conversations/{conv_id}")
async def delete_conversation(
    conv_id: str,
    user: UserContext = Depends(require_user),
):
    """Delete one conversation and all its messages."""
    try:
        oid = ObjectId(conv_id)
    except Exception:
        raise HTTPException(400, "Invalid conversation id.")

    conv = await conversations().find_one({"_id": oid, "user_id": user.user_id})
    if not conv:
        raise HTTPException(404, "Conversation not found.")

    await messages().delete_many({"conv_id": conv_id})
    await conversations().delete_one({"_id": oid})
    return {"ok": True}


@router.get(
    "/conversations/{conv_id}/messages",
    response_model=List[MessageOut],
)
async def get_messages(
    conv_id: str,
    user: UserContext = Depends(require_user),
):
    """Return all messages for a conversation, oldest first."""
    try:
        oid = ObjectId(conv_id)
    except Exception:
        raise HTTPException(400, "Invalid conversation id.")

    conv = await conversations().find_one({"_id": oid, "user_id": user.user_id})
    if not conv:
        raise HTTPException(404, "Conversation not found.")

    result = []
    async for msg in messages().find(
        {"conv_id": conv_id}, sort=[("ts", 1)]
    ):
        result.append(MessageOut(
            id=_str_id(msg),
            role=msg["role"],
            content=msg["content"],
            sources=msg.get("sources", []),
            search_info=msg.get("search_info"),
            ts=_ts(msg.get("ts")),
        ))
    return result