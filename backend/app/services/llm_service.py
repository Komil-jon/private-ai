from google import genai
import os
from dotenv import load_dotenv

load_dotenv()

client = genai.Client(api_key=os.getenv("API_KEY"))

def generate_reply(conversation):
    try:
        # ✅ SYSTEM INSTRUCTIONS (this is your control layer)
        system_prompt = """
You are a helpful AI assistant in a chat application.

Rules:
- Be polite and concise
- Format responses using Markdown (bold, italic, lists when helpful)
- Do NOT answer harmful, illegal, or dangerous questions
- Do NOT provide personal or sensitive information
- If a question is inappropriate, respond with: "PERSONAL"
- If content is unsafe, respond with: "IGNORED"
"""

        formatted_text = system_prompt + "\n\n"

        for msg in conversation:
            role = "User" if msg.role == "user" else "Assistant"
            formatted_text += f"{role}: {msg.content}\n"

        response = client.models.generate_content(
            model="gemini-flash-latest",
            contents=formatted_text
        )

        return response.text

    except Exception as e:
        print("Error:", e)
        return "Sorry, something went wrong."