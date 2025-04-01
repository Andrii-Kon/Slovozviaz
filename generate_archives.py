import os
import random
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

# ⚠️ Працюємо в контексті Flask-додатку
with app.app_context():
    db.create_all()

    # Випадкові 20 слів
    selected = random.sample(list(enumerate(daily_words)), k=20)

    for index, word in selected:
        archive_date = BASE_DATE + timedelta(days=index)
        print(f"[ARCHIVE] Генеруємо для {archive_date} -> {word}")

        # Генеруємо рейтинг
        ranked = generate_rankings(word, archive_date, definitions, wordlist)

        # Додаємо до БД, якщо ще нема
        exists = ArchivedGame.query.filter_by(game_date=archive_date).first()
        if not exists:
            db.session.add(ArchivedGame(
                game_date=archive_date,
                secret_word=word,
                ranking_json=json.dumps(ranked, ensure_ascii=False)
            ))

    db.session.commit()
    print("✅ 20 архівних ігор успішно згенеровано та збережено в БД.")
