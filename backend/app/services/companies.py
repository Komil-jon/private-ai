"""
companies.py — Multi-tenant company registry
=============================================
Add a new entry to COMPANIES to onboard another client.
Each company gets its own isolated Qdrant key and docs directory.

docs_subdir: relative to the project-level docs/ folder.
             e.g. "obelius" → <project>/docs/obelius/
qdrant_key:  the session_id value used in Qdrant for this company's chunks.
"""

from typing import Optional

COMPANIES: dict = {
    "obelius": {
        "id":          "obelius",
        "name":        "Obelius Technologies",
        "domain":      "obelius.com",
        "qdrant_key":  "COMPANY_BASE",   # backward-compat — existing ingested chunks
        "docs_subdir": "obelius",        # docs/obelius/
    },
    "eternal": {
        "id":          "eternal",
        "name":        "Eternal Technologies",
        "domain":      "eternal.uz",
        "qdrant_key":  "COMPANY_eternal",
        "docs_subdir": "eternal",        # docs/eternal/
    },
}


def list_companies() -> list:
    return [
        {"id": c["id"], "name": c["name"], "domain": c["domain"]}
        for c in COMPANIES.values()
    ]


def get_company(company_id: str) -> Optional[dict]:
    return COMPANIES.get(company_id)


def get_qdrant_key(company_id: Optional[str]) -> str:
    """Return the Qdrant key for company_id. Falls back to COMPANY_BASE."""
    if company_id:
        c = COMPANIES.get(company_id)
        if c:
            return c["qdrant_key"]
    return "COMPANY_BASE"
