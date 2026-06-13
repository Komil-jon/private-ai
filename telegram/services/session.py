"""
session.py — MongoDB-backed Telegram session store.

Collections (same DB as the main backend, "eternal_obelius"):
  tg_sessions:    { telegram_id, jwt_token, user_id, username, full_name, email,
                    active_conv_id, updated_at }
  tg_auth_states: { state (UUID str), telegram_id, created_at }  — TTL 10 min
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import motor.motor_asyncio

from config import MONGO_URI, AUTH_STATE_TTL  # prefix-free: telegram/ is on sys.path

log = logging.getLogger("obelius.tg.session")

_client: Optional[motor.motor_asyncio.AsyncIOMotorClient] = None
_db:     Optional[motor.motor_asyncio.AsyncIOMotorDatabase] = None


def _get_db() -> motor.motor_asyncio.AsyncIOMotorDatabase:
    global _client, _db
    if _db is None:
        _client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
        _db     = _client["eternal_obelius"]
    return _db


def _sessions():
    return _get_db()["tg_sessions"]


def _states():
    return _get_db()["tg_auth_states"]


async def init_indexes() -> None:
    db = _get_db()
    await db["tg_sessions"].create_index("telegram_id", unique=True)
    await db["tg_auth_states"].create_index("telegram_id")
    await db["tg_auth_states"].create_index(
        "created_at", expireAfterSeconds=AUTH_STATE_TTL
    )
    log.info("Telegram session indexes ensured.")


# ── Session CRUD ──────────────────────────────────────────────────────────────

async def get_session(telegram_id: int) -> Optional[dict]:
    return await _sessions().find_one({"telegram_id": telegram_id})


async def save_session(telegram_id: int, jwt: str, user: dict) -> None:
    await _sessions().update_one(
        {"telegram_id": telegram_id},
        {
            "$set": {
                "telegram_id": telegram_id,
                "jwt_token":   jwt,
                "user_id":     user["id"],
                "username":    user.get("username", ""),
                "full_name":   user.get("full_name", ""),
                "email":       user.get("email", ""),
                "updated_at":  datetime.now(timezone.utc),
            },
            "$setOnInsert": {"active_conv_id": None},
        },
        upsert=True,
    )
    log.info("Session saved for telegram_id=%s", telegram_id)


async def clear_session(telegram_id: int) -> None:
    await _sessions().delete_one({"telegram_id": telegram_id})


async def set_active_conv(telegram_id: int, conv_id: Optional[str]) -> None:
    await _sessions().update_one(
        {"telegram_id": telegram_id},
        {"$set": {"active_conv_id": conv_id, "updated_at": datetime.now(timezone.utc)}},
    )


# ── One-time auth state ───────────────────────────────────────────────────────

async def create_auth_state(telegram_id: int) -> str:
    state = uuid.uuid4().hex
    await _states().insert_one({
        "state":       state,
        "telegram_id": telegram_id,
        "created_at":  datetime.now(timezone.utc),
    })
    return state


async def consume_auth_state(state: str) -> Optional[int]:
    """Return telegram_id and delete the state. None if expired/invalid."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=AUTH_STATE_TTL)
    doc = await _states().find_one_and_delete({
        "state":      state,
        "created_at": {"$gte": cutoff},
    })
    return doc["telegram_id"] if doc else None
