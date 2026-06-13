"""
llm_service.py
--------------
Two modes:
  generate_reply()  — full text (kept for internal use / fallback)
  stream_reply()    — generator that yields raw token strings one by one

Streaming uses client.models.generate_content_stream() from the google-genai SDK.
Each yielded value is a plain string token so callers don't need to know the SDK.

User personalisation:
  Pass user_profile (string from memory.get_user_profile()) and the prompt
  will include what the AI already knows about the user.
"""

from google import genai
import os
import logging
from typing import List, Dict, Any, Generator, Optional
from dotenv import load_dotenv

log = logging.getLogger("obelius.llm")

API_KEY = os.getenv("API_KEY")
if not API_KEY:
    load_dotenv()
    API_KEY = os.getenv("API_KEY")

if not API_KEY:
    raise ValueError("API_KEY not found. Set it in your .env file or environment.")

client = genai.Client(api_key=API_KEY)


# ── Step 1 of agentic search: multi-query planner ───────────────────────────
# The planner decides HOW MANY searches are needed and WHAT to search.
# Comparisons → one query per item. Multi-topic → one per topic. Simple → one.

_PLAN_QUERIES_PROMPT = """\
You are a search query planner for an AI assistant.
Given a user message, output the web search queries needed to answer it fully.

Rules:
- Comparison / "A vs B" / "which is better" questions: one query PER item being compared
- Questions mentioning multiple distinct topics: one query per topic
- Simple single-topic questions: exactly 1 query
- Follow-up questions (uses "it", "they", "that team", etc.): resolve the reference from context, then output 1-2 queries
- Maximum 3 queries total
- Output ONLY the queries, one per line — no numbers, no bullets, no explanation
- Each query must be concise and optimised for a search engine (include year/specifics where useful)

Recent conversation context (resolve pronouns / references using this):
{context}

Examples:
  message: "which book is more scientific: Subtle Art of Not Giving a Fuck or Surrounded by Idiots"
  → The Subtle Art of Not Giving a Fuck scientific accuracy psychology evidence
  → Surrounded by Idiots Thomas Erikson DISC model scientific validity criticism

  message: "compare iphone 16 vs samsung s25 camera"
  → iPhone 16 camera quality review 2025
  → Samsung Galaxy S25 camera quality review 2025

  message: "which team will win" (context: user was asking about World Cup)
  → FIFA World Cup 2026 winner prediction favorites

  message: "weather in birmingham"
  → weather Birmingham UK today

  message: "latest openai news"
  → OpenAI latest news 2026

User message: {message}
Search queries (one per line):"""


def plan_search_queries(user_message: str, context: str = "") -> List[str]:
    """
    Returns 1–3 optimised search queries for the user message.
    For comparisons/multi-topic questions returns one query per item so each
    topic gets its own search results rather than one muddled combined query.
    Falls back to the original message on any failure.
    """
    if not user_message.strip():
        return [user_message]
    try:
        prompt = _PLAN_QUERIES_PROMPT.format(
            context=context.strip() or "(none)",
            message=user_message.strip(),
        )
        resp = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        lines = (resp.text or "").strip().split("\n")
        queries: List[str] = []
        for line in lines:
            q = line.strip().lstrip("-•*0123456789.)").strip()
            if q and len(q) > 3:
                queries.append(q)
        queries = queries[:3]
        log.info("Query plan: %r → %d queries: %r", user_message[:60], len(queries), queries)
        return queries or [user_message]
    except Exception as exc:
        log.warning("Query planning failed: %s — using original message", exc)
        return [user_message]


# ── shared prompt builder ────────────────────────────────────────────────────

def _build_prompt(
    conversation,
    context_chunks:   Optional[List[Dict[str, Any]]] = None,
    user_profile:     Optional[str] = None,
    web_context:      Optional[List[Dict[str, str]]] = None,
    current_time:     str = "",
    web_attempted:    bool = False,
) -> str:
    has_docs    = bool(context_chunks)
    has_web     = bool(web_context)
    has_profile = bool(user_profile and user_profile.strip())

    # ── Document context block ──────────────────────────────────────────────
    doc_block = ""
    if has_docs:
        doc_block = "\n\n--- DOCUMENT CONTEXT ---\n"
        doc_block += "Excerpts retrieved from the user's uploaded documents. "
        doc_block += "Cite the source filename when relevant.\n\n"
        for i, chunk in enumerate(context_chunks):
            page_info = f", page {chunk['page']}" if chunk.get("page") else ""
            doc_block += (
                f"[Doc {i+1}] {chunk['filename']}{page_info}\n"
                f"{chunk['text'].strip()}\n\n"
            )
        doc_block += "--- END DOCUMENT CONTEXT ---\n"

    # ── Web search context block ────────────────────────────────────────────
    web_block = ""
    if has_web:
        web_block = "\n\n--- LIVE WEB SEARCH RESULTS ---\n"
        web_block += "Full page content fetched from the web moments ago.\n"
        web_block += "Results may come from multiple targeted searches — use ALL of them.\n"
        web_block += "Extract and present the actual data. Do NOT respond with a list of websites.\n\n"
        for i, result in enumerate(web_context[:8]):   # cap at 8 to keep prompt size sane
            body = result.get("content") or result.get("snippet", "").strip()
            query_tag = f" [search: {result['_query']}]" if result.get("_query") else ""
            web_block += (
                f"[Web {i+1}]{query_tag}\n"
                f"Title: {result.get('title', 'Untitled')}\n"
                f"URL: {result.get('url', '')}\n"
                f"{body}\n\n"
            )
        web_block += "--- END WEB RESULTS ---\n"

    # ── User memory block ───────────────────────────────────────────────────
    profile_block = ""
    if has_profile:
        profile_block = f"""
--- WHAT YOU KNOW ABOUT THIS USER ---
{user_profile.strip()}
Use this to personalise your tone and answers. Reference it naturally —
never explicitly say "I have a memory of you" or list facts robotically.
--- END USER INFO ---
"""

    # ── System prompt ────────────────────────────────────────────────────────
    capability_block = ""
    if has_docs and has_web:
        capability_block = (
            "IMPORTANT — you have TWO sources of information already in this prompt:\n"
            "  1. The user's uploaded documents (DOCUMENT CONTEXT section below).\n"
            "  2. Live web search results fetched moments ago (LIVE WEB SEARCH RESULTS section below).\n"
            "Use whichever source is relevant. Cite the filename for documents, the URL for web results.\n"
            "NEVER say 'give me a moment', 'let me check', or 'I'll search for that' — everything is already here. Answer now."
        )
    elif has_docs:
        capability_block = (
            "You have the user's uploaded documents (excerpts in DOCUMENT CONTEXT below). "
            "Reference the source filename when quoting from them."
        )
    elif has_web:
        capability_block = (
            "IMPORTANT — Live web results with actual page content are in this prompt.\n"
            "Read the content and synthesize a DIRECT answer — the actual data the user asked for.\n"
            "For weather: state temperature, conditions, forecast. "
            "For sports: state match results and scores. "
            "For prices/news: state the specific facts.\n"
            "Cite sources naturally inline (e.g. 'According to BBC Weather, …') — "
            "do NOT respond with just a list of website links.\n"
            "NEVER say 'give me a moment', 'let me check', 'I'll look that up', "
            "or 'I am unable to browse the web'. The data is already here — use it."
        )
    elif web_attempted:
        # Search was triggered but returned no results (rate-limit, no results found, etc.)
        capability_block = (
            "NOTE: A live web search was performed for this query but returned no usable results.\n"
            "Answer using your training knowledge and clearly note that the information may not be current.\n"
            "Do NOT say 'I cannot search the web' — a search was attempted. Just answer as best you can."
        )

    time_line = f"- Current date and time: **{current_time}**\n" if current_time else ""

    system_prompt = f"""You are Obelius, a helpful private AI assistant.

Rules:
- Be polite and concise
- Format responses using Markdown (bold, italic, lists when helpful)
- Do NOT answer harmful, illegal, or dangerous questions
- Do NOT provide personal or sensitive information about third parties
- If a question is inappropriate, respond with exactly: PERSONAL
- If content is unsafe, respond with exactly: IGNORED
- Remember details the user shares and use them to give more relevant answers
- For sports, entertainment, or current-events predictions/opinions: give a real answer based on available data. Do NOT refuse or say you "cannot predict" — the user wants your informed take, not a disclaimer.
- When the user asks "which team will win" or "who do you think will win", give a concrete prediction with reasoning from the search data. It is fine and expected.
{time_line}{capability_block}
{profile_block}{doc_block}{web_block}"""

    formatted = system_prompt + "\n\n"
    for msg in conversation:
        role = "User" if msg.role == "user" else "Assistant"
        formatted += f"{role}: {msg.content}\n"

    return formatted


# ── streaming (primary path) ─────────────────────────────────────────────────

def stream_reply(
    conversation,
    context_chunks: Optional[List[Dict[str, Any]]] = None,
    user_profile:   Optional[str] = None,
    web_context:    Optional[List[Dict[str, str]]] = None,
    current_time:   str = "",
    web_attempted:  bool = False,
) -> Generator[str, None, None]:
    """
    LLM call 2 of 2 in the agentic search pipeline.
    Yields plain string tokens as Gemini produces them.
    """
    prompt = _build_prompt(
        conversation, context_chunks, user_profile,
        web_context, current_time, web_attempted,
    )
    for chunk in client.models.generate_content_stream(
        model="gemini-2.5-flash",
        contents=prompt,
    ):
        if chunk.text:
            yield chunk.text


# ── non-streaming fallback ───────────────────────────────────────────────────

def generate_reply(
    conversation,
    context_chunks: Optional[List[Dict[str, Any]]] = None,
    user_profile:   Optional[str] = None,
) -> str:
    try:
        prompt = _build_prompt(conversation, context_chunks, user_profile)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        return response.text
    except Exception as e:
        print(f"[llm_service] Error: {e}")
        return "Sorry, something went wrong."