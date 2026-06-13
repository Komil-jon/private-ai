"""
web_search.py — DuckDuckGo web search (Step 2 of the agentic search pipeline)
==============================================================================
Receives an already-optimised query from the LLM rewriter (llm_service.rewrite_search_query),
so this module's job is purely retrieval + relevance filtering.

Pipeline position:
    user message → rewrite_search_query() → search() → stream_reply()

Uses the `ddgs` package (formerly duckduckgo_search).
"""

from __future__ import annotations

import re
import time
import logging
import concurrent.futures
from typing import List, Dict

import httpx

try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS  # type: ignore[no-redef]

log = logging.getLogger("obelius.websearch")

MAX_SNIPPET = 400       # characters per DDG snippet
MAX_PAGE_CONTENT = 5000 # characters of crawled page content fed to LLM


def _is_relevant(result: dict, query: str, original_query: str = "") -> bool:
    """
    Drop results that share no meaningful words (≥ 4 chars) with the query.
    Checks against both the rewritten query and the original user query.
    """
    keywords = set(re.findall(r"\b\w{4,}\b", query.lower()))
    if original_query:
        keywords |= set(re.findall(r"\b\w{4,}\b", original_query.lower()))
    if not keywords:
        return True
    haystack = ((result.get("title") or "") + " " + (result.get("body") or "")).lower()
    return any(kw in haystack for kw in keywords)


def search(query: str, max_results: int = 5, original_query: str = "") -> List[Dict[str, str]]:
    """
    Run a DuckDuckGo text search and return relevance-filtered results.
    The query should already be optimised by rewrite_search_query().
    Returns [] on any failure — callers never crash.
    """
    if not query or not query.strip():
        return []

    raw = []
    for attempt, safe in enumerate(["moderate", "off"]):
        if attempt > 0:
            time.sleep(1.5)  # brief pause before retry to avoid rate-limit
        try:
            with DDGS() as ddgs:
                raw = list(
                    ddgs.text(
                        query.strip(),
                        max_results=max_results + 4,
                        safesearch=safe,
                    )
                )
            log.info("DDG attempt %d (%s safesearch): %d raw results for %r",
                     attempt + 1, safe, len(raw), query)
            if raw:
                break
        except Exception as exc:
            log.warning("DDG search attempt %d failed (%s safesearch): %s", attempt + 1, safe, exc)

    if not raw:
        log.warning("DDG returned no results for %r", query)
        return []

    results: List[Dict[str, str]] = []
    filtered_out = 0
    for r in raw:
        title   = (r.get("title") or "").strip()
        url     = (r.get("href")  or "").strip()
        snippet = (r.get("body")  or "").strip()

        if not url:
            continue

        if not _is_relevant(r, query, original_query):
            log.debug("Relevance filter dropped: %s", title)
            filtered_out += 1
            continue

        if len(snippet) > MAX_SNIPPET:
            snippet = snippet[:MAX_SNIPPET].rsplit(" ", 1)[0] + "…"

        results.append({"title": title, "url": url, "snippet": snippet})

        if len(results) == max_results:
            break

    log.info("Search %r → %d/%d kept (filtered %d).",
             query, len(results), len(raw), filtered_out)
    return results


# ── Parallel multi-query search ──────────────────────────────────────────────

def search_multiple(
    queries: List[str],
    max_results_per_query: int = 4,
    original_query: str = "",
) -> List[Dict[str, str]]:
    """
    Run multiple search queries in parallel and merge results, deduplicating by URL.
    Each result is tagged with the query that produced it so the LLM has context.
    Falls back gracefully if individual queries fail.
    """
    if not queries:
        return []
    if len(queries) == 1:
        results = search(queries[0], max_results=max_results_per_query, original_query=original_query)
        for r in results:
            r["_query"] = queries[0]
        return results

    all_results: List[Dict[str, str]] = []
    seen_urls: set = set()

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(queries), 4)) as pool:
        future_to_query = {
            pool.submit(search, q, max_results_per_query, original_query): q
            for q in queries
        }
        done, _ = concurrent.futures.wait(future_to_query, timeout=20.0)
        # Preserve query order: process completed futures in original query order
        query_results: dict = {}
        for future in done:
            q = future_to_query[future]
            try:
                query_results[q] = future.result()
            except Exception as exc:
                log.warning("Search failed for %r: %s", q, exc)
                query_results[q] = []

        for q in queries:
            for r in query_results.get(q, []):
                url = r.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    r["_query"] = q
                    all_results.append(r)

    log.info("Multi-search: %d queries → %d unique results", len(queries), len(all_results))
    return all_results


# ── Layer 2: page content crawling ───────────────────────────────────────────

def _fetch_page_content(url: str, timeout: float = 7.0) -> str:
    """
    Fetch actual page text via Jina.ai reader (r.jina.ai/{url}).
    Returns '' on any failure — callers never crash.
    """
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            r = client.get(
                f"https://r.jina.ai/{url}",
                headers={"Accept": "text/plain", "X-No-Cache": "true"},
            )
        if r.status_code == 200:
            text = r.text.strip()
            if len(text) > MAX_PAGE_CONTENT:
                text = text[:MAX_PAGE_CONTENT].rsplit(" ", 1)[0] + "…"
            return text
    except Exception as exc:
        log.debug("Page fetch failed for %s: %s", url, exc)
    return ""


def enrich_with_content(
    results: List[Dict[str, str]], max_pages: int = 2
) -> List[Dict[str, str]]:
    """
    Fetch full page content for the top results and add a 'content' key.
    Runs fetches in parallel; falls back to snippet silently on any failure.
    """
    if not results:
        return results

    to_fetch = results[:max_pages]

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_pages) as pool:
        future_to_idx = {
            pool.submit(_fetch_page_content, r["url"]): i
            for i, r in enumerate(to_fetch)
        }
        done, _ = concurrent.futures.wait(future_to_idx, timeout=8.0)
        for future in done:
            idx = future_to_idx[future]
            try:
                content = future.result()
                if content and len(content) > 150:
                    results[idx]["content"] = content
                    log.info("Page content enriched for result %d (%d chars)", idx + 1, len(content))
            except Exception as exc:
                log.debug("Page content future failed for result %d: %s", idx + 1, exc)

    return results
