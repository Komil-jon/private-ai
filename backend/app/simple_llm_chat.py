from google import genai
import os
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("API_KEY")

client = genai.Client(api_key=API_KEY)

input_text = ""
chat_history = []
print("Hello, how can I help you?")

while input_text != "quit":
    input_text = input()
    if input_text == "quit":
        break
    else:
        chat_history.append(input_text)
        interaction = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=str(chat_history)
        )
        print(interaction.text)
        if len(chat_history) > 9:
            chat_history = chat_history[:9]
        chat_history.append(interaction.text)
