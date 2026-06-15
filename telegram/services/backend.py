"""
backend.py — HTTP client for the Obelius FastAPI backend.
Consumes SSE streams and yields parsed event dicts to handlers.
"""
from __future__ import annotations

import json
import logging
from typing import AsyncGenerator, Optional

import httpx

from config import BACKEND_URL, AUTH_BASE_URL  # prefix-free

log = logging.getLogger("obelius.tg.backend")

_http: Optional[httpx.AsyncClient] = None


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(timeout=120.0)
    return _http


# ── Auth ──────────────────────────────────────────────────────────────────────

async def verify_token(jwt: str) -> Optional[dict]:
    try:
        resp = await _client().post(
            f"{AUTH_BASE_URL}/api/verify",
            json={"token": jwt},
            timeout=10.0,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data.get("user") if data.get("valid") else None
    except Exception as exc:
        log.warning("Token verify failed: %s", exc)
        return None


# ── Chat (SSE stream) ─────────────────────────────────────────────────────────

async def stream_chat(
    jwt: str,
    session_key: str,
    conv_id: Optional[str],
    message: str,
    company_id: Optional[str] = None,
) -> AsyncGenerator[dict, None]:
    """Open the backend SSE /stream endpoint and yield parsed event dicts."""
    headers = {"Authorization": f"Bearer {jwt}"}
    payload = {"message": message, "id": session_key, "conv_id": conv_id, "data": []}
    if company_id:
        payload["company_id"] = company_id
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST", f"{BACKEND_URL}/stream",
                json=payload, headers=headers,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            yield json.loads(line[6:])
                        except json.JSONDecodeError:
                            pass
    except Exception as exc:
        log.error("stream_chat error: %s", exc)
        yield {"type": "error", "text": "Connection to AI backend failed."}


# ── File upload ───────────────────────────────────────────────────────────────

async def upload_file(
    jwt: str,
    session_key: str,
    conv_id: Optional[str],
    filename: str,
    content: bytes,
) -> dict:
    headers = {"Authorization": f"Bearer {jwt}"}
    files   = [("files", (filename, content, "application/octet-stream"))]
    data    = {"session_id": session_key}
    if conv_id:
        data["conv_id"] = conv_id
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{BACKEND_URL}/upload", files=files, data=data, headers=headers
        )
        resp.raise_for_status()
        return resp.json()


# ── Conversations ─────────────────────────────────────────────────────────────

async def list_conversations(jwt: str) -> list:
    headers = {"Authorization": f"Bearer {jwt}"}
    resp = await _client().get(f"{BACKEND_URL}/api/conversations", headers=headers)
    resp.raise_for_status()
    return resp.json()


async def get_messages(jwt: str, conv_id: str) -> list:
    headers = {"Authorization": f"Bearer {jwt}"}
    resp = await _client().get(
        f"{BACKEND_URL}/api/conversations/{conv_id}/messages", headers=headers
    )
    resp.raise_for_status()
    return resp.json()


async def delete_conversation(jwt: str, conv_id: str) -> None:
    headers = {"Authorization": f"Bearer {jwt}"}
    await _client().delete(f"{BACKEND_URL}/api/conversations/{conv_id}", headers=headers)


async def delete_all_conversations(jwt: str) -> int:
    headers = {"Authorization": f"Bearer {jwt}"}
    resp = await _client().delete(f"{BACKEND_URL}/api/conversations/all", headers=headers)
    return resp.json().get("deleted", 0)


# ── Company ───────────────────────────────────────────────────────────────────

async def get_companies() -> list:
    resp = await _client().get(f"{BACKEND_URL}/api/companies")
    resp.raise_for_status()
    return resp.json()


async def get_user_company(jwt: str) -> dict:
    headers = {"Authorization": f"Bearer {jwt}"}
    resp = await _client().get(f"{BACKEND_URL}/api/user/company", headers=headers)
    resp.raise_for_status()
    return resp.json()


async def set_user_company(jwt: str, company_id: str) -> dict:
    headers = {"Authorization": f"Bearer {jwt}"}
    resp = await _client().post(
        f"{BACKEND_URL}/api/user/company",
        json={"company_id": company_id},
        headers=headers,
    )
    resp.raise_for_status()
    return resp.json()


async def create_conversation(jwt: str, title: str = "New conversation") -> dict:
    headers = {"Authorization": f"Bearer {jwt}"}
    resp = await _client().post(
        f"{BACKEND_URL}/api/conversations", json={"title": title}, headers=headers
    )
    resp.raise_for_status()
    return resp.json()
