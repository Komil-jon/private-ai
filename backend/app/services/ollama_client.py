"""
ollama_client.py
----------------
Thin wrapper around a local Ollama server (https://ollama.com), replacing the
google-genai client. Requires `ollama serve` running locally with:

  ollama pull qwen3:8b
  ollama pull nomic-embed-text

Override the host/models via env vars if needed:
  OLLAMA_HOST, OLLAMA_CHAT_MODEL, OLLAMA_EMBED_MODEL, OLLAMA_NUM_CTX

OLLAMA_NUM_CTX controls the context window (in tokens) Ollama allocates per
request. Ollama's own default is 4096 regardless of what the model itself
supports (qwen3:8b supports up to 40960) — we raise it explicitly since the
Homebrew service already runs with OLLAMA_FLASH_ATTENTION=1 and
OLLAMA_KV_CACHE_TYPE=q8_0, which roughly halves the memory cost per cached
token and makes a larger window affordable on 16GB unified memory.
"""

import os
import json
import logging
from typing import Generator, List

import httpx

log = logging.getLogger("obelius.ollama")

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
CHAT_MODEL = os.getenv("OLLAMA_CHAT_MODEL", "qwen3:8b")
EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))

_client = httpx.Client(base_url=OLLAMA_HOST, timeout=120.0)


def generate(prompt: str, model: str = CHAT_MODEL) -> str:
    """Non-streaming single-turn generation. Returns the full response text."""
    resp = _client.post("/api/generate", json={
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {"num_ctx": NUM_CTX},
    })
    resp.raise_for_status()
    return resp.json().get("response", "")


def generate_stream(prompt: str, model: str = CHAT_MODEL) -> Generator[str, None, None]:
    """Streaming generation. Yields plain text chunks as they arrive."""
    with _client.stream("POST", "/api/generate", json={
        "model": model,
        "prompt": prompt,
        "stream": True,
        "think": False,
        "options": {"num_ctx": NUM_CTX},
    }) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line:
                continue
            data = json.loads(line)
            chunk = data.get("response", "")
            if chunk:
                yield chunk
            if data.get("done"):
                break


def embed(texts: List[str], model: str = EMBED_MODEL) -> List[List[float]]:
    """Embed a batch of texts. Returns one vector per input text."""
    resp = _client.post("/api/embed", json={"model": model, "input": texts})
    resp.raise_for_status()
    return resp.json()["embeddings"]
