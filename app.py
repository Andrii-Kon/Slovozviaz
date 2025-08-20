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

# Якщо є DATABASE_URL (наприклад, на PythonAnywhere) — використовуємо його.
# Інакше — локальний SQLite у папці instance/
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
                        print(f"Помилка: Неможливо визначити секретне слово для {current_date}, список DAILY_WORDS порожній.")
                        current_date += timedelta(days=1)
                        continue

                    if delta_days >= len(DAILY_WORDS) and len(DAILY_WORDS) > 0:
                        word_index = delta_days % len(DAILY_WORDS)
                    elif delta_days < len(DAILY_WORDS):
                        word_index = delta_days
                    else:
                        print(f"Помилка: Неможливо обрати слово, DAILY_WORDS порожній або індекс поза межами для {current_date}")
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
                            print(f"Помилка: Не вдалося розпарсити JSON для {current_date} з файлу {archive_path}.")
                        except Exception as e:
                            print(f"Помилка при обробці архіву для {current_date}: {e}")
                    else:
                        print(f"Попередження: Файл архіву {archive_path} для {current_date} не знайдено.")
                current_date += timedelta(days=1)

            if games_added:
                db.session.commit()
    except Exception as e:
        if db.session.is_active:
            db.session.rollback()
        print(f"Відбулася помилка в ensure_games_archived, виконано rollback. Помилка: {e}")

# ── Маршрути ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    """Головна сторінка гри."""
    return render_template("index.html")

@app.route("/ranked")
@app.route("/api/ranked")
def get_ranked():
    """Повертає рейтинг слів для сьогоднішньої гри або заданої дати."""
    target_date_str = request.args.get('date')
    if target_date_str:
        try:
            target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"error": "Невірний формат дати. Використовуйте YYYY-MM-DD."}), 400
    else:
        target_date = date.today()

    game = ArchivedGame.query.filter_by(game_date=target_date).first()
    if game:
        try:
            ranking_data = json.loads(game.ranking_json)
            if isinstance(ranking_data, list):
                return jsonify(ranking_data)
            else:
                print(f"Помилка: рейтинг для {target_date} не є списком.")
                return jsonify({"error": "Невірний формат даних рейтингу на сервері."}), 500
        except json.JSONDecodeError:
            print(f"Помилка: Не вдалося розпарсити JSON рейтингу для {target_date}.")
            return jsonify({"error": "Помилка даних на сервері."}), 500
    else:
        print(f"Помилка: Дані рейтингу для {target_date} не знайдено в базі.")
        ensure_games_archived()  # разова спроба дозаповнити
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
    """Повертає відсортований список дозволених слів."""
    return jsonify(sorted(list(VALID_WORDS)))

@app.route("/api/daily-index")
def daily_index():
    """Повертає номер сьогоднішньої гри."""
    if not DAILY_WORDS:
        return jsonify({"error": "Список щоденних слів не завантажено."}), 500
    delta_days = (date.today() - BASE_DATE).days
    game_number = delta_days + 1
    return jsonify({"game_number": game_number})

@app.route("/archive")
def archive_list():
    """Повертає список дат доступних архівних ігор."""
    games = ArchivedGame.query.order_by(ArchivedGame.game_date.desc()).all()
    valid_games = [g.game_date.isoformat() for g in games if g.game_date <= date.today()]
    return jsonify(valid_games)

@app.route("/archive/<string:game_date_str>")
def archive_by_date(game_date_str):
    """Повертає дані конкретної архівної гри за датою."""
    try:
        target_date = datetime.strptime(game_date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Невірний формат дати. Використовуйте YYYY-MM-DD."}), 400

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
        print(f"Помилка даних для гри {game_date_str}: {e}")
        return jsonify({"error": "Помилка даних для цієї гри."}), 500

# === МАРШРУТ ДЛЯ СТОРІНКИ ПОЛІТИКИ КОНФІДЕНЦІЙНОСТІ ===
@app.route("/privacy.html")
def privacy_policy():
    """Відображає сторінку Політики Конфіденційності."""
    return render_template("privacy.html")

# === МАРШРУТИ ДЛЯ SEO ===
@app.route('/robots.txt')
def robots_txt():
    """Генерує вміст файлу robots.txt."""
    sitemap_url = request.url_root.rstrip('/') + '/sitemap.xml'
    content = f"""User-agent: *
Allow: /

Sitemap: {sitemap_url}"""
    return Response(content, mimetype='text/plain')

@app.route('/sitemap.xml')
def sitemap_xml():
    """Генерує вміст файлу sitemap.xml."""
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
