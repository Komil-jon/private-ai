"""
mcp_auth.py — Bearer-token authentication for the /mcp endpoint
==================================================================
Pure-ASGI middleware (deliberately NOT Starlette's BaseHTTPMiddleware,
which buffers/wraps responses in a way that is unsafe for the SSE
streams the MCP streamable-HTTP transport uses).

Every MCP request must carry:
    Authorization: Bearer <eternal_token>
Verified against the exact same auth.eternal.uz check the REST API uses
(auth_dep.verify_token) — one source of truth for what counts as a valid
session. Unlike the REST dependency, this does NOT fall back to reading
a cookie: MCP clients are not browsers, so only the explicit Bearer
header (per RFC 6750 / the MCP Authorization spec) is honoured.

On success the verified UserContext is attached to the ASGI scope
(`scope["state"]["user"]`) so tools can read the caller's identity —
tools must never trust a user/company id passed as a tool argument.
On failure the request is rejected with 401 before it ever reaches MCP
session/tool dispatch.
"""

from __future__ import annotations

import json
import logging

from app.services.auth_dep import verify_token

log = logging.getLogger("obelius.mcp_auth")


class MCPAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        token = _extract_bearer(scope)
        user = await verify_token(token) if token else None

        if user is None:
            await _send_401(send)
            return

        scope.setdefault("state", {})["user"] = user
        await self.app(scope, receive, send)


def _extract_bearer(scope) -> str | None:
    for name, value in scope.get("headers", ()):
        if name == b"authorization":
            raw = value.decode("latin-1")
            if raw.startswith("Bearer "):
                return raw[7:].strip()
            return None
    return None


async def _send_401(send) -> None:
    body = json.dumps({
        "error": "unauthorized",
        "detail": "A valid 'Authorization: Bearer <eternal_token>' header is required.",
    }).encode()
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [
            (b"content-type", b"application/json"),
            (b"www-authenticate", b"Bearer"),
        ],
    })
    await send({"type": "http.response.body", "body": body})
