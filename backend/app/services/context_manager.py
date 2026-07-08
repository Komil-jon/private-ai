"""
context_manager.py — checkpoint-batch conversation summarization
===================================================================
The local model (qwen3:8b, running with an 8192-token window — see
ollama_client.py) has far less headroom than the Gemini API this app used to
call, and the prompt already spends a good chunk of that budget on the
system prompt, document/web/graph context, and user profile. Sending the
full raw message history on every turn would blow the budget fast on any
conversation longer than a few exchanges.

Strategy: fixed-size checkpoints, not a sliding window. Messages 1..10 are
sent raw with no summary at all. Once an 11th message arrives, messages
1..10 are folded into a summary ONE TIME, and from then on only the new
messages since that checkpoint are sent raw (growing turn by turn: 1, 2, 3,
... up to 10 again). When that raw tail itself reaches 10 messages, the
NEXT message triggers another fold: the previous checkpoint summary is
merged with those 10 raw messages into one updated summary, the raw tail
resets to just the new message, and the cycle repeats.

Concretely (BATCH_SIZE=10):
  messages 1-10   -> no summary, all 10 sent raw
  message 11      -> fold 1-10 into summary; send [summary] + [msg 11]
  messages 12-20  -> send [summary(1-10)] + [msgs 11..N] raw (growing)
  message 21      -> fold 11-20 into summary (merged with summary(1-10));
                     send [summary(1-20)] + [msg 21]
  ... repeats every 10 messages ...

The key difference from a sliding window: summarization only runs once per
10-message batch, not on every single turn — most turns cost zero extra
LLM calls for context management, just a list slice.

For logged-in users the checkpoint (history_summary, summarized_upto) is
persisted on the conversation document so it survives across requests and
folding is O(1) amortized. Guests have no server-side conversation record,
so their checkpoint is recomputed from scratch each turn — still correct,
just not incrementally cached (an unlikely-to-matter cost for typical
guest session lengths).
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from bson import ObjectId

from app.models.schemas import Message
from app.services import ollama_client
from app.services.mongo import conversations

log = logging.getLogger("obelius.context")

BATCH_SIZE = 10  # checkpoint every this many messages, not every turn

_SUMMARY_PROMPT = """\
You are condensing an ongoing conversation so older turns can be dropped \
from the prompt without losing important context.

{prior_block}
New messages to fold into the summary:
{transcript}

Write an updated summary as a single concise paragraph (max 120 words) that \
captures key facts, names, numbers, decisions, and any unresolved questions \
or requests. Write in third person, plain prose. No preamble, no bullet points."""


def _format_transcript(msgs: List[Message]) -> str:
    return "\n".join(f"{m.role}: {m.content}" for m in msgs)


def _summarize(prior_summary: str, batch: List[Message]) -> str:
    prior_block = f"Existing summary so far:\n{prior_summary}\n" if prior_summary.strip() else ""
    prompt = _SUMMARY_PROMPT.format(
        prior_block=prior_block,
        transcript=_format_transcript(batch),
    )
    try:
        return (ollama_client.generate(prompt) or "").strip()
    except Exception as exc:
        log.warning("History summarization failed, keeping prior summary: %s", exc)
        return prior_summary


def _advance_checkpoint(
    history: List[Message], checkpoint_count: int, summary: str,
) -> Tuple[int, str]:
    """Fold as many complete BATCH_SIZE-sized batches as are now available."""
    while len(history) - checkpoint_count > BATCH_SIZE:
        batch = history[checkpoint_count : checkpoint_count + BATCH_SIZE]
        summary = _summarize(summary, batch)
        checkpoint_count += BATCH_SIZE
    return checkpoint_count, summary


async def compact_history(
    conv_id: Optional[str],
    history: List[Message],
) -> Tuple[List[Message], str]:
    """
    Returns (messages_to_send, history_summary). messages_to_send always
    includes the current turn (the last item in `history`) plus everything
    back to the last checkpoint — between 1 and BATCH_SIZE messages.
    """
    if len(history) <= BATCH_SIZE:
        return history, ""

    if not conv_id:
        # Guest / stateless conversation — nothing to persist against, so
        # recompute the checkpoint chain from scratch each call.
        checkpoint_count, summary = _advance_checkpoint(history, 0, "")
        return history[checkpoint_count:], summary

    doc = await conversations().find_one(
        {"_id": ObjectId(conv_id)}, {"history_summary": 1, "summarized_upto": 1}
    )
    checkpoint_count: int = (doc or {}).get("summarized_upto", 0)
    summary: str = (doc or {}).get("history_summary", "")

    new_checkpoint_count, new_summary = _advance_checkpoint(history, checkpoint_count, summary)

    if new_checkpoint_count != checkpoint_count:
        await conversations().update_one(
            {"_id": ObjectId(conv_id)},
            {"$set": {"history_summary": new_summary, "summarized_upto": new_checkpoint_count}},
        )

    return history[new_checkpoint_count:], new_summary
