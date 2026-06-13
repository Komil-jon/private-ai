"""
upload.py  —  POST /upload
--------------------------
Accepts multipart file uploads from the frontend.
Each file is:
  1. Parsed into (page, text) pairs from in-memory bytes
  2. Chunked and stored in Qdrant Cloud (no local disk write needed)

Returns JSON: { message, files }
"""

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from typing import List, Optional
import os

from app.models.schemas import UploadResponse
from app.services.parser import parse_file
from app.services.document_store import store_document

router = APIRouter()

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".csv"}
MAX_FILE_SIZE_MB   = 20


@router.post("/upload", response_model=UploadResponse)
async def upload_files(
    session_id: str = Form(...),
    files: List[UploadFile] = File(...),
    conv_id: Optional[str] = Form(None),
):
    if not session_id or not session_id.startswith("sess_"):
        raise HTTPException(status_code=400, detail="Invalid session_id.")

    # Use conv_id as the document store key when available so that uploads
    # are scoped to a specific conversation rather than the whole browser session.
    store_key = conv_id if conv_id else session_id

    processed = []
    errors    = []

    for upload in files:
        filename = upload.filename or "unknown"
        ext = os.path.splitext(filename)[-1].lower()

        # Extension guard
        if ext not in ALLOWED_EXTENSIONS:
            errors.append(f"{filename}: unsupported type")
            continue

        # Read content into memory
        content = await upload.read()

        # Size guard
        if len(content) > MAX_FILE_SIZE_MB * 1024 * 1024:
            errors.append(f"{filename}: exceeds {MAX_FILE_SIZE_MB} MB limit")
            continue

        # Parse → chunk → store in Qdrant Cloud (no disk write)
        try:
            pages = parse_file(filename, content)
            if not pages:
                errors.append(f"{filename}: could not extract text")
                continue

            chunk_count = store_document(store_key, filename, pages)
            processed.append(filename)
            print(f"[upload] {filename} → {chunk_count} chunks (key={store_key})")

        except Exception as e:
            print(f"[upload] Error processing {filename}: {e}")
            errors.append(f"{filename}: processing error")

    if not processed:
        detail = "No files could be processed."
        if errors:
            detail += " Errors: " + "; ".join(errors)
        raise HTTPException(status_code=422, detail=detail)

    msg = f"{len(processed)} file(s) ready for analysis."
    if errors:
        msg += f" ({len(errors)} skipped)"

    return UploadResponse(message=msg, files=processed)