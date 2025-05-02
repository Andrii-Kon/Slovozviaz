import openai
import json
import os
import re
import time
import concurrent.futures
import threading
from dotenv import load_dotenv

load_dotenv()

# Вивід поточної робочої директорії
print("Поточна робоча директорія:", os.getcwd())

client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

INPUT_FILE = "wordlist.txt"
OUTPUT_FILE = "definitions.json"

# Використовуємо глобальний словник і блокування
definitions = {}
lock = threading.Lock()

# Якщо файл існує, завантажуємо його
if os.path.exists(OUTPUT_FILE) and os.path.getsize(OUTPUT_FILE) > 0:
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            definitions = json.load(f)
    except json.decoder.JSONDecodeError:
        print("⚠️  Файл definitions.json має невірний формат. Використовуємо порожній словник.")
        definitions = {}

with open(INPUT_FILE, "r", encoding="utf-8") as f:
    words = [line.strip() for line in f if line.strip()]

def remove_word_repetition(word, definition):
    """
    Видаляє повторення самого слова на початку визначення.
    Наприклад, для слова "рік" видаляє "рік —", "рік:" або "рік -".
    """
    pattern = re.compile(rf"^\s*{re.escape(word)}\s*(—|-|:)?\s*", re.IGNORECASE)
    return pattern.sub("", definition, count=1)

def save_definition(word, definition):
    """
    Додає визначення до глобального словника та записує у файл.
    Огорнуто в lock, щоб одночасний доступ не зіпсував файл.
    """
    with lock:
        definitions[word] = definition
        # Записуємо словник у файл
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(definitions, f, ensure_ascii=False, indent=2)

def generate_definition(word, max_retries=5):
    prompt = (
        f"Дай коротке, лаконічне визначення для слова '{word}' українською мовою в одному реченні. "
        "Визначення повинно точно описувати основне семантичне значення слова, його ключові характеристики, "
        "без зайвих прикметників та додаткових деталей. Не повторюй саме слово на початку визначення."
    )
    attempt = 0
    while attempt < max_retries:
        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": (
                        "Ти експерт зі створення коротких, лаконічних визначень українською мовою. "
                        "Твоє завдання — давати визначення, що точно описують основне семантичне значення слова в одному реченні. "
                        "Використовуй просту і зрозумілу мову, без зайвих прикметників і описових відтінків. "
                        "Не повторюй слово, для якого генерується визначення, на початку відповіді."
                    )},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.5,
                max_tokens=100  # збільшено з 50 до 100
            )
            raw_definition = response.choices[0].message.content.strip()
            clean_definition = remove_word_repetition(word, raw_definition)
            print(f"📌 '{word}': {clean_definition}")
            return word, clean_definition

        except Exception as e:
            if "rate_limit_exceeded" in str(e):
                wait_time = 10 * (2 ** attempt)  # експоненціальний backoff
                print(f"❌ Помилка для '{word}': {e}. Чекаємо {wait_time} секунд перед повторною спробою.")
                time.sleep(wait_time)
                attempt += 1
            else:
                print(f"❌ Помилка для '{word}': {e}. Пропускаємо це слово.")
                return word, None

    print(f"❌ Не вдалося згенерувати визначення для '{word}' після {max_retries} спроб.")
    return word, None

def main():
    to_process = [word for word in words if word not in definitions]
    print(f"Залишилось обробити: {len(to_process)} слів.")
    start_time = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(generate_definition, word): word for word in to_process}
        for future in concurrent.futures.as_completed(futures):
            word, definition = future.result()
            if definition:
                save_definition(word, definition)

    end_time = time.time()
    print(f"\nГенерація визначень завершена за {end_time - start_time:.2f} секунд.")

if __name__ == "__main__":
    main()