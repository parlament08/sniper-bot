import os
import google.generativeai as genai

genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

print("Доступные модели для твоего ключа:")
for m in genai.list_models():
    if 'generateContent' in m.supported_generation_methods:
        print(m.name)