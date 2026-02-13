import json
from datetime import timedelta

from app import app, db, ArchivedGame, BASE_DATE
from generate_rankings import generate_rankings, load_embedding_resources


def load_lines(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def main() -> None:
    daily_words = load_lines("data/daily_words.txt")
    wordlist = load_lines("data/wordlist.txt")
    resources = load_embedding_resources(words=wordlist, daily_words=daily_words)

    with app.app_context():
        db.create_all()

        for index, word in enumerate(daily_words):
            archive_date = BASE_DATE + timedelta(days=index)
            print(f"[ARCHIVE] Генеруємо для {archive_date} -> {word}")

            exists = ArchivedGame.query.filter_by(game_date=archive_date).first()
            if exists:
                print(f"Пропуск: рейтинг для {word} (гра: {archive_date}) вже згенеровано.")
                continue

            ranked = generate_rankings(
                target_word=word,
                target_date=archive_date,
                definitions=None,
                words=wordlist,
                resources=resources,
            )

            db.session.add(
                ArchivedGame(
                    game_date=archive_date,
                    secret_word=word,
                    ranking_json=json.dumps(ranked, ensure_ascii=False),
                )
            )

        db.session.commit()
        print("Архівні ігри згенеровано та збережено в базі для всіх слів із daily_words.txt.")


if __name__ == "__main__":
    main()
