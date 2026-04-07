from pydantic import BaseModel
from typing import List

class Message(BaseModel):
    role: str
    content: str

class ProcessRequest(BaseModel):
    type: str
    data: List[Message]
    id: str

class ProcessResponse(BaseModel):
    response: str