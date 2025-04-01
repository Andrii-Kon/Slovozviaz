from flask import Flask, render_template, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date
import os
import json

app = Flask(__name__)
basedir = os.path.abspath(os.path.dirname(__file__))

# Директорія для бази даних
instance_path = os.path.join(basedir, "instance")
os.makedirs(instance_path, exist_ok=True)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(instance_path, "games.db")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

class ArchivedGame(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    game_date = db.Column(db.Date, unique=True, nullable=False)
    secret_word = db.Column(db.String(100), nullable=False)
    ranking_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

def load_daily_words():
    with open("daily_words.txt", "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

def load_wordlist():
    with open("wordlist.txt", "r", encoding="utf-8") as f:
        return {line.strip().lower() for line in f if line.strip()}

DAILY_WORDS = load_daily_words()
VALID_WORDS = load_wordlist()
BASE_DATE = date(2025, 3, 31)

loaded = False

@app.before_request
def load_precomputed():
    global loaded
    if loaded:
        return

    db.create_all()
    today = date.today()
    delta = (today - BASE_DATE).days % len(DAILY_WORDS)
    secret_word = DAILY_WORDS[delta]

    existing_game = ArchivedGame.query.filter_by(game_date=today).first()
    if not existing_game:
        archive_path = os.path.join("archive", f"{secret_word}.json")
        if os.path.exists(archive_path):
            with open(archive_path, "r", encoding="utf-8") as f:
                ranking_data = json.load(f)
            new_game = ArchivedGame(
                game_date=today,
                secret_word=secret_word,
                ranking_json=json.dumps(ranking_data, ensure_ascii=False)
            )
            db.session.add(new_game)
            db.session.commit()
    loaded = True

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/ranked")
def get_ranked():
    today = date.today()
    game = ArchivedGame.query.filter_by(game_date=today).first()
    if game:
        return jsonify(json.loads(game.ranking_json))
    return jsonify({"error": "Ranked data not found."}), 404

@app.route("/api/wordlist")
def wordlist_api():
    return jsonify(sorted(list(VALID_WORDS)))

@app.route("/api/daily-index")
def daily_index():
    delta_days = (date.today() - BASE_DATE).days
    game_number = (delta_days % len(DAILY_WORDS)) + 1
    return jsonify({"day_number": game_number})

@app.route("/guess", methods=["POST"])
def guess():
    data = request.get_json()
    user_word = data.get("word", "").strip().lower()

    if user_word not in VALID_WORDS:
        return jsonify({"error": "Вибачте, я не знаю цього слова"}), 400

    today = date.today()
    game = ArchivedGame.query.filter_by(game_date=today).first()
    if not game:
        return jsonify({"error": "Ranked data not found."}), 404

    ranked_words = json.loads(game.ranking_json)
    for item in ranked_words:
        if item["word"].strip().lower() == user_word:
            return jsonify({"rank": item["rank"], "similarity": item["similarity"]})

    return jsonify({"error": "Слово не знайдено"}), 404

@app.route("/archive")
def archive_list():
    games = ArchivedGame.query.order_by(ArchivedGame.game_date.desc()).all()
    return jsonify([g.game_date.isoformat() for g in games])

@app.route("/archive/<string:game_date>")
def archive_by_date(game_date):
    try:
        target_date = datetime.strptime(game_date, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Невірний формат дати."}), 400

    game = ArchivedGame.query.filter_by(game_date=target_date).first()
    if not game:
        return jsonify({"error": "Гру не знайдено."}), 404

    return jsonify({
        "game_date": game.game_date.isoformat(),
        "secret_word": game.secret_word,
        "ranking": json.loads(game.ranking_json),
        "created_at": game.created_at.isoformat()
    })

if __name__ == "__main__":
    app.run(debug=True)