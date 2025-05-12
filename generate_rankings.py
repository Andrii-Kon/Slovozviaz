import openai
import json
import os
import numpy as np
import time
from tqdm import tqdm
from dotenv import load_dotenv
from datetime import timedelta, date

load_dotenv()

api_key = os.environ.get("OPENAI_API_KEY")
if not api_key:
    raise RuntimeError("Не знайдено змінної оточення OPENAI_API_KEY!")

client = openai.OpenAI(api_key=api_key)

CACHE_FILE = "embeddings_cache.json"
BATCH_SIZE = 100
BASE_DATE = date(2025, 5, 11)

if not os.path.exists("precomputed"):
    os.makedirs("precomputed")

# Load or create cache
if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        embedding_cache = json.load(f)
else:
    embedding_cache = {}

def get_embeddings_batch(phrases, model="text-embedding-3-large"):
    uncached = [p for p in phrases if p not in embedding_cache]

    if uncached:
        for i in range(0, len(uncached), BATCH_SIZE):
            batch = uncached[i:i + BATCH_SIZE]
            print(f"🔄 Надсилаємо batch {i + 1}–{i + len(batch)}")
            response = client.embeddings.create(input=batch, model=model)
            for phrase, obj in zip(batch, response.data):
                embedding_cache[phrase] = obj.embedding

        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(embedding_cache, f, ensure_ascii=False)

    return [embedding_cache[p] for p in phrases]

def cosine_similarity(a, b):
    a = np.array(a)
    b = np.array(b)
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

def generate_rankings(target_word, target_date, definitions, words):
    print(f"[{target_date}] ▶️ Обробка слова: {target_word}")
    target_phrase = definitions.get(target_word, target_word)
    target_emb = get_embeddings_batch([target_phrase])[0]

    phrases = [definitions.get(w, w) for w in words]
    print("📥 Отримуємо ембеддінги для всіх фраз...")
    embeddings = get_embeddings_batch(phrases)

    scored = []
    for word, emb in tqdm(zip(words, embeddings), total=len(words), desc="Обчислюємо схожість"):
        sim = cosine_similarity(emb, target_emb)
        scored.append((word, sim))

    scored.sort(key=lambda x: x[1], reverse=True)
    ranked_words = [{"word": w, "similarity": s, "rank": r} for r, (w, s) in enumerate(scored, start=1)]

    filename = f"precomputed/{target_date}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(ranked_words, f, ensure_ascii=False, indent=2)

    print(f"✅ Збережено у {filename} ({len(ranked_words)} слів)")
    return ranked_words

if __name__ == "__main__":
    with open("daily_words.txt", "r", encoding="utf-8") as f:
        daily_words = [line.strip() for line in f if line.strip()]

    with open("wordlist.txt", "r", encoding="utf-8") as f:
        words = [line.strip() for line in f if line.strip()]

    with open("definitions.json", "r", encoding="utf-8") as f:
        definitions = json.load(f)

    for i, target_word in enumerate(daily_words):
        day = BASE_DATE + timedelta(days=i)
        output_file = f"precomputed/{day}.json"
        if os.path.exists(output_file):
            print(f"⏭️ Пропущено {target_word} (вже існує)")
            continue
        generate_rankings(target_word, day, definitions, words)
