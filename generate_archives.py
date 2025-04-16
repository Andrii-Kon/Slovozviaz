import os
import json
from datetime import date, timedelta
from app import app, db, ArchivedGame, BASE_DATE
from generate_rankings import generate_rankings

# Завантаження слів
with open("fornow.txt", "r", encoding="utf-8") as f:
    daily_words = [line.strip() for line in f if line.strip()]

with open("wordlist.txt", "r", encoding="utf-8") as f:
    wordlist = [line.strip() for line in f if line.strip()]

with open("definitions.json", "r", encoding="utf-8") as f:
    definitions = json.load(f)

# ⚠️ Виконуємо у контексті Flask-додатку
with app.app_context():
    db.create_all()

    # Генеруємо рейтинги для всіх слів із файлу fornow.txt
    for index, word in enumerate(daily_words):
        archive_date = BASE_DATE + timedelta(days=index)
        print(f"[ARCHIVE] Генеруємо для {archive_date} -> {word}")

        # Якщо архів для цієї дати вже існує, пропускаємо генерацію
        exists = ArchivedGame.query.filter_by(game_date=archive_date).first()
        if exists:
            print(f"⏭️ Рейтинг для {word} (гра: {archive_date}) вже згенерований, пропускаємо.")
            continue

        # Генеруємо рейтинг для секретного слова
        ranked = generate_rankings(word, archive_date, definitions, wordlist)

        # Додаємо до БД нову архівну гру, якщо така ще не існує
        db.session.add(ArchivedGame(
            game_date=archive_date,
            secret_word=word,
            ranking_json=json.dumps(ranked, ensure_ascii=False)
        ))

    db.session.commit()
    print("✅ Архівні ігри успішно згенеровано та збережено в БД для всіх слів з fornow.txt.")
