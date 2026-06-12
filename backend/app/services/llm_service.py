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
from typing import List, Dict, Any, Generator, Optional
from dotenv import load_dotenv

API_KEY = os.getenv("API_KEY")
if not API_KEY:
    load_dotenv()
    API_KEY = os.getenv("API_KEY")

if not API_KEY:
    raise ValueError("API_KEY not found. Set it in your .env file or environment.")

client = genai.Client(api_key=API_KEY)


# ── shared prompt builder ────────────────────────────────────────────────────

def _build_prompt(
    conversation,
    context_chunks: Optional[List[Dict[str, Any]]] = None,
    user_profile:   Optional[str] = None,
) -> str:
    has_context = bool(context_chunks)
    has_profile = bool(user_profile and user_profile.strip())

    # ── Document context block ──────────────────────────────────────────────
    context_block = ""
    if has_context:
        context_block = "\n\n--- DOCUMENT CONTEXT ---\n"
        context_block += "The following excerpts were retrieved from the user's uploaded documents.\n"
        context_block += "Use them to answer the question. Cite the source filename when relevant.\n\n"
        for i, chunk in enumerate(context_chunks):
            page_info = f", page {chunk['page']}" if chunk.get("page") else ""
            context_block += (
                f"[{i+1}] Source: {chunk['filename']}{page_info}\n"
                f"{chunk['text'].strip()}\n\n"
            )
        context_block += "--- END CONTEXT ---\n"

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
    system_prompt = f"""You are Obelius, a helpful private AI assistant.

Rules:
- Be polite and concise
- Format responses using Markdown (bold, italic, lists when helpful)
- Do NOT answer harmful, illegal, or dangerous questions
- Do NOT provide personal or sensitive information about third parties
- If a question is inappropriate, respond with exactly: PERSONAL
- If content is unsafe, respond with exactly: IGNORED
- Remember details the user shares and use them to give more relevant answers
{"- When answering from documents, mention the source filename so the user knows where the information comes from." if has_context else ""}
{profile_block}{context_block}"""

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
) -> Generator[str, None, None]:
    """
    Yields plain string tokens as Gemini produces them.
    Raises on hard errors so the route can catch and close the stream cleanly.
    """
    prompt = _build_prompt(conversation, context_chunks, user_profile)
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