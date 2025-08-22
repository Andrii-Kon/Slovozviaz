# app.py
from flask import Flask, render_template, jsonify, request, Response
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date
from dotenv import load_dotenv
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import load_only  # ⬅ додано
import os
import json

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
        .options(load_only(ArchivedGame.game_date))  # ⬅ ключ до швидкості
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

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
