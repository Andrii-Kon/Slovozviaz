# migrate_json_to_db.py
import os
import glob
import json
import argparse
from datetime import date
from typing import Tuple

from app import app, db, ArchivedGame, DAILY_WORDS, BASE_DATE

def choose_secret_word(day: date) -> str:
    if not DAILY_WORDS:
        raise RuntimeError("DAILY_WORDS is empty — неможливо підібрати secret_word.")
    delta_days = (day - BASE_DATE).days
    if delta_days < 0:
        raise RuntimeError(f"Дата {day} раніше BASE_DATE={BASE_DATE}")
    # така ж логіка, як у твоєму app.py
    if delta_days >= len(DAILY_WORDS) and len(DAILY_WORDS) > 0:
        idx = delta_days % len(DAILY_WORDS)
    else:
        idx = delta_days
    return DAILY_WORDS[idx]

def parse_day_from_name(path: str) -> Tuple[date, str]:
    name = os.path.splitext(os.path.basename(path))[0]  # YYYY-MM-DD
    y, m, d = map(int, name.split("-"))
    return date(y, m, d), name

def main():
    ap = argparse.ArgumentParser(description="Міграція precomputed/*.json у таблицю ArchivedGame.")
    ap.add_argument("--dir", default="precomputed", help="Папка з JSON (default: precomputed)")
    ap.add_argument("--dry-run", action="store_true", help="Лише показати план, без запису в БД")
    ap.add_argument("--replace", action="store_true", help="Перезаписувати існуючі дні у БД")
    ap.add_argument("--commit-every", type=int, default=200, help="Коміт кожні N записів (default: 200)")
    args = ap.parse_args()

    src_dir = os.path.abspath(args.dir)
    if not os.path.isdir(src_dir):
        print(f"Folder not found: {src_dir}")
        return

    files = sorted(glob.glob(os.path.join(src_dir, "*.json")))
    print(f"Знайдено {len(files)} файлів у {src_dir}")

    added, replaced, skipped, errors = 0, 0, 0, 0
    planned = 0

    with app.app_context():
        db.create_all()

        for i, path in enumerate(files, 1):
            try:
                day, _ = parse_day_from_name(path)
            except Exception:
                print(f"[SKIP] Некоректна назва (очікував YYYY-MM-DD.json): {os.path.basename(path)}")
                skipped += 1
                continue

            existing = ArchivedGame.query.filter_by(game_date=day).first()
            if existing and not args.replace:
                skipped += 1
                continue

            # читаємо JSON
            try:
                with open(path, "r", encoding="utf-8") as f:
                    ranking = json.load(f)
            except Exception as e:
                print(f"[ERR ] Не вдалося прочитати/розпарсити {path}: {e}")
                errors += 1
                continue

            try:
                secret = choose_secret_word(day)
            except Exception as e:
                print(f"[ERR ] Не вдалося визначити secret_word для {day}: {e}")
                errors += 1
                continue

            planned += 1
            if args.dry_run:
                # у dry-run тільки рахуємо
                continue

            # запис у БД
            try:
                payload = json.dumps(ranking, ensure_ascii=False)
                if existing:
                    existing.secret_word = secret
                    existing.ranking_json = payload
                    replaced += 1
                else:
                    db.session.add(ArchivedGame(
                        game_date=day,
                        secret_word=secret,
                        ranking_json=payload
                    ))
                    added += 1

                if (added + replaced) % args.commit_every == 0:
                    db.session.commit()

            except Exception as e:
                db.session.rollback()
                print(f"[ERR ] DB помилка для {day}: {e}")
                errors += 1

        if not args.dry_run:
            db.session.commit()

        total_in_db = ArchivedGame.query.count()

    # Підсумки
    print("—" * 60)
    print(f"Заплановано до запису: {planned} (dry-run: {args.dry_run})")
    print(f"Додано: {added}, Перезаписано: {replaced}, Пропущено: {skipped}, Помилок: {errors}")
    print(f"Всього записів у БД після операції: {total_in_db}")

if __name__ == "__main__":
    main()
