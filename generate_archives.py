import os
import json
from datetime import date, timedelta
from app import app, db, ArchivedGame, BASE_DATE
from generate_rankings import generate_rankings

# -----------------------------------------------------------------------------
# Завантаження вхідних даних
# -----------------------------------------------------------------------------
with open("data/daily_words.txt", "r", encoding="utf-8") as f:
    daily_words = [line.strip() for line in f if line.strip()]

with open("data/wordlist.txt", "r", encoding="utf-8") as f:
    wordlist = [line.strip() for line in f if line.strip()]

with open("definitions.json", "r", encoding="utf-8") as f:
    definitions = json.load(f)

# -----------------------------------------------------------------------------
# Генерація архівних ігор у контексті Flask-додатку
# -----------------------------------------------------------------------------
with app.app_context():
    # Створюємо таблиці (якщо відсутні)
    db.create_all()

    # Проходимо усі слова з daily_words.txt і генеруємо архів на кожен день
    for index, word in enumerate(daily_words):
        archive_date = BASE_DATE + timedelta(days=index)
        print(f"[ARCHIVE] Генеруємо для {archive_date} -> {word}")

        # Пропускаємо, якщо запис для цієї дати вже існує
        exists = ArchivedGame.query.filter_by(game_date=archive_date).first()
        if exists:
            print(f"Пропуск: рейтинг для {word} (гра: {archive_date}) вже згенеровано.")
            continue

        # Генеруємо рейтинг для секретного слова на вказану дату
        ranked = generate_rankings(word, archive_date, definitions, wordlist)

        # Додаємо архівну гру до БД
        db.session.add(ArchivedGame(
            game_date=archive_date,
            secret_word=word,
            ranking_json=json.dumps(ranked, ensure_ascii=False)
        ))

    # Фіксуємо всі зміни в БД
    db.session.commit()
    print("Архівні ігри згенеровано та збережено в базі для всіх слів із daily_words.txt.")
