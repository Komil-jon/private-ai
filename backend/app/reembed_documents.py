"""
reembed_documents.py — one-off migration script
=================================================
Re-embeds every chunk already stored in the Qdrant `company_docs` collection
using the new local embedding model (nomic-embed-text) instead of Gemini's
gemini-embedding-001. The old and new embedding spaces are NOT compatible
even though both are 768-dim, so this must be run once after switching
document_store.py over to the local model — otherwise old chunks will
silently return poor/irrelevant search results forever.

Payload (filename, session_id, text, page, chunk_idx) is left untouched;
only the vector for each existing point is recomputed and updated in place.

Run from the backend/ directory:
  python -m app.reembed_documents
"""

from __future__ import annotations

import logging

from qdrant_client.models import PointVectors

from app.services.document_store import COLLECTION, EMBED_BATCH, _embed, _get_client

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("obelius.reembed")


def reembed_all() -> None:
    client = _get_client()

    total = client.count(collection_name=COLLECTION, exact=True).count
    log.info("Found %d chunk(s) in '%s' to re-embed.", total, COLLECTION)
    if total == 0:
        return

    done = 0
    next_offset = None
    while True:
        records, next_offset = client.scroll(
            collection_name=COLLECTION,
            limit=EMBED_BATCH,
            offset=next_offset,
            with_payload=["text"],
            with_vectors=False,
        )
        if not records:
            break

        texts = [r.payload.get("text", "") for r in records]
        vectors = _embed(texts)

        client.update_vectors(
            collection_name=COLLECTION,
            points=[
                PointVectors(id=r.id, vector=vec)
                for r, vec in zip(records, vectors)
            ],
        )

        done += len(records)
        log.info("Re-embedded %d/%d chunks...", done, total)

        if next_offset is None:
            break

    log.info("Done. Re-embedded %d chunk(s) with the local embedding model.", done)


if __name__ == "__main__":
    reembed_all()
