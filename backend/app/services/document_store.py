"""
document_store.py
-----------------
In-memory store for document chunks, keyed by session_id.

Structure:
    _store = {
        "sess_abc123": [
            {
                "filename": "report.pdf",
                "page":     2,           # 1-based; 0 for non-PDF
                "chunk":    0,           # chunk index within that page/section
                "text":     "..."
            },
            ...
        ]
    }

Retrieval strategy (simple keyword overlap):
    Score each chunk by how many words from the query appear in the chunk.
    Return the top-k chunks above a minimum score threshold.
    This is intentionally simple — easy to swap for Qdrant vector search
    in Class 3/4 of the camp without changing any other file.
"""

from typing import List, Dict, Any
import re

# ── tuneable constants ──────────────────────────────────────────────────────
CHUNK_SIZE      = 400   # characters per chunk
CHUNK_OVERLAP   = 80    # characters overlap between consecutive chunks
TOP_K           = 4     # how many chunks to inject into the prompt
MIN_SCORE       = 1     # minimum keyword hits to be considered relevant
# ────────────────────────────────────────────────────────────────────────────

_store: Dict[str, List[Dict[str, Any]]] = {}


# ── public API ───────────────────────────────────────────────────────────────

def store_document(session_id: str, filename: str, pages: List[tuple]) -> int:
    """
    Chunks and stores all pages/sections of a document for a session.
    Returns the total number of chunks stored.

    pages: list of (page_index, text) from parser.py
    """
    if session_id not in _store:
        _store[session_id] = []

    total = 0
    for page_index, text in pages:
        chunks = _chunk_text(text, CHUNK_SIZE, CHUNK_OVERLAP)
        for chunk_i, chunk_text in enumerate(chunks):
            _store[session_id].append({
                "filename": filename,
                "page":     page_index,
                "chunk":    chunk_i,
                "text":     chunk_text,
            })
            total += 1

    return total


def retrieve_context(session_id: str, query: str) -> List[Dict[str, Any]]:
    """
    Returns the top-k most relevant chunks for this session + query.
    Returns an empty list if no documents have been uploaded for the session.
    """
    chunks = _store.get(session_id, [])
    if not chunks:
        return []

    query_words = _tokenise(query)
    if not query_words:
        return []

    scored = []
    for chunk in chunks:
        chunk_words = _tokenise(chunk["text"])
        score = len(query_words & chunk_words)
        if score >= MIN_SCORE:
            scored.append((score, chunk))

    # Sort by score descending, then return top-k
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:TOP_K]]


def session_has_documents(session_id: str) -> bool:
    return bool(_store.get(session_id))


def clear_session(session_id: str) -> None:
    """Remove all stored documents for a session."""
    _store.pop(session_id, None)


def list_files(session_id: str) -> List[str]:
    """Return unique filenames stored for this session."""
    chunks = _store.get(session_id, [])
    seen = []
    for c in chunks:
        if c["filename"] not in seen:
            seen.append(c["filename"])
    return seen


# ── helpers ──────────────────────────────────────────────────────────────────

def _chunk_text(text: str, size: int, overlap: int) -> List[str]:
    """Split text into overlapping character-level chunks."""
    text = text.strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start += size - overlap
    return chunks


def _tokenise(text: str):
    """Lowercase word-set for keyword overlap scoring."""
    return set(re.findall(r"[a-z]{3,}", text.lower()))