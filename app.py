# app.py
from flask import Flask, render_template, jsonify, request, Response
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
from sqlalchemy.dialects.mysql import LONGTEXT
import os
import json

# ── ENV / конфіг БД ─────────────────────────────────────────────────────────────
load_dotenv()

app = Flask(__name__)
basedir = os.path.abspath(os.path.dirname(__file__))

instance_path = os.path.join(basedir, "instance")
os.makedirs(instance_path, exist_ok=True)

# Якщо є DATABASE_URL (наприклад, на сервері) — використовуємо його
db_uri = os.getenv("DATABASE_URL")

# Якщо немає DATABASE_URL, підключаємося напряму до MySQL (PythonAnywhere)
if not db_uri and "PYTHONANYWHERE_DOMAIN" in os.environ:
    db_uri = (
        "mysql+pymysql://AndriiKon:Matimatichka1@@"
        "AndriiKon.mysql.pythonanywhere-services.com/AndriiKon$default"
    )

# Якщо немає — fallback на SQLite (локально)
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
    # Для MySQL використовуємо LONGTEXT (а для SQLite лишається Text)
    ranking_json = db.Column(
        db.Text().with_variant(LONGTEXT, "mysql"),
        nullable=False
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ── Завантаження словників ─────────────────────────────────────────────────────
def load_daily_words():
    """Завантажує список секретних слів для щоденних ігор."""
    try:
        with open("daily_words.txt", "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print("Помилка: Файл daily_words.txt не знайдено.")
        return []

def load_wordlist():
    """Завантажує повний словник дозволених слів."""
    try:
        with open("wordlist.txt", "r", encoding="utf-8") as f:
            return {line.strip().lower() for line in f if line.strip()}
    except FileNotFoundError:
        print("Помилка: Файл wordlist.txt не знайдено.")
        return set()

DAILY_WORDS = load_daily_words()
VALID_WORDS = load_wordlist()
BASE_DATE = date(2025, 6, 2)  # Перевір, що це перший день твоїх даних

# ── Автододавання ігор у БД з precomputed/ *.json ─────────────────────────────
@app.before_request
def ensure_games_archived():
    """Перевіряє та архівує ігри від базової дати до сьогодні, якщо їх немає в БД."""
    try:
        with app.app_context():
            db.create_all()
            today = date.today()
            current_date = BASE_DATE
            games_added = False

            while current_date <= today:
                exists = db.session.query(
                    ArchivedGame.query.filter_by(game_date=current_date).exists()
                ).scalar()
                if not exists:
                    delta_days = (current_date - BASE_DATE).days
                    if not DAILY_WORDS:
                        print(f"Помилка: DAILY_WORDS порожній для {current_date}.")
                        current_date += timedelta(days=1)
                        continue

                    if delta_days >= len(DAILY_WORDS) and len(DAILY_WORDS) > 0:
                        word_index = delta_days % len(DAILY_WORDS)
                    elif delta_days < len(DAILY_WORDS):
                        word_index = delta_days
                    else:
                        current_date += timedelta(days=1)
                        continue

                    secret_word = DAILY_WORDS[word_index]
                    archive_path = os.path.join("precomputed", f"{current_date.isoformat()}.json")

                    if os.path.exists(archive_path):
                        try:
                            with open(archive_path, "r", encoding="utf-8") as f:
                                ranking_data = json.load(f)
                            new_game = ArchivedGame(
                                game_date=current_date,
                                secret_word=secret_word,
                                ranking_json=json.dumps(ranking_data, ensure_ascii=False)
                            )
                            db.session.add(new_game)
                            games_added = True
                            print(f"Гра для {current_date} додана до архіву.")
                        except json.JSONDecodeError:
                            print(f"Помилка JSON для {current_date}.")
                        except Exception as e:
                            print(f"Помилка архіву {current_date}: {e}")
                    else:
                        print(f"Файл {archive_path} не знайдено.")
                current_date += timedelta(days=1)

            if games_added:
                db.session.commit()
    except Exception as e:
        if db.session.is_active:
            db.session.rollback()
        print(f"Rollback в ensure_games_archived. Помилка: {e}")

# ── Маршрути ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/ranked")
@app.route("/api/ranked")
def get_ranked():
    target_date_str = request.args.get('date')
    if target_date_str:
        try:
            target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"error": "Невірний формат дати."}), 400
    else:
        target_date = date.today()

    game = ArchivedGame.query.filter_by(game_date=target_date).first()
    if game:
        try:
            ranking_data = json.loads(game.ranking_json)
            if isinstance(ranking_data, list):
                return jsonify(ranking_data)
            else:
                return jsonify({"error": "Невірний формат даних рейтингу."}), 500
        except json.JSONDecodeError:
            return jsonify({"error": "Помилка JSON рейтингу."}), 500
    else:
        ensure_games_archived()
        game_retry = ArchivedGame.query.filter_by(game_date=target_date).first()
        if game_retry:
            try:
                ranking_data = json.loads(game_retry.ranking_json)
                if isinstance(ranking_data, list):
                    return jsonify(ranking_data)
            except Exception:
                pass
        return jsonify({"error": f"Рейтинг для {target_date.isoformat()} не знайдено."}), 404

@app.route("/api/wordlist")
def wordlist_api():
    return jsonify(sorted(list(VALID_WORDS)))

@app.route("/api/daily-index")
def daily_index():
    if not DAILY_WORDS:
        return jsonify({"error": "Список щоденних слів не завантажено."}), 500
    delta_days = (date.today() - BASE_DATE).days
    game_number = delta_days + 1
    return jsonify({"game_number": game_number})

@app.route("/archive")
def archive_list():
    games = ArchivedGame.query.order_by(ArchivedGame.game_date.desc()).all()
    valid_games = [g.game_date.isoformat() for g in games if g.game_date <= date.today()]
    return jsonify(valid_games)

@app.route("/archive/<string:game_date_str>")
def archive_by_date(game_date_str):
    try:
        target_date = datetime.strptime(game_date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Невірний формат дати."}), 400

    game = ArchivedGame.query.filter_by(game_date=target_date).first()
    if not game:
        return jsonify({"error": f"Гру для {game_date_str} не знайдено."}), 404

    try:
        ranking_data = json.loads(game.ranking_json)
        if not isinstance(ranking_data, list):
            raise ValueError("Ranking data is not a list")
        return jsonify({
            "game_date": game.game_date.isoformat(),
            "ranking": ranking_data,
            "created_at": game.created_at.isoformat() if game.created_at else None
        })
    except (json.JSONDecodeError, ValueError) as e:
        return jsonify({"error": "Помилка даних для цієї гри."}), 500

@app.route("/privacy.html")
def privacy_policy():
    return render_template("privacy.html")

@app.route('/robots.txt')
def robots_txt():
    sitemap_url = request.url_root.rstrip('/') + '/sitemap.xml'
    content = f"""User-agent: *
Allow: /

Sitemap: {sitemap_url}"""
    return Response(content, mimetype='text/plain')

@app.route('/sitemap.xml')
def sitemap_xml():
    base_url = request.url_root.rstrip('/')
    last_mod_date = date.today().isoformat()
    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{base_url}/</loc>
    <lastmod>{last_mod_date}</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>{base_url}/privacy.html</loc>
    <lastmod>{last_mod_date}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.5</priority>
  </url>
</urlset>"""
    return Response(content, mimetype='application/xml')

# ── Локальний запуск ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not os.path.exists(instance_path):
        os.makedirs(instance_path)
    with app.app_context():
        db.create_all()
        ensure_games_archived()
    app.run(debug=True)  # debug=True тільки для розробки!