from app.services import ollama_client

input_text = ""
chat_history = []
print("Hello, how can I help you?")

while input_text != "quit":
    input_text = input()
    if input_text == "quit":
        break
    else:
        chat_history.append(input_text)
        reply = ollama_client.generate(str(chat_history))
        print(reply)
        if len(chat_history) > 9:
            chat_history = chat_history[:9]
        chat_history.append(reply)
