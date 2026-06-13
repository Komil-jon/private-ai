"""
document_store.py — Qdrant Cloud-backed semantic document store
================================================================
Embedding is done via Gemini text-embedding-004 API (768 dims) — no local
ONNX model, no RAM spike, works fine on Render's free 512 MB tier.

Upload flow:
  text chunks → Gemini embed API → 768-dim vectors → Qdrant Cloud

Retrieve flow:
  query → Gemini embed API → cosine search in Qdrant Cloud → top-k chunks
"""

from __future__ import annotations

import os
import uuid
import logging
from typing import List, Dict, Any, Optional

from dotenv import load_dotenv
from google import genai
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
    FilterSelector,
    PayloadSchemaType,
)

load_dotenv()

log = logging.getLogger("obelius.docstore")

# ── Constants ─────────────────────────────────────────────────────────────────
COLLECTION      = "company_docs"
CHUNK_SIZE      = 400
CHUNK_OVERLAP   = 80
TOP_K           = 5
VECTOR_SIZE     = 768          # Gemini text-embedding-004 output dimension
EMBED_MODEL     = "text-embedding-004"
EMBED_BATCH     = 50           # Gemini allows up to 100 per call; 50 is safe
SCORE_THRESHOLD = 0.55

# ── Lazy singletons ───────────────────────────────────────────────────────────
_qdrant: Optional[QdrantClient] = None
_gemini: Optional[genai.Client] = None


def _get_gemini() -> genai.Client:
    global _gemini
    if _gemini is None:
        api_key = os.getenv("API_KEY", "")
        if not api_key:
            raise RuntimeError("API_KEY not set — cannot embed documents.")
        _gemini = genai.Client(api_key=api_key)
    return _gemini


def _get_client() -> QdrantClient:
    global _qdrant
    if _qdrant is None:
        url     = os.getenv("QDRANT_URL", "").strip()
        api_key = os.getenv("QDRANT_API_KEY", "").strip()

        if url and api_key:
            log.info("Connecting to Qdrant Cloud at %s", url)
            _qdrant = QdrantClient(url=url, api_key=api_key)
        else:
            data_dir = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "..", "qdrant_data")
            )
            os.makedirs(data_dir, exist_ok=True)
            log.warning("QDRANT_URL/QDRANT_API_KEY not set — using local storage at %s", data_dir)
            _qdrant = QdrantClient(path=data_dir)

        _ensure_collection(_qdrant)
    return _qdrant


def _ensure_collection(client: QdrantClient) -> None:
    existing = {c.name: c for c in client.get_collections().collections}

    if COLLECTION in existing:
        # If the collection was created with the old 384-dim fastembed vectors,
        # delete and recreate it so dimensions match Gemini's 768.
        info = client.get_collection(COLLECTION)
        current_size = info.config.params.vectors.size
        if current_size != VECTOR_SIZE:
            log.warning(
                "Collection '%s' has vector size %d but expected %d — recreating.",
                COLLECTION, current_size, VECTOR_SIZE,
            )
            client.delete_collection(COLLECTION)
            existing.pop(COLLECTION)

    if COLLECTION not in existing:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
        log.info("Created Qdrant collection '%s' (%d dims).", COLLECTION, VECTOR_SIZE)

    # Qdrant Cloud requires a payload index for filtered fields.
    try:
        client.create_payload_index(
            collection_name=COLLECTION,
            field_name="session_id",
            field_schema=PayloadSchemaType.KEYWORD,
        )
    except Exception:
        pass  # index already exists — safe to ignore


# ── Embedding ─────────────────────────────────────────────────────────────────

def _embed(texts: List[str]) -> List[List[float]]:
    """
    Embed texts using Gemini text-embedding-004.
    Batches calls so we never exceed the API's per-request limit.
    No local model loaded — zero RAM overhead.
    """
    client  = _get_gemini()
    vectors = []

    for i in range(0, len(texts), EMBED_BATCH):
        batch  = texts[i : i + EMBED_BATCH]
        result = client.models.embed_content(
            model=EMBED_MODEL,
            contents=batch,
        )
        vectors.extend(e.values for e in result.embeddings)

    return vectors


# ── Chunking ──────────────────────────────────────────────────────────────────

def _chunk_text(text: str) -> List[str]:
    text = text.strip()
    if not text:
        return []
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start : start + CHUNK_SIZE])
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


# ── Public API ────────────────────────────────────────────────────────────────

def init_docstore() -> None:
    """Called at startup. Connects to Qdrant and verifies the collection."""
    _get_client()
    log.info("Document store ready (Qdrant Cloud + Gemini %s).", EMBED_MODEL)


def store_document(session_id: str, filename: str, pages: List[tuple]) -> int:
    """Chunk, embed, and store all pages of a document. Returns chunk count."""
    client = _get_client()

    all_texts: List[str] = []
    all_meta:  List[dict] = []

    for page_idx, text in pages:
        for chunk_i, chunk in enumerate(_chunk_text(text)):
            all_texts.append(chunk)
            all_meta.append({
                "session_id": session_id,
                "filename":   filename,
                "page":       page_idx,
                "chunk_idx":  chunk_i,
                "text":       chunk,
            })

    if not all_texts:
        return 0

    all_vectors = _embed(all_texts)

    points = [
        PointStruct(id=str(uuid.uuid4()), vector=vec, payload=meta)
        for vec, meta in zip(all_vectors, all_meta)
    ]

    for i in range(0, len(points), EMBED_BATCH):
        client.upsert(collection_name=COLLECTION, points=points[i : i + EMBED_BATCH])

    log.info("Stored %d chunks (session=%s, file=%s).", len(points), session_id, filename)
    return len(points)


def retrieve_context(session_id: str, query: str) -> List[Dict[str, Any]]:
    """Return top-k semantically relevant chunks for this session + query."""
    if not query.strip():
        return []

    client = _get_client()
    vec    = _embed([query])[0]

    response = client.query_points(
        collection_name=COLLECTION,
        query=vec,
        query_filter=Filter(
            must=[FieldCondition(key="session_id", match=MatchValue(value=session_id))]
        ),
        limit=TOP_K,
        with_payload=True,
        score_threshold=SCORE_THRESHOLD,
    )

    return [
        {
            "filename": h.payload["filename"],
            "page":     h.payload.get("page"),
            "chunk":    h.payload.get("chunk_idx"),
            "text":     h.payload["text"],
            "score":    round(h.score, 3),
        }
        for h in response.points
    ]


def session_has_documents(session_id: str) -> bool:
    client = _get_client()
    result = client.count(
        collection_name=COLLECTION,
        count_filter=Filter(
            must=[FieldCondition(key="session_id", match=MatchValue(value=session_id))]
        ),
        exact=False,
    )
    return result.count > 0


def clear_session(session_id: str) -> None:
    """Remove all stored vectors and graph nodes for a session."""
    client = _get_client()
    client.delete(
        collection_name=COLLECTION,
        points_selector=FilterSelector(
            filter=Filter(
                must=[FieldCondition(key="session_id", match=MatchValue(value=session_id))]
            )
        ),
    )
    log.info("Cleared Qdrant vectors for session=%s.", session_id)

    from app.services.graph_store import clear_session_graph
    clear_session_graph(session_id)


def list_files(session_id: str) -> List[str]:
    """Return unique filenames stored for a session."""
    client = _get_client()
    records, _ = client.scroll(
        collection_name=COLLECTION,
        scroll_filter=Filter(
            must=[FieldCondition(key="session_id", match=MatchValue(value=session_id))]
        ),
        limit=1000,
        with_payload=["filename"],
    )
    seen: List[str] = []
    for r in records:
        fn = r.payload.get("filename", "")
        if fn and fn not in seen:
            seen.append(fn)
    return seen
