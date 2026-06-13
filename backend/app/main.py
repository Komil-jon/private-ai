from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os

from app.routes.process import router as process_router
from app.routes.upload  import router as upload_router
from app.routes.history import router as history_router
from app.services.mongo import init_db, close_db
from app.services.document_store import init_docstore

BASE_DIR     = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
UPLOAD_DIR   = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await init_db()
    import asyncio, concurrent.futures
    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        await loop.run_in_executor(pool, init_docstore)
    yield
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