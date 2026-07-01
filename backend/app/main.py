from __future__ import annotations

import sys
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import httpx

from app.routes.process  import router as process_router
from app.routes.upload   import router as upload_router
from app.routes.history  import router as history_router
from app.routes.company  import router as company_router
from app.services.mongo  import init_db, close_db
from app.services.document_store import init_docstore, ingest_company_docs
from app.services.graph_store import init_graph
from app.services.companies import COMPANIES
from app.mcp_server import mcp, mcp_asgi_app

BASE_DIR     = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
DOCS_DIR     = os.path.join(BASE_DIR, "docs")

# Add telegram/ dir to sys.path so our bot modules resolve without the 'telegram.' prefix.
# This avoids shadowing the python-telegram-bot library (our dir has no __init__.py).
_TELEGRAM_DIR = os.path.join(BASE_DIR, "telegram")
if _TELEGRAM_DIR not in sys.path:
    sys.path.insert(0, _TELEGRAM_DIR)

BOT_MODE  = os.getenv("BOT_MODE", "polling")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    import logging as _log
    _logger = _log.getLogger("obelius.main")

    await init_db()
    import asyncio, concurrent.futures
    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        await loop.run_in_executor(pool, init_docstore)
        # Ingest docs for every registered company (skips already-ingested files)
        for _cid, _cinfo in COMPANIES.items():
            _subdir = _cinfo["docs_subdir"]
            _cdir   = os.path.join(DOCS_DIR, _subdir) if _subdir else DOCS_DIR
            _ckey   = _cinfo["qdrant_key"]
            await loop.run_in_executor(
                pool, lambda d=_cdir, k=_ckey: ingest_company_docs(d, k)
            )
    try:
        await loop.run_in_executor(None, init_graph)
    except Exception as exc:
        _logger.warning("Neo4j init skipped: %s", exc)

    # Initialise Telegram session indexes regardless of bot mode
    if BOT_TOKEN:
        try:
            from services.session import init_indexes   # telegram/ is on sys.path
            await init_indexes()
        except Exception as exc:
            _logger.warning("Telegram session init skipped: %s", exc)

    # Webhook mode: start bot application and register webhook with Telegram
    if BOT_MODE == "webhook" and BOT_TOKEN:
        try:
            from telegram import BotCommandScopeAllPrivateChats
            from bot import get_application, _COMMANDS   # our bot, telegram/ on sys.path
            from config import WEBHOOK_URL, WEBHOOK_SECRET, WEBHOOK_PATH

            tg_app = await get_application()
            await tg_app.bot.set_my_commands(
                _COMMANDS, scope=BotCommandScopeAllPrivateChats()
            )
            webhook_url = f"{WEBHOOK_URL}{WEBHOOK_PATH}"
            await tg_app.bot.set_webhook(
                url=webhook_url,
                secret_token=WEBHOOK_SECRET,
                allowed_updates=["message", "callback_query", "inline_query"],
                drop_pending_updates=True,
            )
            _logger.info("Telegram webhook registered: %s", webhook_url)
        except Exception as exc:
            _logger.error("Telegram webhook setup failed: %s", exc)

    # MCP streamable-HTTP transport needs its session manager's task group
    # running for the lifetime of the app.
    async with mcp.session_manager.run():
        yield

    # Webhook mode: clean up bot application
    if BOT_MODE == "webhook" and BOT_TOKEN:
        try:
            from bot import shutdown_application
            await shutdown_application()
        except Exception:
            pass

    await close_db()


app = FastAPI(title="Obelius", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten in production to your domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

app.include_router(process_router)
app.include_router(upload_router)
app.include_router(history_router)
app.include_router(company_router)

# MCP server — every request is auth-gated inside mcp_asgi_app itself (see
# mcp_server.py / mcp_auth.py) via Bearer token, independent of the
# CORSMiddleware above (which only affects browser fetches, not MCP clients).
app.mount("/mcp", mcp_asgi_app)

# Telegram bot support routes (login redirect, auth callback, webhook)
from app.telegram_router import router as telegram_router
app.include_router(telegram_router)


@app.post("/api/auth-verify")
async def auth_verify(request: Request) -> Response:
    """Proxy to auth.eternal.uz — avoids browser CORS restrictions."""
    body = await request.body()
    async with httpx.AsyncClient(timeout=10.0) as client:
        upstream = await client.post(
            "https://auth.eternal.uz/api/verify",
            content=body,
            headers={"Content-Type": "application/json"},
        )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )


@app.get("/")
async def serve_index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


@app.get("/activate")
async def activate():
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse("Activation successful!", status_code=200)