#!/bin/bash
# Starts everything needed to run Obelius locally:
#   1. Ollama (local LLM + embedding server) — usually already running in the
#      background via `brew services`, this just makes sure.
#   2. The FastAPI backend, which also serves the frontend at "/".
set -e

brew services start ollama >/dev/null 2>&1 || true

cd "$(dirname "$0")/backend"
source .venv/bin/activate
echo "Starting backend on http://localhost:8001 ..."
uvicorn app.main:app --host 0.0.0.0 --port 8001
