import openai
import json
import os
import numpy as np
import time
from tqdm import tqdm  # імпортуємо tqdm для прогрес-бару
from dotenv import load_dotenv

load_dotenv()

# Зчитуємо ключ із змінної оточення
api_key = os.environ.get("OPENAI_API_KEY")
if not api_key:
    raise RuntimeError("Не знайдено змінної оточення OPENAI_API_KEY!")

client = openai.OpenAI(api_key=api_key)

CACHE_FILE = "embeddings_cache.json"
BATCH_SIZE = 100  # розмір пакету

# Завантаження/створення кешу ембеддінгів
if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        embedding_cache = json.load(f)
else:
    embedding_cache = {}

def get_embeddings_batch(phrases, model="text-embedding-3-small"):
    # Відфільтруємо ті, що вже в кеші
    uncached = [p for p in phrases if p not in embedding_cache]

    if uncached:
        for i in range(0, len(uncached), BATCH_SIZE):
            batch = uncached[i:i + BATCH_SIZE]
            print(f"🔄 Надсилаємо batch {i + 1}–{i + len(batch)}")
            response = client.embeddings.create(
                input=batch,
                model=model
            )
            for phrase, obj in zip(batch, response.data):
                embedding_cache[phrase] = obj.embedding

        # Збереження оновленого кешу
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(embedding_cache, f, ensure_ascii=False)

    # Повертаємо ембеддінги у тому порядку, як передано
    return [embedding_cache[p] for p in phrases]

def cosine_similarity(a, b):
    a = np.array(a)
    b = np.array(b)
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

if __name__ == "__main__":
    start_time = time.time()

    # Завантаження визначень зі файлу definitions.json
    with open("definitions.json", "r", encoding="utf-8") as f:
        definitions = json.load(f)

    # Визначаємо цільове слово та його фразу (визначення)
    target_word = "історія"
    target_phrase = definitions.get(target_word, target_word)
    print(f"Отримуємо embedding для фрази: {target_phrase}")
    target_emb = get_embeddings_batch([target_phrase])[0]

    # Зчитування слів з файлу wordlist.txt
    with open("wordlist.txt", "r", encoding="utf-8") as f:
        words = [w.strip() for w in f if w.strip()]

    # Формуємо список фраз: якщо є визначення, використовуємо його, інакше саме слово
    phrases = [definitions.get(word, word) for word in words]

    print("📥 Отримуємо ембеддінги для всіх фраз...")
    embeddings = get_embeddings_batch(phrases)

    # Обчислення схожості з прогрес-баром
    temp_list = []
    for word, emb in tqdm(zip(words, embeddings), total=len(words), desc="Обробка слів"):
        sim = cosine_similarity(emb, target_emb)
        temp_list.append((word, sim))

    # Сортування за спаданням схожості
    temp_list.sort(key=lambda x: x[1], reverse=True)

    ranked_words = [
        {"word": w, "similarity": sim, "rank": rank}
        for rank, (w, sim) in enumerate(temp_list, start=1)
    ]

    with open("ranked_words.json", "w", encoding="utf-8") as f:
        json.dump(ranked_words, f, ensure_ascii=False, indent=2)

    print("✅ Файл ranked_words.json створено успішно!")
    elapsed_time = time.time() - start_time
    print(f"Загальний час виконання: {elapsed_time:.2f} секунд")
