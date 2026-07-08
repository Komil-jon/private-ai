"""
mcp_server.py — MCP (Model Context Protocol) server for Obelius
==================================================================
Exposes a read-only subset of Obelius's RAG capabilities — company
document search, knowledge-graph queries, web search, and personal
memory — to MCP clients (Claude Code, Claude Desktop, etc.) over
Streamable HTTP, mounted at /mcp alongside the existing REST API.

Security model
---------------
- Authentication happens in MCPAuthMiddleware (mcp_auth.py), OUTSIDE
  this module, before any JSON-RPC/tool dispatch runs. Every request
  must carry a valid `Authorization: Bearer <eternal_token>` — the same
  token/verification the REST API and the browser frontend use.
- Tools never accept a user_id / company_id / session_id argument from
  the MCP client. Every tool derives the caller's identity from the
  verified token (via ctx) and looks up the caller's OWN company
  assignment in Mongo — exactly like GET /api/user/company does. This
  closes off the obvious cross-tenant IDOR: a client cannot simply pass
  someone else's id to read their data.
- A small per-user in-memory rate limit guards the local LLM /
  embedding / web-search calls each tool triggers from being hammered
  by a runaway or compromised client.
- This surface is read-only (search/query) by design. Upload and
  account mutation stay REST-only, behind the existing UI flows.
"""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict, deque
from typing import Optional

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.services import web_search as ws
from app.services.auth_dep import UserContext
from app.services.companies import get_qdrant_key
from app.services.document_store import retrieve_context_multi
from app.services.graph_store import query_graph
from app.services.memory import get_user_profile
from app.services.mcp_auth import MCPAuthMiddleware
from app.services.mongo import user_settings

log = logging.getLogger("obelius.mcp")

# Comma-separated list of hostnames this server is reachable as (DNS-rebinding
# guard on the streamable-HTTP transport). Must match the Host header real
# requests arrive with in production (set in backend/app/.env — override
# MCP_ALLOWED_HOSTS there whenever a new public domain is added, e.g. a
# custom Render domain like www.obelius.uz).
_ALLOWED_HOSTS = [h.strip() for h in os.getenv("MCP_ALLOWED_HOSTS", "private-ai-web.onrender.com").split(",") if h.strip()]
_ALLOWED_ORIGINS = [f"https://{h}" for h in _ALLOWED_HOSTS]

mcp = FastMCP(
    "Obelius",
    instructions=(
        "Private company knowledge assistant. Tools search the authenticated "
        "caller's own company knowledge base, its knowledge graph, the live "
        "web, and the caller's own remembered profile. Every call requires a "
        "valid eternal_token — there is no guest access."
    ),
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_ALLOWED_HOSTS,
        allowed_origins=_ALLOWED_ORIGINS,
    ),
)


# ── per-user rate limiting ───────────────────────────────────────────────────
_RATE_LIMIT = 20      # tool calls
_RATE_WINDOW = 60.0   # seconds
_calls: dict[str, deque] = defaultdict(deque)


def _rate_limited(user_id: str) -> bool:
    now = time.monotonic()
    q = _calls[user_id]
    while q and now - q[0] > _RATE_WINDOW:
        q.popleft()
    if len(q) >= _RATE_LIMIT:
        return True
    q.append(now)
    return False


# ── identity / authorization ─────────────────────────────────────────────────

def _current_user(ctx: Context) -> Optional[UserContext]:
    req = ctx.request_context.request
    if req is None:
        return None
    return getattr(req.state, "user", None)


async def _authorize(ctx: Context) -> UserContext:
    """
    Every tool calls this first. Raises PermissionError (surfaced to the MCP
    client as a clean tool-error, never a crash) if unauthenticated or over
    the rate limit.

    Defensive-in-depth: MCPAuthMiddleware already rejects unauthenticated
    HTTP requests with a 401 before they reach MCP dispatch, so `user`
    should never actually be None here — this is a second layer in case the
    /mcp mount is ever changed to bypass that middleware.
    """
    user = _current_user(ctx)
    if user is None:
        raise PermissionError("Not authenticated.")
    if _rate_limited(user.user_id):
        raise PermissionError("Rate limit exceeded — try again in a minute.")
    return user


async def _company_qdrant_key(user_id: str) -> Optional[str]:
    """The caller's OWN selected company only — never client-supplied."""
    doc = await user_settings().find_one({"user_id": user_id})
    company_id = doc.get("company_id") if doc else None
    if not company_id:
        return None
    return get_qdrant_key(company_id)


_NO_COMPANY_MSG = (
    "No company is configured for this account yet — sign in at the Obelius "
    "app and select your company first."
)


# ── tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
async def search_company_docs(query: str, ctx: Context) -> str:
    """Semantic search over the authenticated caller's own company knowledge
    base (HR policies, manuals, SOPs, internal guidelines the company has
    uploaded). Returns the top matching excerpts with filename/page/score."""
    user = await _authorize(ctx)
    company_key = await _company_qdrant_key(user.user_id)
    if not company_key:
        return _NO_COMPANY_MSG

    chunks = retrieve_context_multi([company_key], query)
    if not chunks:
        return "No relevant company documents found for that query."

    lines = []
    for c in chunks:
        page = f", page {c['page']}" if c.get("page") else ""
        lines.append(f"[{c['filename']}{page}, score {c['score']}]\n{c['text'].strip()}")
    return "\n\n".join(lines)


@mcp.tool()
async def query_knowledge_graph(query: str, ctx: Context) -> str:
    """Query the entity-relationship knowledge graph extracted from the
    authenticated caller's own company documents (e.g. reporting lines,
    ownership, dependencies)."""
    user = await _authorize(ctx)
    company_key = await _company_qdrant_key(user.user_id)
    if not company_key:
        return _NO_COMPANY_MSG

    result = query_graph(company_key, query)
    return result or "No relevant relationships found in the knowledge graph."


@mcp.tool()
async def web_search(query: str, ctx: Context) -> str:
    """Live web search (DuckDuckGo) with page content fetched for the top
    results. Use for anything requiring current or external information the
    company knowledge base wouldn't have."""
    await _authorize(ctx)
    results = ws.search(query, max_results=5)
    if not results:
        return "No web results found."
    results = ws.enrich_with_content(results, max_pages=2)

    lines = []
    for r in results:
        body = r.get("content") or r.get("snippet", "")
        lines.append(f"[{r.get('title', 'Untitled')}]({r.get('url', '')})\n{body}")
    return "\n\n".join(lines)


@mcp.tool()
async def get_my_memory_profile(ctx: Context) -> str:
    """Return what Obelius has learned/remembered about the authenticated
    caller across past conversations (facts + summary)."""
    user = await _authorize(ctx)
    profile = await get_user_profile(user.user_id)
    return profile or "No memory stored for this account yet."


# Auth-gated ASGI app — this is what main.py mounts at /mcp. The middleware
# runs before any MCP session/tool code, so unauthenticated traffic never
# reaches the JSON-RPC layer.
mcp_asgi_app = MCPAuthMiddleware(mcp.streamable_http_app())
