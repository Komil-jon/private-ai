"""
graph_store.py — Neo4j-backed knowledge graph for Graph RAG
============================================================
Runs alongside the Qdrant vector store (document_store.py).
Qdrant handles semantic similarity; Neo4j handles entity relationships.

Upload flow:
  document text → local LLM entity extractor → (Entity)-[RELATION]->(Entity)
  nodes + edges written to Neo4j, tagged with session_id + filename

Retrieval flow:
  user query → local LLM entity extractor → graph traversal → relationship strings
  merged with Qdrant chunks in process.py before the final LLM call

All Cypher uses $params (never string interpolation) to prevent injection.
All nodes/edges carry session_id so users never see each other's data.
Falls back silently if NEO4J_URI is not configured.
"""

from __future__ import annotations

import json
import logging
import os
from typing import List, Optional

from dotenv import load_dotenv

from app.services import ollama_client

load_dotenv()

log = logging.getLogger("obelius.graph")

# ── Neo4j driver (lazy singleton) ────────────────────────────────────────────
_driver = None


def _get_driver():
    global _driver
    if _driver is None:
        uri      = os.getenv("NEO4J_URI", "").strip()
        user     = os.getenv("NEO4J_USER", "neo4j").strip()
        password = os.getenv("NEO4J_PASSWORD", "").strip()

        if not uri or not password:
            log.warning("NEO4J_URI/NEO4J_PASSWORD not set — graph store disabled.")
            return None

        from neo4j import GraphDatabase
        _driver = GraphDatabase.driver(uri, auth=(user, password))
        log.info("Neo4j driver connected to %s", uri)

    return _driver


# ── Entity extraction prompt ──────────────────────────────────────────────────

_EXTRACT_PROMPT = """\
You are an information extraction assistant for a company knowledge base.

Read the text below and extract all meaningful relationships between entities.
Entities can be: people, roles, teams, departments, policies, projects, systems, documents, rules, or processes.

Return ONLY a JSON array. Each item must have exactly three string fields:
  "from"     — the source entity name
  "relation" — the relationship type in SCREAMING_SNAKE_CASE (e.g. REPORTS_TO, APPLIES_TO, OWNED_BY, DEFINED_IN, PART_OF)
  "to"       — the target entity name

Rules:
- Extract only relationships clearly stated or directly implied in the text
- Keep entity names short and consistent (e.g. always "Engineering Team", not sometimes "Eng Team")
- Maximum 40 relationships
- If there are no relationships to extract, return an empty array: []
- Return ONLY the JSON array — no explanation, no markdown fences

Text:
{text}"""


_QUERY_ENTITIES_PROMPT = """\
List the key entities (people, roles, teams, departments, policies, projects, systems) \
mentioned in this query. Return ONLY a JSON array of short strings. Max 6 items.
If none, return [].

Query: {query}"""


# ── Internal helpers ──────────────────────────────────────────────────────────

def _extract_triples(text: str) -> List[dict]:
    """Call the local LLM to extract (from, relation, to) triples from document text."""
    if not text.strip():
        return []
    try:
        prompt = _EXTRACT_PROMPT.format(text=text[:6000])  # cap to avoid huge prompts
        raw = (ollama_client.generate(prompt) or "").strip().strip("```json").strip("```").strip()
        triples = json.loads(raw)
        if not isinstance(triples, list):
            return []
        valid = [
            t for t in triples
            if isinstance(t, dict)
            and isinstance(t.get("from"), str)
            and isinstance(t.get("relation"), str)
            and isinstance(t.get("to"), str)
            and t["from"].strip() and t["to"].strip()
        ]
        return valid
    except Exception as exc:
        log.warning("Entity extraction failed: %s", exc)
        return []


def _extract_query_entities(query: str) -> List[str]:
    """Extract entity names from a user query for graph lookup."""
    if not query.strip():
        return []
    try:
        prompt = _QUERY_ENTITIES_PROMPT.format(query=query)
        raw = (ollama_client.generate(prompt) or "").strip().strip("```json").strip("```").strip()
        entities = json.loads(raw)
        if isinstance(entities, list):
            return [str(e).strip() for e in entities if str(e).strip()][:6]
        return []
    except Exception as exc:
        log.warning("Query entity extraction failed: %s", exc)
        return []


def _write_triples(session_id: str, filename: str, triples: List[dict]) -> None:
    """Write extracted triples into Neo4j using parameterised Cypher."""
    driver = _get_driver()
    if not driver or not triples:
        return

    cypher = """
    MERGE (a:Entity {name: $from_name, session_id: $session_id})
    MERGE (b:Entity {name: $to_name,   session_id: $session_id})
    MERGE (a)-[r:RELATION {type: $relation, session_id: $session_id, filename: $filename}]->(b)
    """

    with driver.session() as neo_sess:
        for triple in triples:
            neo_sess.run(
                cypher,
                from_name=triple["from"].strip(),
                to_name=triple["to"].strip(),
                relation=triple["relation"].strip().upper(),
                session_id=session_id,
                filename=filename,
            )


# ── Public API ────────────────────────────────────────────────────────────────

def init_graph() -> None:
    """
    Called once at server startup (from main.py lifespan).
    Verifies the Neo4j connection and creates a uniqueness constraint
    on (Entity.name, Entity.session_id) so MERGE is idempotent.
    """
    driver = _get_driver()
    if not driver:
        return

    try:
        with driver.session() as neo_sess:
            neo_sess.run(
                "CREATE CONSTRAINT entity_unique IF NOT EXISTS "
                "FOR (e:Entity) REQUIRE (e.name, e.session_id) IS NODE KEY"
            )
        log.info("Neo4j graph store ready.")
    except Exception as exc:
        log.warning("Neo4j constraint creation skipped (may already exist): %s", exc)


def index_document_graph(session_id: str, filename: str, pages: List[tuple]) -> int:
    """
    Extract entities/relationships from document pages and store in Neo4j.
    Returns the number of triples written.
    Called as a background task from upload.py — does not block the response.
    """
    driver = _get_driver()
    if not driver:
        return 0

    # Join all pages into one text for entity extraction
    full_text = "\n\n".join(text for _, text in pages if text.strip())
    if not full_text:
        return 0

    triples = _extract_triples(full_text)
    if not triples:
        log.info("No graph triples extracted from %s (session=%s)", filename, session_id)
        return 0

    _write_triples(session_id, filename, triples)
    log.info(
        "Graph indexed: %d triples (session=%s, file=%s)",
        len(triples), session_id, filename,
    )
    return len(triples)


def query_graph(session_id: str, query: str) -> str:
    """
    Look up entity relationships relevant to the query.
    Returns a plain-text block of relationship strings, or "" if nothing found.
    """
    driver = _get_driver()
    if not driver:
        return ""

    entities = _extract_query_entities(query)
    if not entities:
        return ""

    cypher = """
    MATCH (a:Entity {session_id: $session_id})-[r:RELATION]->(b:Entity {session_id: $session_id})
    WHERE a.name IN $entities OR b.name IN $entities
    RETURN a.name AS from_name, r.type AS relation, b.name AS to_name, r.filename AS filename
    LIMIT 20
    """

    try:
        with driver.session() as neo_sess:
            result = neo_sess.run(
                cypher,
                session_id=session_id,
                entities=entities,
            )
            rows = result.data()

        if not rows:
            return ""

        lines = [
            f"{row['from_name']} {row['relation']} {row['to_name']}  (source: {row['filename']})"
            for row in rows
        ]
        return "\n".join(lines)

    except Exception as exc:
        log.warning("Graph query failed: %s", exc)
        return ""


def clear_session_graph(session_id: str) -> None:
    """
    Delete all nodes and relationships for a session.
    Called alongside clear_session() in document_store.py.
    """
    driver = _get_driver()
    if not driver:
        return

    cypher = """
    MATCH (e:Entity {session_id: $session_id})
    DETACH DELETE e
    """
    try:
        with driver.session() as neo_sess:
            neo_sess.run(cypher, session_id=session_id)
        log.info("Neo4j graph cleared for session=%s", session_id)
    except Exception as exc:
        log.warning("Graph clear failed for session=%s: %s", session_id, exc)
