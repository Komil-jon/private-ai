"""
mongo.py  —  Shared async MongoDB client for Obelius
=====================================================
Collections (all in the "eternal_obelius" database):

  conversations  { _id, user_id, title, created_at, updated_at }
  messages       { _id, conv_id, user_id, role, content, sources[], ts }
  user_memory    { _id, user_id, facts[], summary, updated_at }

Call init_db() from the FastAPI lifespan hook.
Access collections via conversations(), messages(), user_memory().
"""

from __future__ import annotations

import os
import logging
from typing import Optional

import motor.motor_asyncio
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("obelius.mongo")

_client:  Optional[motor.motor_asyncio.AsyncIOMotorClient] = None
_db:      Optional[motor.motor_asyncio.AsyncIOMotorDatabase] = None


async def init_db() -> None:
    global _client, _db
    uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    _client = motor.motor_asyncio.AsyncIOMotorClient(
        uri, serverSelectionTimeoutMS=5_000
    )
    await _client.admin.command("ping")
    _db = _client["eternal_obelius"]

    # Indexes
    await _db["conversations"].create_index("user_id")
    await _db["conversations"].create_index("updated_at")
    await _db["messages"].create_index("conv_id")
    await _db["messages"].create_index([("conv_id", 1), ("ts", 1)])
    await _db["user_memory"].create_index("user_id", unique=True)
    await _db["user_settings"].create_index("user_id", unique=True)

    log.info("Obelius MongoDB connected.")


async def close_db() -> None:
    if _client:
        _client.close()
        log.info("Obelius MongoDB closed.")


def _col(name: str) -> motor.motor_asyncio.AsyncIOMotorCollection:
    if _db is None:
        raise RuntimeError("Database not initialised — call init_db() first.")
    return _db[name]


def conversations() -> motor.motor_asyncio.AsyncIOMotorCollection:
    return _col("conversations")


def messages() -> motor.motor_asyncio.AsyncIOMotorCollection:
    return _col("messages")


def user_memory() -> motor.motor_asyncio.AsyncIOMotorCollection:
    return _col("user_memory")


def user_settings() -> motor.motor_asyncio.AsyncIOMotorCollection:
    return _col("user_settings")