"""
telegram_router.py — FastAPI routes that support the Telegram bot.

  GET  /telegram/login     → redirect user to auth.eternal.uz with state
  GET  /telegram/callback  → auth.eternal.uz posts JWT back here; stores session
  POST /telegram/webhook   → Telegram sends bot updates (webhook mode only)
  GET  /telegram/status    → health check

NOTE ON IMPORTS:
  Our telegram/ directory has no __init__.py so it doesn't shadow the
  python-telegram-bot library. We add it to sys.path explicitly before
  importing our own modules (imported without 'telegram.' prefix).
"""
from __future__ import annotations

import logging
import os
import sys

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

log = logging.getLogger("obelius.tg.router")

router = APIRouter(prefix="/telegram", tags=["telegram"])

# Add telegram/ to sys.path FIRST so config.py (which calls load_dotenv) is importable.
# This must happen before reading any env vars so that the .env values are in os.environ.
_TELEGRAM_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "telegram")
)
if _TELEGRAM_DIR not in sys.path:
    sys.path.insert(0, _TELEGRAM_DIR)

# Import from telegram config — this triggers load_dotenv() and populates os.environ.
try:
    from config import BOT_TOKEN, BOT_MODE, WEBHOOK_SECRET, AUTH_BASE_URL, BACKEND_URL, WEBHOOK_URL
except ImportError:
    BOT_TOKEN      = os.getenv("BOT_TOKEN",      "")
    BOT_MODE       = os.getenv("BOT_MODE",       "polling")
    WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
    AUTH_BASE_URL  = os.getenv("AUTH_BASE_URL",  "https://auth.eternal.uz")
    BACKEND_URL    = os.getenv("BACKEND_URL",    "http://localhost:8001")
    WEBHOOK_URL    = os.getenv("WEBHOOK_URL",    "")


# ── Login redirect ────────────────────────────────────────────────────────────

@router.get("/login")
async def telegram_login(state: str) -> RedirectResponse:
    """
    Step 2 of auth flow: redirect user's browser to auth.eternal.uz.
    auth.eternal.uz appends ?token=<jwt> on its redirect back to us.
    """
    callback_url = f"{BACKEND_URL}/telegram/callback?state={state}"
    login_url    = f"{AUTH_BASE_URL}?redirect={callback_url}&prompt=login"
    return RedirectResponse(url=login_url)


# ── Auth callback ─────────────────────────────────────────────────────────────

_SUCCESS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Obelius — Logged In</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{display:flex;align-items:center;justify-content:center;
        min-height:100vh;background:#0a0a0f;font-family:Poppins,sans-serif;color:#ffe4b5;
        padding:1rem}}
  .card{{text-align:center;max-width:340px;width:100%}}
  .icon{{font-size:3.5rem;margin-bottom:1rem}}
  h1{{font-size:1.4rem;margin-bottom:.4rem}}
  .name{{font-size:1.1rem;font-weight:700;color:#fff;margin:.3rem 0}}
  .email{{font-size:.85rem;color:rgba(255,228,181,.6)}}
  .hint{{margin-top:1.8rem;font-size:.82rem;color:rgba(255,228,181,.5)}}
  .bar{{margin-top:1.4rem;height:4px;border-radius:2px;background:rgba(255,228,181,.15);overflow:hidden}}
  .bar-fill{{height:100%;width:0;background:#ffe4b5;animation:fill 1.8s ease forwards}}
  @keyframes fill{{to{{width:100%}}}}
</style>
</head>
<body>
<div class="card">
  <div class="icon">✅</div>
  <h1>Logged in!</h1>
  <p class="name">{name}</p>
  <p class="email">{email}</p>
  <p class="hint">Closing in a moment…</p>
  <div class="bar"><div class="bar-fill"></div></div>
</div>
<script>
  // If opened as a Telegram Mini App, close the sheet after the animation.
  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) {{
    tg.ready();
    tg.expand();
    setTimeout(() => tg.close(), 1800);
  }}
</script>
</body>
</html>"""

_ERROR_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Login Failed</title>
<style>body{{font-family:sans-serif;display:flex;align-items:center;
justify-content:center;min-height:100vh;background:#0a0a0f;color:#ffe4b5;padding:1rem}}
.card{{text-align:center}}.icon{{font-size:3rem;margin-bottom:1rem}}</style>
</head>
<body><div class="card"><div class="icon">❌</div><p>{msg}</p></div></body>
</html>"""


@router.get("/callback")
async def telegram_callback(
    request: Request,
    state:         str | None = None,
    token:         str | None = None,
    eternal_token: str | None = None,
) -> HTMLResponse:
    """
    Step 4 of auth flow — auth.eternal.uz redirects here after successful login.
    Expected query params: ?state=<uuid>&token=<jwt>
    """
    jwt = token or eternal_token
    if not jwt or not state:
        return HTMLResponse(_ERROR_HTML.format(msg="Missing token or state."), status_code=400)

    try:
        from services.session import consume_auth_state, save_session
        from services.backend import verify_token
    except ImportError as exc:
        log.error("Telegram services import failed: %s", exc)
        return HTMLResponse(_ERROR_HTML.format(msg="Server error — please try again."), status_code=500)

    telegram_id = await consume_auth_state(state)
    if not telegram_id:
        return HTMLResponse(
            _ERROR_HTML.format(msg="Login link expired or already used. Use /login in Telegram again."),
            status_code=400,
        )

    user = await verify_token(jwt)
    if not user:
        return HTMLResponse(_ERROR_HTML.format(msg="Token verification failed."), status_code=401)

    await save_session(telegram_id, jwt, user)

    # Send proactive login confirmation to the Telegram chat with the main menu keyboard.
    # Then, if no company is selected yet, follow up with the company picker.
    if BOT_TOKEN:
        name  = user.get("full_name") or user.get("username") or "there"
        email = user.get("email") or ""
        main_menu_keyboard = {
            "keyboard": [
                [{"text": "💬 New Chat"}, {"text": "📋 History"}],
                [{"text": "📎 Upload File"}, {"text": "🧠 My Memory"}],
                [{"text": "👤 Account"}, {"text": "❓ Help"}],
            ],
            "resize_keyboard": True,
            "is_persistent":   True,
        }
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id":      telegram_id,
                        "text": (
                            f"✅ <b>Logged in as {name}!</b>"
                            + (f"\n<code>{email}</code>" if email else "")
                            + "\n\nYou're all set — all features are now available.\n"
                            "Start chatting or pick an option below."
                        ),
                        "parse_mode":   "HTML",
                        "reply_markup": main_menu_keyboard,
                    },
                )
                if resp.status_code != 200:
                    log.warning("sendMessage returned %s: %s", resp.status_code, resp.text)

                # Check if a company is already selected; if not, send the picker
                try:
                    company_resp = await client.get(
                        f"{BACKEND_URL}/api/user/company",
                        headers={"Authorization": f"Bearer {jwt}"},
                        timeout=5.0,
                    )
                    company_data = company_resp.json() if company_resp.status_code == 200 else {}
                except Exception:
                    company_data = {}

                if not company_data.get("company_id"):
                    try:
                        companies_resp = await client.get(
                            f"{BACKEND_URL}/api/companies", timeout=5.0
                        )
                        companies = companies_resp.json() if companies_resp.status_code == 200 else []
                    except Exception:
                        companies = []

                    if companies:
                        email_domain = email.split("@")[1].lower() if "@" in email else ""
                        inline_buttons = []
                        for company in companies:
                            is_match = email_domain and email_domain == company.get("domain", "").lower()
                            label    = ("⭐ " if is_match else "🏢 ") + company["name"]
                            if is_match:
                                label += " (Suggested)"
                            inline_buttons.append([{
                                "text":          label,
                                "callback_data": f"company:{company['id']}",
                            }])

                        await client.post(
                            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                            json={
                                "chat_id":    telegram_id,
                                "text":       (
                                    "🏢 <b>One more step — select your company</b>\n\n"
                                    "Choose the company you work for to access "
                                    "the right knowledge base:"
                                ),
                                "parse_mode": "HTML",
                                "reply_markup": {"inline_keyboard": inline_buttons},
                            },
                        )
        except Exception as exc:
            log.warning("Could not send login confirmation message: %s", exc)

    name  = user.get("full_name") or user.get("username") or "User"
    email = user.get("email") or ""
    return HTMLResponse(_SUCCESS_HTML.format(name=name, email=email))


# ── Webhook endpoint ──────────────────────────────────────────────────────────

@router.post("/webhook")
async def telegram_webhook(request: Request) -> dict:
    """
    Telegram POSTs each Update here when webhook mode is active.
    Protected by the X-Telegram-Bot-Api-Secret-Token header.
    """
    if BOT_MODE != "webhook":
        raise HTTPException(404, "Webhook mode is not active.")

    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        raise HTTPException(403, "Invalid secret token.")

    try:
        from telegram import Update
        from bot import get_application

        data   = await request.json()
        app    = await get_application()
        update = Update.de_json(data, app.bot)
        await app.process_update(update)
        return {"ok": True}
    except Exception as exc:
        log.error("Webhook processing error: %s", exc, exc_info=True)
        return {"ok": False, "error": str(exc)}


# ── Status ────────────────────────────────────────────────────────────────────

@router.get("/status")
async def telegram_status() -> dict:
    return {
        "mode":        BOT_MODE,
        "webhook_url": f"{BACKEND_URL}/telegram/webhook" if BOT_MODE == "webhook" else None,
        "bot_token":   "configured" if BOT_TOKEN else "MISSING",
    }
