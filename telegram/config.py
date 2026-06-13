"""
config.py — All environment settings for the Telegram bot.

Dev:   BOT_MODE=polling  → run `python -m telegram.bot`
Prod:  BOT_MODE=webhook  → bot integrates with FastAPI, Telegram POSTs updates
"""
from __future__ import annotations

import os
import secrets
from urllib.parse import quote
from dotenv import load_dotenv

# Load from both the telegram folder and the backend .env
_here = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_here, ".env"))
load_dotenv(os.path.join(_here, "..", "backend", "app", ".env"))

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
BOT_MODE: str = os.getenv("BOT_MODE", "polling")          # polling | webhook
WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "")           # https://yourapp.railway.app
WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", secrets.token_hex(16))
WEBHOOK_PATH: str = "/telegram/webhook"

BACKEND_URL: str = os.getenv("BACKEND_URL", "http://localhost:8001")
MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017")
AUTH_BASE_URL: str = os.getenv("AUTH_BASE_URL", "https://auth.eternal.uz")

# Telegram document size limit (Telegram caps Bot API downloads at 20MB)
MAX_FILE_MB: int = 20
ALLOWED_EXTENSIONS: set[str] = {".pdf", ".docx", ".txt", ".csv"}

# Auth state TTL in seconds (10 minutes)
AUTH_STATE_TTL: int = 600


def build_login_url(state: str) -> str:
    """
    Build the auth.eternal.uz login URL for the Telegram Mini App button.
    Callback uses WEBHOOK_URL (public) so mobile browsers can reach it.
    Falls back to BACKEND_URL only when WEBHOOK_URL is not set (local testing via ngrok etc).
    """
    public_base = (WEBHOOK_URL or BACKEND_URL).rstrip("/")
    callback = f"{public_base}/telegram/callback?state={state}"
    return f"{AUTH_BASE_URL}?redirect={quote(callback, safe='')}&prompt=login"
