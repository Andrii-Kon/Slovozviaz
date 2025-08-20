# app.py
from flask import Flask, render_template, jsonify, request, Response
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date
from dotenv import load_dotenv
from sqlalchemy.dialects.mysql import LONGTEXT
import os
import glob
import json

# ── ENV / конфіг ───────────────────────────────────────────────────────────────
load_dotenv()

app = Flask(__name__)
basedir = os.path.abspath(os.path.dirname(__file__))

instance_path = os.path.join(basedir, "instance")
os.makedirs(instance_path, exist_ok=True)

# DB: спершу DATABASE_URL, інакше локальний SQLite
db_uri = os.getenv("DATABASE_URL")
if not db_uri:
    db_uri = "sqlite:///" + os.path.join(instance_path, "games.db")
app.config["SQLALCHEMY_DATABASE_URI"] = db_uri
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# ── Модель ─────────────────────────────────────────────────────────────────────
class ArchivedGame(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    game_date = db.Column(db.Date, unique=True, nullable=False)
    secret_word = db.Column(db.String(100), nullable=False)
    # для MySQL LONGTEXT, для інших просто Text
    ranking_json = db.Column(db.Text().with_variant(LONGTEXT, "mysql"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ── Дані / словники ────────────────────────────────────────────────────────────
def load_daily_words():
    try:
        with open("daily_words.txt", "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print("Помилка: daily_words.txt не знайдено.")
        return []

def load_wordlist():
    try:
        with open("wordlist.txt", "r", encoding="utf-8") as f:
            return {line.strip().lower() for line in f if line.strip()}
    except FileNotFoundError:
        print("Помилка: wordlist.txt не знайдено.")
        return set()

DAILY_WORDS = load_daily_words()
VALID_WORDS = load_wordlist()
BASE_DATE = date(2025, 6, 2)
PRECOMPUTED_DIR = os.path.join(basedir, "precomputed")

# ── Утиліти імпорту ────────────────────────────────────────────────────────────
def _secret_for_date(d: date) -> str:
    if not DAILY_WORDS:
        return ""
    idx = (d - BASE_DATE).days % len(DAILY_WORDS)
    return DAILY_WORDS[idx]

def import_all_precomputed(commit_every: int = 200):
    """Імпорт/оновлення ВСІХ precomputed/*.json у БД. Повертає статистику."""
    if not os.path.isdir(PRECOMPUTED_DIR):
        print(f"[Імпорт] Директорія відсутня: {PRECOMPUTED_DIR}")
        return (0, 0, 0, 0)

    paths = sorted(glob.glob(os.path.join(PRECOMPUTED_DIR, "*.json")))
    if not paths:
        print(f"[Імпорт] JSON-файлів не знайдено у {PRECOMPUTED_DIR}")
        return (0, 0, 0, 0)

    added = replaced = skipped = errors = 0

    for i, path in enumerate(paths, 1):
        fname = os.path.basename(path)
        name_no_ext = os.path.splitext(fname)[0]

        # 1) дата з назви файлу
        try:
            game_date = datetime.strptime(name_no_ext, "%Y-%m-%d").date()
        except ValueError:
            print(f"[WARN] Пропуск некоректної назви файлу: {fname}")
            skipped += 1
            continue

        # 2) читання JSON
        try:
            with open(path, "r", encoding="utf-8") as f:
                ranking = json.load(f)
            if not isinstance(ranking, list):
                raise ValueError("ranking is not a list")
        except Exception as e:
            print(f"[ERR] {fname}: не можу прочитати/розпарсити JSON: {e}")
            errors += 1
            continue

        # 3) upsert
        try:
            secret_word = _secret_for_date(game_date)
            row = ArchivedGame.query.filter_by(game_date=game_date).first()
            if row:
                row.secret_word = secret_word or row.secret_word
                row.ranking_json = json.dumps(ranking, ensure_ascii=False)
                replaced += 1
            else:
                db.session.add(
                    ArchivedGame(
                        game_date=game_date,
                        secret_word=secret_word,
                        ranking_json=json.dumps(ranking, ensure_ascii=False),
                    )
                )
                added += 1

            if (added + replaced) % commit_every == 0:
                db.session.commit()

        except Exception as e:
            db.session.rollback()
            print(f"[ERR] DB операція для {fname}: {e}")
            errors += 1

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"[ERR] Остаточний commit: {e}")

    print(f"[Імпорт завершено] додано: {added}, оновлено: {replaced}, пропущено: {skipped}, помилок: {errors}")
    return (added, replaced, skipped, errors)

# ── Одноразова ініціалізація при першому запиті ───────────────────────────────
@app.before_request
def bootstrap_once():
    """Створює таблиці і одноразово імпортує усі JSON (якщо ще не імпортовано)."""
    if app.config.get("_BOOTSTRAPPED"):
        return
    with app.app_context():
        db.create_all()
        # Імпортуємо, якщо є директорія precomputed і БД порожня,
        # або якщо хочемо завжди оновити — можна прибрати перевірку count==0
        try:
            count = ArchivedGame.query.count()
        except Exception:
            count = 0
        if os.path.isdir(PRECOMPUTED_DIR) and count == 0:
            import_all_precomputed()
    app.config["_BOOTSTRAPPED"] = True

# ── Маршрути ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/ranked")
@app.route("/api/ranked")
def get_ranked():
    d = request.args.get("date")
    if d:
        try:
            target = datetime.strptime(d, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"error": "Невірний формат дати. Використовуйте YYYY-MM-DD."}), 400
    else:
        target = date.today()

    row = ArchivedGame.query.filter_by(game_date=target).first()
    if not row:
        return jsonify({"error": f"Рейтинг для {target.isoformat()} не знайдено."}), 404

    try:
        data = json.loads(row.ranking_json)
        if not isinstance(data, list):
            return jsonify({"error": "Невірний формат даних рейтингу на сервері."}), 500
        return jsonify(data)
    except Exception:
        return jsonify({"error": "Помилка даних на сервері."}), 500

@app.route("/api/wordlist")
def wordlist_api():
    return jsonify(sorted(list(VALID_WORDS)))

@app.route("/api/daily-index")
def daily_index():
    if not DAILY_WORDS:
        return jsonify({"error": "Список щоденних слів не завантажено."}), 500
    delta = (date.today() - BASE_DATE).days
    return jsonify({"game_number": delta + 1})

@app.route("/archive")
def archive_list():
    games = ArchivedGame.query.order_by(ArchivedGame.game_date.desc()).all()
    return jsonify([g.game_date.isoformat() for g in games])

@app.route("/archive/<string:game_date_str>")
def archive_by_date(game_date_str):
    try:
        d = datetime.strptime(game_date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Невірний формат дати. Використовуйте YYYY-MM-DD."}), 400

    row = ArchivedGame.query.filter_by(game_date=d).first()
    if not row:
        return jsonify({"error": f"Гру для {game_date_str} не знайдено."}), 404

    try:
        ranking = json.loads(row.ranking_json)
        if not isinstance(ranking, list):
            raise ValueError("Ranking data is not a list")
        return jsonify({
            "game_date": row.game_date.isoformat(),
            "ranking": ranking,
            "created_at": row.created_at.isoformat() if row.created_at else None
        })
    except Exception as e:
        print(f"Помилка даних для гри {game_date_str}: {e}")
        return jsonify({"error": "Помилка даних для цієї гри."}), 500

# SEO / Політика
@app.route("/privacy.html")
def privacy_policy():
    return render_template("privacy.html")

@app.route("/robots.txt")
def robots_txt():
    sitemap_url = request.url_root.rstrip('/') + '/sitemap.xml'
    return Response(f"User-agent: *\nAllow: /\n\nSitemap: {sitemap_url}", mimetype='text/plain')

@app.route("/sitemap.xml")
def sitemap_xml():
    base_url = request.url_root.rstrip('/')
    last_mod = date.today().isoformat()
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{base_url}/</loc><lastmod>{last_mod}</lastmod><changefreq>daily</changefreq><priority>1.0</priority></url>
  <url><loc>{base_url}/privacy.html</loc><lastmod>{last_mod}</lastmod><changefreq>monthly</changefreq><priority>0.5</priority></url>
</urlset>"""
    return Response(xml, mimetype="application/xml")

# Локальний запуск (python app.py) — не потрібен, якщо використовуєш `flask run`,
# але не завадить для прямого запуску скрипта.
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        if os.path.isdir(PRECOMPUTED_DIR) and ArchivedGame.query.count() == 0:
            import_all_precomputed()
    app.run(debug=True)
