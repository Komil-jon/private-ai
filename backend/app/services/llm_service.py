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


# ── Step 1 of agentic search: query rewriter ────────────────────────────────

_REWRITE_PROMPT = """\
Convert the following user message into a concise, effective web search query.
Output ONLY the search query — no explanation, no quotes, no punctuation at the end.

Examples
  "tell me the weather in birmingham right now pls" → weather Birmingham UK today
  "what is the current time in tashkent"            → current time Tashkent Uzbekistan
  "latest news about openai"                        → OpenAI latest news 2026
  "how much does a iphone 16 cost"                  → iPhone 16 price 2026

User message: {message}
Search query:"""


def rewrite_search_query(user_message: str) -> str:
    """
    LLM call 1 of 2 in the agentic search pipeline.
    Converts a natural-language user message into an optimised search engine query.
    Falls back to the original message if the call fails.
    """
    if not user_message.strip():
        return user_message
    try:
        prompt = _REWRITE_PROMPT.format(message=user_message.strip())
        resp = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        rewritten = (resp.text or "").strip().strip('"').strip("'")
        log.info("Query rewrite: %r → %r", user_message[:60], rewritten[:80])
        return rewritten or user_message
    except Exception as exc:
        log.warning("Query rewrite failed: %s — using original", exc)
        return user_message


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
        web_block += "Current results fetched from the web for this query. "
        web_block += "Use them for up-to-date information and always cite the URL.\n\n"
        for i, result in enumerate(web_context):
            web_block += (
                f"[Web {i+1}] {result.get('title', 'Untitled')}\n"
                f"URL: {result.get('url', '')}\n"
                f"{result.get('snippet', '').strip()}\n\n"
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
            "IMPORTANT — live web search results are already provided in the LIVE WEB SEARCH RESULTS section below.\n"
            "These were fetched moments ago specifically for this query. "
            "Answer directly using those results now. Cite the URL for every fact you state.\n"
            "NEVER say 'give me a moment', 'let me check', 'I'll look that up', "
            "'I am unable to browse the web', or anything similar. The results are already here."
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