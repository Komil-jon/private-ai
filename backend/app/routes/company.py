"""
company.py — Multi-tenant company API
======================================
GET  /api/companies          → list all registered companies (public)
GET  /api/user/company       → get the current user's selected company
POST /api/user/company       → save the current user's company selection
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.services.auth_dep import require_user, UserContext
from app.services.companies import list_companies, get_company
from app.services.mongo import user_settings

router = APIRouter(prefix="/api", tags=["company"])


class SelectCompanyBody(BaseModel):
    company_id: str


@router.get("/companies")
async def get_companies():
    return list_companies()


@router.get("/user/company")
async def get_user_company(user: UserContext = Depends(require_user)):
    doc = await user_settings().find_one({"user_id": user.user_id})
    if not doc or not doc.get("company_id"):
        return {"company_id": None}

    company = get_company(doc["company_id"])
    if not company:
        return {"company_id": None}

    return {
        "company_id": company["id"],
        "name":       company["name"],
        "domain":     company["domain"],
    }


@router.post("/user/company")
async def set_user_company(
    body: SelectCompanyBody,
    user: UserContext = Depends(require_user),
):
    company = get_company(body.company_id)
    if not company:
        raise HTTPException(400, f"Unknown company: {body.company_id!r}")

    await user_settings().update_one(
        {"user_id": user.user_id},
        {"$set": {"user_id": user.user_id, "company_id": body.company_id}},
        upsert=True,
    )
    return {"ok": True, "company_id": body.company_id}
