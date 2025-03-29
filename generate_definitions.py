import openai
import json
import os
import time
from dotenv import load_dotenv

load_dotenv()

client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))  # ключ береться з .env

# Імена файлів
INPUT_FILE = "wordlist.txt"       # Файл із вхідними словами
OUTPUT_FILE = "definitions.json"  # Файл для збереження визначень

# Завантаження вже існуючих визначень, якщо є
if os.path.exists(OUTPUT_FILE):
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        definitions = json.load(f)
else:
    definitions = {}

# Зчитування слів з файлу
with open(INPUT_FILE, "r", encoding="utf-8") as f:
    words = [line.strip() for line in f if line.strip()]

# Цикл генерації визначень
for word in words:
    if word in definitions:
        print(f"✅ Визначення для '{word}' вже є. Пропускаємо.")
        continue

    prompt = f"Дай коротке визначення українською мовою для слова '{word}' в одному реченні."
    print(f"🔄 Генеруємо визначення для: {word}")

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Ти помічник, який створює короткі визначення українською мовою."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.5,
            max_tokens=50
        )

        definition = response.choices[0].message.content.strip()
        definitions[word] = definition
        print(f"📌 '{word}': {definition}")

        # Оновлюємо JSON-файл після кожного запиту
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(definitions, f, ensure_ascii=False, indent=2)

        # Пауза, щоб уникнути перевантаження API
        time.sleep(0.1)

    except Exception as e:
        print(f"❌ Помилка для '{word}': {e}")
        time.sleep(5)

print("✅ Генерація визначень завершена!")
