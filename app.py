# Імпортуємо необхідні модулі
from flask import Flask, render_template, jsonify, request, Response # Додано Response
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date, timedelta
import os
import json

# Ініціалізація Flask додатка
app = Flask(__name__)
basedir = os.path.abspath(os.path.dirname(__file__))

# Конфігурація бази даних
instance_path = os.path.join(basedir, "instance")
os.makedirs(instance_path, exist_ok=True)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(instance_path, "games.db")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Ініціалізація SQLAlchemy
db = SQLAlchemy(app)

# Модель для зберігання архівних ігор
class ArchivedGame(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    game_date = db.Column(db.Date, unique=True, nullable=False)
    secret_word = db.Column(db.String(100), nullable=False)
    ranking_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# --- Функції завантаження даних ---
def load_daily_words():
    """Завантажує список секретних слів для щоденних ігор."""
    try:
        with open("daily_words.txt", "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print("Помилка: Файл daily_words.txt не знайдено.")
        return [] # Повертаємо порожній список у разі помилки

def load_wordlist():
    """Завантажує повний словник дозволених слів."""
    try:
        with open("wordlist.txt", "r", encoding="utf-8") as f:
            return {line.strip().lower() for line in f if line.strip()}
    except FileNotFoundError:
        print("Помилка: Файл wordlist.txt не знайдено.")
        return set() # Повертаємо порожню множину

# Завантажуємо дані при старті
DAILY_WORDS = load_daily_words()
VALID_WORDS = load_wordlist()
BASE_DATE = date(2025, 5, 11) # Базова дата для розрахунку номерів ігор (Перевірте, чи це правильна дата)

# --- Обробник перед першим запитом ---
@app.before_request
def ensure_games_archived():
    """Перевіряє та архівує ігри від базової дати до сьогодні, якщо їх немає в БД."""
    # Цей код тепер виконується перед кожним запитом, що може бути не оптимально.
    # Розгляньте варіант виконання цього один раз при старті або за розкладом.
    # Проте, для Render це може бути робочим варіантом.
    try:
        db.create_all() # Створює таблиці, якщо їх немає
        today = date.today()
        current_date = BASE_DATE
        games_added = False # Прапорець, щоб уникнути зайвих комітів

        while current_date <= today:
            exists = db.session.query(ArchivedGame.query.filter_by(game_date=current_date).exists()).scalar()
            if not exists:
                delta_days = (current_date - BASE_DATE).days
                if DAILY_WORDS: # Перевіряємо, чи список слів не порожній
                    word_index = delta_days % len(DAILY_WORDS)
                    secret_word = DAILY_WORDS[word_index]
                else:
                    print(f"Помилка: Неможливо визначити секретне слово для {current_date}, список DAILY_WORDS порожній.")
                    current_date += timedelta(days=1)
                    continue # Переходимо до наступної дати

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
            db.session.commit() # Коммітимо зміни, якщо щось було додано
    except Exception as e:
        print(f"Загальна помилка в ensure_games_archived: {e}")
        db.session.rollback() # Відкочуємо зміни у разі помилки

# --- Основні маршрути (Endpoints) ---
@app.route("/")
def index():
    """Головна сторінка гри."""
    return render_template("index.html")

@app.route("/ranked")
@app.route("/api/ranked") # Додамо префікс /api для консистентності
def get_ranked():
    """Повертає рейтинг слів для сьогоднішньої гри."""
    target_date_str = request.args.get('date') # Приймаємо дату як параметр
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
            # Забезпечуємо, що повертаємо список (масив JSON)
            ranking_data = json.loads(game.ranking_json)
            if isinstance(ranking_data, list):
                return jsonify(ranking_data)
            else:
                print(f"Помилка: рейтинг для {target_date} не є списком.")
                return jsonify({"error": "Невірний формат даних рейтингу."}), 500
        except json.JSONDecodeError:
            print(f"Помилка: Не вдалося розпарсити JSON рейтингу для {target_date}.")
            return jsonify({"error": "Помилка даних на сервері."}), 500
    else:
        print(f"Помилка: Дані рейтингу для {target_date} не знайдено в базі.")
        return jsonify({"error": f"Рейтинг для {target_date.isoformat()} не знайдено."}), 404


@app.route("/api/wordlist")
def wordlist_api():
    """Повертає відсортований список дозволених слів."""
    # Перетворюємо множину на список і сортуємо
    return jsonify(sorted(list(VALID_WORDS)))

@app.route("/api/daily-index")
def daily_index():
    """Повертає номер сьогоднішньої гри."""
    if not DAILY_WORDS: # Перевірка, чи список слів завантажено
        return jsonify({"error": "Список щоденних слів не завантажено."}), 500
    delta_days = (date.today() - BASE_DATE).days
    # Розраховуємо номер гри (нумерація з 1)
    game_number = delta_days + 1
    return jsonify({"game_number": game_number})

# Видалено маршрут /guess, оскільки логіка обробки тепер на клієнті

@app.route("/archive")
def archive_list():
    """Повертає список дат доступних архівних ігор."""
    games = ArchivedGame.query.order_by(ArchivedGame.game_date.desc()).all()
    return jsonify([g.game_date.isoformat() for g in games])

@app.route("/archive/<string:game_date>")
def archive_by_date(game_date):
    """Повертає дані конкретної архівної гри за датою."""
    try:
        target_date = datetime.strptime(game_date, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Невірний формат дати. Використовуйте YYYY-MM-DD."}), 400

    game = ArchivedGame.query.filter_by(game_date=target_date).first()
    if not game:
        return jsonify({"error": f"Гру для {game_date} не знайдено."}), 404

    try:
        ranking_data = json.loads(game.ranking_json)
        if not isinstance(ranking_data, list):
            raise ValueError("Ranking data is not a list")
        return jsonify({
            "game_date": game.game_date.isoformat(),
            # "secret_word": game.secret_word, # Можливо, не варто віддавати секретне слово заздалегідь?
            "ranking": ranking_data,
            "created_at": game.created_at.isoformat() if game.created_at else None
        })
    except (json.JSONDecodeError, ValueError) as e:
        print(f"Помилка даних для гри {game_date}: {e}")
        return jsonify({"error": "Помилка даних для цієї гри."}), 500


# === ДОДАНО МАРШРУТИ ДЛЯ SEO ===
@app.route('/robots.txt')
def robots_txt():
    """Генерує вміст файлу robots.txt."""
    # Переконайтесь, що URL у Sitemap вказано правильно
    sitemap_url = request.url_root.rstrip('/') + '/sitemap.xml'
    content = f"""User-agent: *
Allow: /

Sitemap: {sitemap_url}"""
    return Response(content, mimetype='text/plain')

@app.route('/sitemap.xml')
def sitemap_xml():
    """Генерує вміст файлу sitemap.xml."""
    # Отримуємо базовий URL з запиту
    base_url = request.url_root.rstrip('/')
    # Формуємо поточну дату у форматі YYYY-MM-DD
    last_mod_date = date.today().isoformat()

    # Вміст sitemap.xml для головної сторінки
    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{base_url}/</loc>
    <lastmod>{last_mod_date}</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>"""
    return Response(content, mimetype='application/xml')
# === КІНЕЦЬ МАРШРУТІВ ДЛЯ SEO ===


# Запуск додатка (для локальної розробки)
if __name__ == "__main__":
    # Переконуємось, що директорія instance існує перед створенням таблиць
    if not os.path.exists(instance_path):
        os.makedirs(instance_path)
    with app.app_context():
        ensure_games_archived() # Запускаємо перевірку архіву при старті
    app.run(debug=True) # debug=True тільки для розробки!