"""
upload.py  —  POST /upload
--------------------------
Accepts multipart file uploads from the frontend.
Each file is:
  1. Saved to disk under uploads/<session_id>/
  2. Parsed into (page, text) pairs
  3. Chunked and stored in the in-memory document store

Returns JSON: { message, files }
"""

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from typing import List
import os
import shutil

from app.models.schemas import UploadResponse
from app.services.parser import parse_file
from app.services.document_store import store_document

router = APIRouter()

# Upload root is two levels up from this file (project root / uploads)
_HERE = os.path.dirname(os.path.abspath(__file__))
UPLOAD_ROOT = os.path.join(_HERE, "..", "..", "..", "uploads")

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".csv"}
MAX_FILE_SIZE_MB   = 20


@router.post("/upload", response_model=UploadResponse)
async def upload_files(
    session_id: str = Form(...),
    files: List[UploadFile] = File(...),
):
    if not session_id or not session_id.startswith("sess_"):
        raise HTTPException(status_code=400, detail="Invalid session_id.")

    # Per-session upload directory
    session_dir = os.path.join(UPLOAD_ROOT, session_id)
    os.makedirs(session_dir, exist_ok=True)

    processed = []
    errors    = []

    for upload in files:
        filename = upload.filename or "unknown"
        ext = os.path.splitext(filename)[-1].lower()

        # Extension guard
        if ext not in ALLOWED_EXTENSIONS:
            errors.append(f"{filename}: unsupported type")
            continue

        # Read content
        content = await upload.read()

        # Size guard
        if len(content) > MAX_FILE_SIZE_MB * 1024 * 1024:
            errors.append(f"{filename}: exceeds {MAX_FILE_SIZE_MB} MB limit")
            continue

        # Save to disk
        dest_path = os.path.join(session_dir, filename)
        with open(dest_path, "wb") as f:
            f.write(content)

        # Parse → chunk → store
        try:
            pages = parse_file(filename, content)
            if not pages:
                errors.append(f"{filename}: could not extract text")
                continue

            chunk_count = store_document(session_id, filename, pages)
            processed.append(filename)
            print(f"[upload] {filename} → {chunk_count} chunks stored for session {session_id}")

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