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

# Перевірка API ключа
api_key = os.environ.get("OPENAI_API_KEY")
if not api_key:
    print("❌ Помилка: Змінна середовища OPENAI_API_KEY не встановлена.")
    print("Будь ласка, створіть файл .env з вашим ключем або встановіть змінну середовища.")
    exit(1)

try:
    client = openai.OpenAI(api_key=api_key)
except Exception as e:
    print(f"❌ Помилка ініціалізації OpenAI клієнта: {e}")
    exit(1)

INPUT_FILE = "wordlist.txt"
# Повертаємося до моделі, яка не викликала помилку типу винятку, для стабільності.
# Якщо ви впевнені в ID "o4-mini" і вирішили проблему з квотою, можете змінити.
MODEL_NAME = "gpt-4.1-mini" # Або "o4-mini", якщо впевнені в ID та квоті
OUTPUT_FILE = "definitions_4.1o.json"

# Використовуємо глобальний словник і блокування
definitions = {}
lock = threading.Lock()

# Завантаження існуючих визначень
if os.path.exists(OUTPUT_FILE):
    if os.path.getsize(OUTPUT_FILE) > 0:
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                definitions = json.load(f)
            print(f"ℹ️  Завантажено {len(definitions)} існуючих визначень з {OUTPUT_FILE}.")
        except json.decoder.JSONDecodeError:
            print(f"⚠️  Файл {OUTPUT_FILE} має невірний формат JSON. Використовуємо порожній словник.")
            definitions = {}
    else:
        print(f"ℹ️  Файл {OUTPUT_FILE} порожній. Використовуємо порожній словник.")
        definitions = {}
else:
    print(f"ℹ️  Файл {OUTPUT_FILE} не знайдено. Буде створено новий.")
    definitions = {}

# Завантаження слів
if not os.path.exists(INPUT_FILE):
    print(f"❌ Помилка: Вхідний файл '{INPUT_FILE}' не знайдено.")
    exit(1)
try:
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        words_from_file = [line.strip() for line in f if line.strip()]
        unique_words = []
        seen_words = set()
        for word in words_from_file:
            if word not in seen_words:
                unique_words.append(word)
                seen_words.add(word)
        words = unique_words
    print(f"ℹ️  Знайдено {len(words)} унікальних слів у {INPUT_FILE} для обробки.")
except Exception as e:
    print(f"❌ Помилка читання файлу {INPUT_FILE}: {e}")
    exit(1)


def remove_word_repetition(word, definition):
    pattern = re.compile(rf"^\s*{re.escape(word)}\s*(—|-|:)?\s*", re.IGNORECASE)
    cleaned_definition = pattern.sub("", definition, count=1).strip()
    if cleaned_definition.startswith('"') and cleaned_definition.endswith('"'):
        cleaned_definition = cleaned_definition[1:-1]
    if cleaned_definition.startswith("'") and cleaned_definition.endswith("'"):
        cleaned_definition = cleaned_definition[1:-1]
    return cleaned_definition.strip()

def save_definition(word, definition):
    with lock:
        definitions[word] = definition
        try:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(definitions, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"❌ Помилка запису у файл {OUTPUT_FILE}: {e}")


def generate_definition(word, max_retries=5):
    system_prompt_text = (
        "Ти висококваліфікований лінгвіст-лексикограф, що створює точні та лаконічні визначення для українського тлумачного словника. "
        "Твоє завдання — для кожного наданого слова дати одне основне, найбільш вживане визначення в одному реченні. "
        "Використовуй сучасну українську літературну мову, уникай зайвих прикметників та описових відтінків. "
        "Не повторюй саме слово на початку визначення. Відповідь має бути лише визначенням або 'NA'.\n\n"
        "Приклади бажаних визначень:\n"
        "Слово: книга\n"
        "Визначення: Друковане або рукописне видання, що складається зі скріплених аркушів паперу чи іншого матеріалу з текстом та/або ілюстраціями.\n"
        "Слово: рок (музичний жанр)\n"
        "Визначення: Напрям популярної музики, що виник у середині XX століття, характерними рисами якого є використання електрогітар та чіткий ритм.\n"
        "Слово: аргумент\n"
        "Визначення: Підстава або доказ, що наводиться для обґрунтування чи підтвердження думки, теорії.\n\n"
        "ВАЖЛИВО: Якщо надане слово є дуже рідковживаним, неологізмом без усталеного значення, очевидною калькою, "
        "жаргонізмом, містить явну орфографічну помилку, є абревіатурою без загальновідомого розшифрування, "
        "або якщо його практично неможливо ідентифікувати як існуюче слово в українській мові з чітким значенням, "
        "то у відповідь напиши лише 'NA'."
    )
    user_prompt_text = (
        f"Надай визначення для українського слова: '{word}'\n"
        "Пам'ятай про інструкцію щодо 'NA' для специфічних випадків."
    )

    attempt = 0
    while attempt < max_retries:
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt_text},
                    {"role": "user", "content": user_prompt_text}
                ],
                temperature=0.2,
                max_tokens=120,
                timeout=45.0
            )
            raw_definition = response.choices[0].message.content.strip()

            if raw_definition.upper() == "NA":
                clean_definition = "NA"
            else:
                clean_definition = remove_word_repetition(word, raw_definition)
                if not clean_definition and raw_definition.upper() != "NA":
                    print(f"⚠️  Для '{word}' отримано порожнє визначення після очищення: '{raw_definition}'. Позначаємо як NA.")
                    clean_definition = "NA"

            # print(f"📌 '{word}': {clean_definition}") # Перемістив вивід у main для кращого контролю над прогресом
            return word, clean_definition

        except openai.Timeout as e_timeout:
            print(f"⌛ Таймаут для '{word}' після 45с: {e_timeout}. Спроба {attempt + 1}/{max_retries}")
            attempt += 1
            if attempt >= max_retries:
                return word, "NA_TIMEOUT"
            time.sleep(min(60, 5 * (2**attempt))) # Збільшена затримка
        except openai.RateLimitError as e_rate_limit:
            # Спеціальна обробка для insufficient_quota всередині RateLimitError
            if e_rate_limit.status_code == 429 and "insufficient_quota" in str(e_rate_limit.body).lower():
                print(f"🚫 Недостатньо квоти для '{word}': {e_rate_limit.body.get('message', str(e_rate_limit))}. Зупинка.")
                return word, "NA_QUOTA_ERROR"

            wait_time = min(120, 20 * (2 ** attempt))
            print(f"⏳ RateLimit для '{word}': {e_rate_limit}. Чекаємо {wait_time}с ({attempt + 1}/{max_retries}).")
            time.sleep(wait_time)
            attempt += 1
        except openai.APIStatusError as e_api_status: # Більш конкретний тип для помилок статусу API
            if e_api_status.status_code == 429 and "insufficient_quota" in str(e_api_status.response.text).lower():
                print(f"🚫 Недостатньо квоти для '{word}': {e_api_status.response.json().get('error', {}).get('message', '')}. Зупинка.")
                return word, "NA_QUOTA_ERROR"

            print(f"❌ APIStatusError для '{word}' (статус {e_api_status.status_code}, спроба {attempt + 1}/{max_retries}): {e_api_status}")
            attempt += 1
            if attempt < max_retries:
                time.sleep(min(60, 10 * (2 ** attempt)))
            else:
                return word, f"NA_API_STATUS_ERROR_{e_api_status.status_code}"
        except openai.APIConnectionError as e_api_conn:
            print(f"❌ APIConnectionError для '{word}' (спроба {attempt + 1}/{max_retries}): {e_api_conn}")
            attempt += 1
            if attempt < max_retries:
                time.sleep(min(60, 10 * (2 ** attempt)))
            else:
                return word, "NA_API_CONNECTION_ERROR"
        except openai.APIError as e_api: # Загальний APIError, якщо не зловлено вище
            print(f"❌ Загальна APIError для '{word}' (спроба {attempt + 1}/{max_retries}): {e_api}")
            attempt += 1
            if attempt < max_retries:
                time.sleep(min(60, 10 * (2 ** attempt)))
            else:
                return word, "NA_GENERAL_API_ERROR"
        except Exception as e_unknown:
            print(f"❌ Невідома помилка в generate_definition для '{word}': {e_unknown} (Тип: {type(e_unknown)})")
            return word, "NA_UNKNOWN_ERROR_FUNC"

    return word, "NA_MAX_RETRIES"

def main():
    # Переобробляємо слова, які раніше отримали помилку або спеціальну "NA" позначку
    to_process = [word for word in words if word not in definitions or definitions.get(word, "").startswith("NA_")]

    if not to_process:
        print(f"✅ Усі слова з {INPUT_FILE} вже мають визначення у {OUTPUT_FILE} або не потребують переобробки.")
        return

    print(f"🚀 Залишилось обробити: {len(to_process)} слів.")
    start_time = time.time()

    processed_count = 0
    total_to_process = len(to_process)

    # Для gpt-4.1-mini можна спробувати 3-5 воркерів, для o4-mini краще почати з 2.
    # Якщо використовуєте o4-mini, встановіть max_workers=2
    max_w = 3 if "gpt-4.1-mini" in MODEL_NAME else 2

    quota_error_encountered = False

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_w) as executor:
        future_to_word = {executor.submit(generate_definition, word): word for word in to_process}

        active_futures = list(future_to_word.keys())

        while active_futures:
            # Чекаємо на завершення наступного future
            done, active_futures = concurrent.futures.wait(
                active_futures,
                return_when=concurrent.futures.FIRST_COMPLETED
            )

            for future in done:
                word_from_future = future_to_word[future]
                definition_result = None # Ініціалізуємо
                try:
                    _, definition_result = future.result()

                    if definition_result == "NA_QUOTA_ERROR":
                        print(f"🚫🚫🚫 Виявлено помилку недостатньої квоти для '{word_from_future}'. Зупиняю подальшу обробку.")
                        quota_error_encountered = True
                        # Скасовуємо решту завдань
                        for बाकी_future in active_futures:
                            बाकी_future.cancel()
                        active_futures = [] # Очищуємо список активних, щоб вийти з циклу
                        break # Виходимо з циклу обробки `done` фьючерсів

                    if definition_result is not None:
                        print(f"📌 '{word_from_future}': {definition_result}")
                        save_definition(word_from_future, definition_result)
                    else: # Якщо definition_result is None, що не повинно траплятися з поточним кодом generate_definition
                        print(f"⚠️ Для '{word_from_future}' отримано None як результат визначення.")
                        save_definition(word_from_future, "NA_UNEXPECTED_NONE")


                except concurrent.futures.CancelledError:
                    print(f"ℹ️ Завдання для '{word_from_future}' було скасовано.")
                except Exception as exc: # Загальний Exception для непередбачених помилок з future.result()
                    print(f"‼️‼️ Слово '{word_from_future}' згенерувало виняток в main: {exc} (Тип: {type(exc)})")
                    save_definition(word_from_future, f"NA_MAIN_EXCEPTION_{type(exc).__name__}")

                if not quota_error_encountered:
                    processed_count += 1
                    if total_to_process > 0:
                        progress = (processed_count / total_to_process) * 100
                        # Виводимо прогрес тут, щоб він був синхронізований з обробкою
                        print(f"📊 Прогрес: {processed_count}/{total_to_process} ({progress:.2f}%)")

            if quota_error_encountered:
                break # Виходимо з основного while-циклу, якщо була помилка квоти

    end_time = time.time()
    print(f"\n🎉 Генерація визначень завершена за {end_time - start_time:.2f} секунд.")
    print(f"📖 Всього визначень у файлі {OUTPUT_FILE}: {len(definitions)}")
    if quota_error_encountered:
        print("🔴 УВАГА: Роботу було зупинено через помилку недостатньої квоти. Перевірте ваш баланс OpenAI.")

if __name__ == "__main__":
    main()