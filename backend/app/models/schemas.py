from pydantic import BaseModel
from typing import List, Optional


class Message(BaseModel):
    role: str
    content: str


class ProcessRequest(BaseModel):
    type: str
    data: List[Message]
    id: str  # session_id from frontend localStorage


class SourceChip(BaseModel):
    """One cited document chunk returned alongside an AI answer."""
    title: str          # filename shown in the UI chip
    page: Optional[int] = None   # page number if available (PDF)
    chunk: Optional[int] = None  # chunk index within the file


class ProcessResponse(BaseModel):
    response: str
    sources: List[SourceChip] = []


class UploadResponse(BaseModel):
    message: str
    files: List[str]    # list of filenames successfully processed