# app.py
from flask import Flask, render_template, jsonify, request, Response
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date
from dotenv import load_dotenv
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import load_only
from sqlalchemy import inspect
import os
import json
import glob
import re
from typing import List, Dict, Any, Optional

# ── ENV / конфіг ───────────────────────────────────────────────────────────────
load_dotenv()

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False  # коректна UTF-8 відповідь

basedir = os.path.abspath(os.path.dirname(__file__))
instance_path = os.path.join(basedir, "instance")
os.makedirs(instance_path, exist_ok=True)

# DB: на проді беремо з DATABASE_URL (MySQL), локально — SQLite
db_uri = os.getenv("DATABASE_URL")
if not db_uri:
    db_uri = "sqlite:///" + os.path.join(instance_path, "games.db")

app.config["SQLALCHEMY_DATABASE_URI"] = db_uri
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# ── Модель ─────────────────────────────────────────────────────────────────────
class ArchivedGame(db.Model):
    __tablename__ = "archived_game"

    id = db.Column(db.Integer, primary_key=True)
    game_date = db.Column(db.Date, unique=True, nullable=False, index=True)
    secret_word = db.Column(db.String(100), nullable=False)
    ranking_json = db.Column(db.Text().with_variant(LONGTEXT, "mysql"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ── Дані / невеликі словники (опціонально) ─────────────────────────────────────
def load_daily_words():
    try:
        with open("daily_words.txt", "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return []

def load_wordlist():
    try:
        with open("wordlist.txt", "r", encoding="utf-8") as f:
            return {line.strip().lower() for line in f if line.strip()}
    except FileNotFoundError:
        return set()

DAILY_WORDS = load_daily_words()
VALID_WORDS = load_wordlist()

BASE_DATE = date(2025, 6, 2)

# ── Допоміжні для імпорту JSON → SQLite (локально) ────────────────────────────
CANDIDATE_DIRS = ["precomputed", "archive", "data", "rankings", "json"]

DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")  # YYYY-MM-DD з 2000+

def parse_date_from_filename(filepath: str) -> Optional[date]:
    name = os.path.basename(filepath)
    m = DATE_RE.search(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None

def guess_secret_word_for_date(d: date) -> str:
    """Визначити secret_word з daily_words.txt за індексом від BASE_DATE; якщо не вийшло — 'невідомо'."""
    if not DAILY_WORDS:
        return "невідомо"
    idx = (d - BASE_DATE).days
    if 0 <= idx < len(DAILY_WORDS):
        return DAILY_WORDS[idx]
    # fallback: обрізати в межі
    clamped_idx = max(0, min(len(DAILY_WORDS) - 1, idx))
    return DAILY_WORDS[clamped_idx] if DAILY_WORDS else "невідомо"

def iter_json_files() -> List[str]:
    files: List[str] = []
    for d in CANDIDATE_DIRS:
        full = os.path.join(basedir, d)
        if os.path.isdir(full):
            files.extend(glob.glob(os.path.join(full, "*.json")))
    # унікалізація та сортування, щоб стабільно обробляти
    files = sorted(set(files))
    return files

def coerce_ranking(value: Any) -> Optional[List[Any]]:
    """Повертає список-ранкінг або None, якщо формат не підходить."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and "ranking" in value and isinstance(value["ranking"], list):
        return value["ranking"]
    return None

def extract_game_date(value: Any, filepath: str) -> Optional[date]:
    # 1) з JSON об’єкта
    if isinstance(value, dict) and "game_date" in value and isinstance(value["game_date"], str):
        try:
            return datetime.strptime(value["game_date"], "%Y-%m-%d").date()
        except ValueError:
            pass
    # 2) з імені файлу
    return parse_date_from_filename(filepath)

def extract_secret_word(value: Any, game_date: Optional[date]) -> str:
    # 1) безпосередньо із JSON
    if isinstance(value, dict):
        sw = value.get("secret_word")
        if isinstance(sw, str) and sw.strip():
            return sw.strip()
    # 2) якщо є дата — спробувати з daily_words.txt
    if game_date:
        return guess_secret_word_for_date(game_date)
    # 3) fallback
    return "невідомо"

def import_json_into_sqlite_if_needed():
    """Якщо локальний SQLite і таблиця порожня — імпортувати з JSON файлів."""
    with app.app_context():
        engine = db.engine
        is_sqlite = engine.url.drivername.startswith("sqlite")
        insp = inspect(engine)

        # Створити таблиці, якщо їх немає
        if "archived_game" not in insp.get_table_names():
            db.create_all()
            # оновити інспектор після створення
            insp = inspect(engine)

        if not is_sqlite:
            # На проді (MySQL) — нічого не імпортуємо автоматом
            return

        # Перевірити, чи таблиця вже заповнена
        has_any = db.session.query(ArchivedGame.id).first()
        if has_any:
            return  # вже є дані — нічого не робимо

        json_files = iter_json_files()
        if not json_files:
            print("[DB INIT] JSON файлів не знайдено — база порожня, але готова.")
            return

        added = 0
        for path in json_files:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception as e:
                print(f"[DB INIT] Пропускаю '{path}': не вдалося прочитати JSON ({e})")
                continue

            ranking = coerce_ranking(payload)
            if ranking is None:
                print(f"[DB INIT] Пропускаю '{path}': відсутній список 'ranking' або масив.")
                continue

            gdate = extract_game_date(payload, path)
            if not gdate:
                print(f"[DB INIT] Пропускаю '{path}': не вдалося визначити дату гри.")
                continue

            secret_word = extract_secret_word(payload, gdate)

            # Пропустити, якщо вже існує запис на цю дату (на випадок дублів у різних папках)
            exists = ArchivedGame.query.filter_by(game_date=gdate).first()
            if exists:
                continue

            row = ArchivedGame(
                game_date=gdate,
                secret_word=secret_word,
                ranking_json=json.dumps(ranking, ensure_ascii=False)
            )
            db.session.add(row)
            added += 1

        if added:
            try:
                db.session.commit()
                print(f"[DB INIT] Імпортовано ігор: {added}")
            except Exception as e:
                db.session.rollback()
                print(f"[DB INIT] Помилка commit: {e}")
        else:
            print("[DB INIT] Підходящих JSON не знайдено — нічого не додано.")

# ──  Ініціалізація БД при імпорті (працює і для `flask run`)  ──────────────────
with app.app_context():
    # Гарантуємо наявність таблиць
    if "archived_game" not in inspect(db.engine).get_table_names():
        db.create_all()
    # Якщо локальний SQLite і таблиця порожня — імпортуємо з JSON
    import_json_into_sqlite_if_needed()

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
    delta = (date.today() - BASE_DATE).days
    return jsonify({"game_number": delta + 1})

@app.route("/archive")
def archive_list():
    """
    Повертає ТІЛЬКИ список дат.
    ВАЖЛИВО: не вантажимо LONGTEXT ranking_json із MySQL.
    """
    games = (
        ArchivedGame.query
        .options(load_only(ArchivedGame.game_date))
        .order_by(ArchivedGame.game_date.desc())
        .all()
    )
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

# ── Опційно: CLI для ручної ініціалізації ──────────────────────────────────────
try:
    import click

    @app.cli.command("init-db")
    def init_db():
        """Створити всі таблиці БД та (локально) спробувати імпорт із JSON."""
        with app.app_context():
            db.create_all()
            import_json_into_sqlite_if_needed()
        click.echo("DB initialized (and imported from JSON if applicable).")
except Exception:
    # click може бути відсутній у середовищі — ігноруємо
    pass

if __name__ == "__main__":
    # Запуск напряму також гарантує створення/імпорт
    with app.app_context():
        db.create_all()
        import_json_into_sqlite_if_needed()
    app.run(debug=True)
