from fastapi import APIRouter
from app.models.schemas import ProcessRequest, ProcessResponse
from app.services.llm_service import generate_reply

router = APIRouter()

@router.post("/process", response_model=ProcessResponse)
async def process_chat(request: ProcessRequest):

    conversation = request.data

    # 🚨 Example safety rules (you can explain this in documentation)
    last_user_msg = conversation[-1].content.lower()

    if "password" in last_user_msg or "hack" in last_user_msg:
        return {"response": "IGNORED"}

    if "who am i" in last_user_msg or "my name" in last_user_msg:
        return {"response": "PERSONAL"}

    # 🤖 Normal AI response
    reply = generate_reply(conversation)

    return {"response": reply}