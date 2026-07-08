"""
memory.py  —  User memory and personalisation
==============================================
After each AI reply, a short background local-LLM call extracts any
learnable facts from the exchange and upserts them into user_memory.

Stored per user:
  facts[]   — short bullet strings: "Prefers concise answers",
               "Works as a software engineer", "Located in Uzbekistan"
  summary   — one-paragraph prose summary (regenerated periodically)
  updated_at

These are injected into the LLM system prompt so the AI behaves like
it actually remembers the user across sessions — exactly like Claude or
ChatGPT's memory feature.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from app.services import ollama_client
from app.services.mongo import user_memory

log = logging.getLogger("obelius.memory")

MAX_FACTS = 40  # cap to keep prompts from bloating


async def get_user_profile(user_id: str) -> str:
    """
    Returns a formatted string describing what we know about this user.
    Returns "" if no memory exists yet.
    """
    doc = await user_memory().find_one({"user_id": user_id})
    if not doc:
        return ""

    parts: List[str] = []
    if doc.get("summary"):
        parts.append(f"User summary: {doc['summary']}")
    if doc.get("facts"):
        parts.append("Known facts about this user:")
        for f in doc["facts"]:
            parts.append(f"  • {f}")
    return "\n".join(parts)


async def update_user_memory(
    user_id: str,
    user_message: str,
    assistant_reply: str,
) -> None:
    """
    Background task: extract new facts from this exchange and upsert.
    """
    try:
        existing_doc = await user_memory().find_one({"user_id": user_id})
        existing_facts: List[str] = existing_doc.get("facts", []) if existing_doc else []

        existing_block = ""
        if existing_facts:
            existing_block = "Already known facts:\n" + "\n".join(
                f"  • {f}" for f in existing_facts
            )

        prompt = f"""You are a memory extraction assistant. Analyse this conversation exchange and extract any NEW facts worth remembering about the user (preferences, background, skills, location, goals, communication style, etc.).

{existing_block}

User message: {user_message}
Assistant reply: {assistant_reply}

Rules:
- Only extract facts that are clearly stated or strongly implied by the USER's message
- Skip anything already in the known facts list
- Each fact must be a short, specific, useful sentence (max 15 words)
- If there are no new facts, respond with exactly: NONE
- Respond ONLY with a JSON array of strings, e.g. ["fact1", "fact2"]
  or the word NONE. No preamble, no markdown fences."""

        raw = (ollama_client.generate(prompt) or "").strip()

        if raw == "NONE" or not raw:
            return

        import json
        new_facts: List[str] = json.loads(raw)
        if not isinstance(new_facts, list):
            return

        # Merge, deduplicate, cap
        merged = existing_facts + [f for f in new_facts if f not in existing_facts]
        merged = merged[-MAX_FACTS:]  # keep newest

        # Regenerate summary every 10 facts or on first write
        summary = existing_doc.get("summary", "") if existing_doc else ""
        if len(merged) % 10 == 0 or not summary:
            summary = await _regenerate_summary(merged)

        await user_memory().update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "facts":      merged,
                    "summary":    summary,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
        log.info("Memory updated for user %s (%d facts)", user_id, len(merged))

    except Exception as exc:
        log.warning("Memory update failed for %s: %s", user_id, exc)


async def _regenerate_summary(facts: List[str]) -> str:
    """Generate a short prose summary from the current fact list."""
    if not facts:
        return ""
    try:
        fact_block = "\n".join(f"• {f}" for f in facts)
        prompt = (
            f"Write a single concise paragraph (max 60 words) summarising "
            f"what is known about this user based on these facts:\n{fact_block}\n"
            f"Write in third person. No preamble."
        )
        return (ollama_client.generate(prompt) or "").strip()
    except Exception:
        return ""