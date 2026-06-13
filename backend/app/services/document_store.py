"""
document_store.py — Qdrant Cloud-backed semantic document store
================================================================
How it works:
  Upload:   text chunks → fastembed (local ONNX model) → 384-dim vectors
            → stored in Qdrant Cloud, tagged with session_id

  Retrieve: query text → embed → Qdrant cosine nearest-neighbour search
            filtered by session_id → top-k semantically relevant chunks

Storage:   Qdrant Cloud free tier (QDRANT_URL + QDRANT_API_KEY env vars)
           Falls back to local ./qdrant_data/ if env vars are not set.
Model:     BAAI/bge-small-en-v1.5  (~130 MB, downloads once on first use)
"""

from __future__ import annotations

import os
import uuid
import logging
from typing import List, Dict, Any, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
    FilterSelector,
)
from fastembed import TextEmbedding

log = logging.getLogger("obelius.docstore")

# ── Tuneable constants ───────────────────────────────────────────────────────
COLLECTION    = "company_docs"
CHUNK_SIZE    = 400          # characters per chunk
CHUNK_OVERLAP = 80           # overlap between consecutive chunks
TOP_K           = 5            # chunks returned per query
VECTOR_SIZE     = 384          # matches BAAI/bge-small-en-v1.5
EMBED_MODEL     = "BAAI/bge-small-en-v1.5"
BATCH_SIZE      = 64           # embedding batch size
SCORE_THRESHOLD = 0.55         # minimum cosine similarity — below this the chunk is not relevant enough

# ── Lazy singletons (initialised on first use) ───────────────────────────────
_client:   Optional[QdrantClient]  = None
_embedder: Optional[TextEmbedding] = None


# ── Initialisation ────────────────────────────────────────────────────────────

def init_docstore() -> None:
    """
    Called once at server startup (from main.py lifespan).
    Pre-warms the embedding model so the first upload isn't slow.
    """
    _get_client()
    _get_embedder()
    log.info("Document store ready (Qdrant + %s).", EMBED_MODEL)


def _get_client() -> QdrantClient:
    global _client
    if _client is None:
        qdrant_url = os.getenv("QDRANT_URL", "").strip()
        qdrant_api_key = os.getenv("QDRANT_API_KEY", "").strip()

        if qdrant_url and qdrant_api_key:
            log.info("Connecting to Qdrant Cloud at %s", qdrant_url)
            _client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
        else:
            # Fallback: local file storage for development
            data_dir = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "..", "qdrant_data")
            )
            os.makedirs(data_dir, exist_ok=True)
            log.warning("QDRANT_URL/QDRANT_API_KEY not set — falling back to local storage at %s", data_dir)
            _client = QdrantClient(path=data_dir)

        _ensure_collection(_client)
    return _client


def _get_embedder() -> TextEmbedding:
    global _embedder
    if _embedder is None:
        log.info("Loading embedding model %s (first run may download ~130 MB)…", EMBED_MODEL)
        _embedder = TextEmbedding(model_name=EMBED_MODEL)
        log.info("Embedding model loaded.")
    return _embedder


def _ensure_collection(client: QdrantClient) -> None:
    existing = {c.name for c in client.get_collections().collections}
    if COLLECTION not in existing:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
        log.info("Created Qdrant collection '%s'.", COLLECTION)

    # Qdrant Cloud requires a payload index on any field used in filters.
    # create_payload_index is idempotent — safe to call on every startup.
    from qdrant_client.models import PayloadSchemaType
    client.create_payload_index(
        collection_name=COLLECTION,
        field_name="session_id",
        field_schema=PayloadSchemaType.KEYWORD,
    )
    log.info("Payload index ensured for 'session_id' in '%s'.", COLLECTION)


# ── Embedding helper ─────────────────────────────────────────────────────────

def _embed(texts: List[str]) -> List[List[float]]:
    """Embed a list of strings, return list of float vectors."""
    embedder = _get_embedder()
    return [vec.tolist() for vec in embedder.embed(texts)]


# ── Text chunking ─────────────────────────────────────────────────────────────

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

def store_document(session_id: str, filename: str, pages: List[tuple]) -> int:
    """
    Chunk, embed, and store all pages of a document for a session.
    Returns the total number of chunks stored.

    pages: list of (page_index, text) from parser.py
    """
    client = _get_client()

    all_texts: List[str] = []
    all_meta:  List[dict] = []

    for page_idx, text in pages:
        for chunk_i, chunk_text in enumerate(_chunk_text(text)):
            all_texts.append(chunk_text)
            all_meta.append({
                "session_id": session_id,
                "filename":   filename,
                "page":       page_idx,
                "chunk_idx":  chunk_i,
                "text":       chunk_text,
            })

    if not all_texts:
        return 0

    # Embed in batches to avoid memory spikes on large documents
    all_vectors: List[List[float]] = []
    for i in range(0, len(all_texts), BATCH_SIZE):
        all_vectors.extend(_embed(all_texts[i : i + BATCH_SIZE]))

    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=vec,
            payload=meta,
        )
        for vec, meta in zip(all_vectors, all_meta)
    ]

    # Upsert in batches
    for i in range(0, len(points), BATCH_SIZE):
        client.upsert(collection_name=COLLECTION, points=points[i : i + BATCH_SIZE])

    log.info(
        "Stored %d chunks (session=%s, file=%s).",
        len(points), session_id, filename,
    )
    return len(points)


def retrieve_context(session_id: str, query: str) -> List[Dict[str, Any]]:
    """
    Return the top-k most semantically relevant chunks for this session + query.
    Returns [] if no documents have been uploaded for the session.
    """
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

    # Keep Qdrant and Neo4j in sync
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
