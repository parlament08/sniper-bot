import os
import time
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted, NotFound, InvalidArgument

# Инициализация API
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise ValueError("❌ Не найден GEMINI_API_KEY. Выполни export GEMINI_API_KEY='твой_ключ'")

genai.configure(api_key=api_key)

# Твой список доступных моделей
models_to_test = [
    "models/gemini-2.5-flash", "models/gemini-2.5-pro", "models/gemini-2.0-flash",
    "models/gemini-2.0-flash-001", "models/gemini-2.0-flash-lite-001", "models/gemini-2.0-flash-lite",
    "models/gemini-2.5-flash-preview-tts", "models/gemini-2.5-pro-preview-tts", "models/gemma-4-26b-a4b-it",
    "models/gemma-4-31b-it", "models/gemini-flash-latest", "models/gemini-flash-lite-latest",
    "models/gemini-pro-latest", "models/gemini-2.5-flash-lite", "models/gemini-2.5-flash-image",
    "models/gemini-3-pro-preview", "models/gemini-3-flash-preview", "models/gemini-3.1-pro-preview",
    "models/gemini-3.1-pro-preview-customtools", "models/gemini-3.1-flash-lite-preview", "models/gemini-3.1-flash-lite",
    "models/gemini-3-pro-image-preview", "models/gemini-3-pro-image", "models/nano-banana-pro-preview",
    "models/gemini-3.1-flash-image-preview", "models/gemini-3.1-flash-image", "models/gemini-3.1-flash-lite-image",
    "models/gemini-3.5-flash", "models/gemini-omni-flash-preview", "models/lyria-3-clip-preview",
    "models/lyria-3-pro-preview", "models/gemini-3.1-flash-tts-preview", "models/gemini-robotics-er-1.5-preview",
    "models/gemini-robotics-er-1.6-preview", "models/gemini-2.5-computer-use-preview-10-2025",
    "models/antigravity-preview-05-2026", "models/deep-research-max-preview-04-2026",
    "models/deep-research-preview-04-2026", "models/deep-research-pro-preview-12-2025"
]

working_models = []

print("🚀 Начинаем стресс-тест моделей на наличие бесплатных квот...\n")

for model_name in models_to_test:
    print(f"⏳ Проверка {model_name:<45}", end="")
    try:
        model = genai.GenerativeModel(model_name)
        # Отправляем микро-запрос для проверки пропускной способности
        response = model.generate_content("ping")
        print("✅ РАБОТАЕТ (Квота есть)")
        working_models.append(model_name)
    except ResourceExhausted:
        print("❌ БЛОК (Лимит 0 или исчерпана квота)")
    except (NotFound, InvalidArgument) as e:
        print("⚠️ ОШИБКА (Не поддерживает текстовую генерацию)")
    except Exception as e:
        print(f"⚠️ ОШИБКА: {type(e).__name__}")
    
    # Делаем паузу 3 секунды, чтобы нас не забанило по лимиту Requests Per Minute
    time.sleep(3)

print("\n" + "="*50)
print("🎯 ИТОГ: РАБОЧИЕ МОДЕЛИ ДЛЯ БЕСПЛАТНОГО ТАРИФА:")
if working_models:
    for m in working_models:
        print(f" - {m}")
else:
    print("Ни одна модель не пропустила запрос. Free Tier полностью исчерпан.")
print("="*50)