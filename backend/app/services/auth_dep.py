"""
auth_dep.py  —  FastAPI dependency for optional user authentication
===================================================================
Checks for a JWT in:
  1. Authorization: Bearer <token>  header
  2. Cookie: eternal_token=<token>

Validates by calling POST https://auth.eternal.uz/api/verify.
Returns a UserContext if valid, None if the user is not logged in.
Raises HTTP 401 only on routes that use require_user().

Usage in routes:
    from app.services.auth_dep import optional_user, require_user, UserContext

    # Works for both guests and logged-in users:
    async def my_route(user: Optional[UserContext] = Depends(optional_user)):
        ...

    # Requires login:
    async def my_route(user: UserContext = Depends(require_user)):
        ...
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import Optional

import httpx
from fastapi import Depends, HTTPException, Request
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("obelius.auth")

AUTH_BASE = os.getenv("AUTH_BASE_URL", "https://auth.eternal.uz")
_VERIFY_URL = f"{AUTH_BASE}/api/verify"

# Reuse a single async HTTP client for all verify calls
_http: Optional[httpx.AsyncClient] = None


def get_http() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(timeout=5.0)
    return _http


@dataclass
class UserContext:
    user_id:   str
    username:  str
    email:     str
    full_name: str


def _extract_token(request: Request) -> Optional[str]:
    """Pull JWT from Authorization header or eternal_token cookie."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return request.cookies.get("eternal_token")


async def optional_user(request: Request) -> Optional[UserContext]:
    """
    Dependency: returns UserContext if token is valid, None otherwise.
    Never raises — guests pass through as None.
    """
    token = _extract_token(request)
    if not token:
        return None
    try:
        resp = await get_http().post(_VERIFY_URL, json={"token": token})
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not data.get("valid"):
            return None
        u = data["user"]
        return UserContext(
            user_id=u["id"],
            username=u["username"],
            email=u["email"],
            full_name=u.get("full_name", ""),
        )
    except Exception as exc:
        log.warning("Auth verify failed: %s", exc)
        return None


async def require_user(
    user: Optional[UserContext] = Depends(optional_user),
) -> UserContext:
    """Dependency: raises 401 if user is not authenticated."""
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user