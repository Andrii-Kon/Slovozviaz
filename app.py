# app.py
from flask import Flask, render_template, jsonify, request, Response, abort
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date
from dotenv import load_dotenv
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import load_only
from sqlalchemy import inspect
from sqlalchemy.exc import OperationalError, InterfaceError
from collections import OrderedDict
from functools import lru_cache
import os
import json
import glob
import re
import time
import hashlib
import hmac
from typing import List, Dict, Any, Optional, Tuple

import numpy as np

# ── ENV / конфіг ───────────────────────────────────────────────────────────────
load_dotenv()

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False  # коректна UTF-8 відповідь


def _env_flag(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        return max(minimum, int(raw_value))
    except ValueError:
        return default

basedir = os.path.abspath(os.path.dirname(__file__))
instance_path = os.path.join(basedir, "instance")
os.makedirs(instance_path, exist_ok=True)

# DB: на проді беремо з DATABASE_URL (MySQL), локально — SQLite
db_uri = os.getenv("DATABASE_URL")
if not db_uri:
    db_uri = "sqlite:///" + os.path.join(instance_path, "games.db")

app.config["SQLALCHEMY_DATABASE_URI"] = db_uri
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
if db_uri.startswith("mysql"):
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,
        "pool_recycle": int(os.getenv("DB_POOL_RECYCLE_SECONDS", "280"))
    }

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
        with open("data/daily_words.txt", "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return []

def load_wordlist():
    try:
        with open("data/wordlist.txt", "r", encoding="utf-8") as f:
            return {line.strip().lower() for line in f if line.strip()}
    except FileNotFoundError:
        return set()

DAILY_WORDS = load_daily_words()
VALID_WORDS = load_wordlist()
VALID_WORDS_SORTED = sorted(VALID_WORDS)

BASE_DATE = date(2025, 6, 2)
LIVE_VECTORS_PATH = os.getenv(
    "LIVE_VECTORS_PATH",
    os.path.join("data", "word_vectors_ubercorpus_wordlist_fp16.npz"),
)
CUSTOM_RANKING_CACHE_SIZE = max(1, int(os.getenv("CUSTOM_RANKING_CACHE_SIZE", "2")))
CUSTOM_GAME_TOKEN_SECRET = (
    os.getenv("CUSTOM_GAME_TOKEN_SECRET")
    or os.getenv("FLASK_SECRET_KEY")
    or os.getenv("SECRET_KEY")
    or "slovozviaz-custom-game-v1"
).encode("utf-8")
DEV_MODE_ENABLED = _env_flag("DEV_MODE_ENABLED")
DEV_MODE_PASSWORD = (os.getenv("DEV_MODE_PASSWORD") or "").strip()
DEV_MODE_PATH = (os.getenv("DEV_MODE_PATH") or "").strip()
DEV_MODE_COOKIE_NAME = os.getenv("DEV_MODE_COOKIE_NAME", "slovozviaz_dev_mode")
DEV_MODE_COOKIE_MAX_AGE_SECONDS = _env_int(
    "DEV_MODE_COOKIE_MAX_AGE_SECONDS",
    60 * 60 * 24 * 30,
    minimum=300,
)

if DEV_MODE_PATH and not DEV_MODE_PATH.startswith("/"):
    DEV_MODE_PATH = f"/{DEV_MODE_PATH}"
if len(DEV_MODE_PATH) > 1:
    DEV_MODE_PATH = DEV_MODE_PATH.rstrip("/")
if DEV_MODE_PATH == "/":
    DEV_MODE_PATH = ""

if DEV_MODE_ENABLED and (not DEV_MODE_PASSWORD or not DEV_MODE_PATH):
    print("[DEV MODE] Потрібні DEV_MODE_PASSWORD і DEV_MODE_PATH. Без них dev mode буде вимкнено.")

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
            return

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

# ── Простий in-memory кеш для ранкінгу ─────────────────────────────────────────
RANKING_CACHE: dict[date, list] = {}
ARCHIVE_DATES_CACHE: Optional[List[str]] = None
ARCHIVE_DATES_CACHE_EXPIRES_AT = 0.0
ARCHIVE_DATES_CACHE_TTL_SECONDS = max(30, int(os.getenv("ARCHIVE_DATES_CACHE_TTL_SECONDS", "300")))
CUSTOM_RANKING_CACHE: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
LIVE_WORDS: Optional[List[str]] = None
LIVE_WORD_TO_INDEX: Optional[Dict[str, int]] = None
LIVE_MATRIX: Optional[np.ndarray] = None
LIVE_NORMS: Optional[np.ndarray] = None
CUSTOM_GAME_ID_TO_WORD: Optional[Dict[str, str]] = None
UK_MORPH_ANALYZER: Any | None = None
UK_MORPH_ANALYZER_INIT_ATTEMPTED = False

def _reset_db_connection():
    try:
        db.session.rollback()
    except Exception:
        pass
    db.session.remove()
    try:
        db.engine.dispose()
    except Exception:
        pass

def _run_db_query_with_retry(loader):
    try:
        return loader()
    except (OperationalError, InterfaceError):
        _reset_db_connection()
        return loader()

def _load_ranking_from_db(target_date: date) -> list | None:
    row = ArchivedGame.query.filter_by(game_date=target_date).first()
    if not row:
        return None
    data = json.loads(row.ranking_json)
    if not isinstance(data, list):
        raise ValueError("Ranking data is not a list")
    return data

def _load_archive_dates_from_db() -> List[str]:
    games = (
        ArchivedGame.query
        .options(load_only(ArchivedGame.game_date))
        .order_by(ArchivedGame.game_date.desc())
        .all()
    )
    return [g.game_date.isoformat() for g in games]

def _get_archive_dates_cached() -> List[str]:
    global ARCHIVE_DATES_CACHE, ARCHIVE_DATES_CACHE_EXPIRES_AT
    now = time.time()
    if ARCHIVE_DATES_CACHE is not None and now < ARCHIVE_DATES_CACHE_EXPIRES_AT:
        return ARCHIVE_DATES_CACHE

    dates = _run_db_query_with_retry(_load_archive_dates_from_db)
    ARCHIVE_DATES_CACHE = dates
    ARCHIVE_DATES_CACHE_EXPIRES_AT = now + ARCHIVE_DATES_CACHE_TTL_SECONDS
    return dates


def _invalidate_archive_caches(target_date: Optional[date] = None) -> None:
    global ARCHIVE_DATES_CACHE, ARCHIVE_DATES_CACHE_EXPIRES_AT

    if target_date is None:
        RANKING_CACHE.clear()
    else:
        RANKING_CACHE.pop(target_date, None)

    ARCHIVE_DATES_CACHE = None
    ARCHIVE_DATES_CACHE_EXPIRES_AT = 0.0


def _normalize_word(raw_word: Optional[str]) -> str:
    return (raw_word or "").strip().lower()


def _normalize_game_id(raw_game_id: Optional[str]) -> str:
    return (raw_game_id or "").strip().lower()


def _get_uk_morph_analyzer() -> Any | None:
    global UK_MORPH_ANALYZER, UK_MORPH_ANALYZER_INIT_ATTEMPTED

    if UK_MORPH_ANALYZER_INIT_ATTEMPTED:
        return UK_MORPH_ANALYZER

    UK_MORPH_ANALYZER_INIT_ATTEMPTED = True
    try:
        import pymorphy3  # type: ignore
        UK_MORPH_ANALYZER = pymorphy3.MorphAnalyzer(lang="uk")
        print("[MORPH] Ukrainian pymorphy3 analyzer enabled.")
    except Exception as e:
        UK_MORPH_ANALYZER = None
        print(f"[MORPH] pymorphy3 unavailable; normalization disabled: {e!r}")

    return UK_MORPH_ANALYZER


@lru_cache(maxsize=8192)
def _resolve_word_to_valid_lemma(raw_word: str) -> Optional[str]:
    word = _normalize_word(raw_word)
    if not word:
        return None

    if word in VALID_WORDS:
        return word

    morph = _get_uk_morph_analyzer()
    if morph is None:
        return None

    seen_candidates: set[str] = set()

    for parse in morph.parse(word):
        if not getattr(parse, "is_known", False):
            continue

        if getattr(parse.tag, "POS", None) != "NOUN":
            continue

        candidate = _normalize_word(getattr(parse, "normal_form", ""))
        if not candidate or candidate in seen_candidates:
            continue

        seen_candidates.add(candidate)
        if candidate in VALID_WORDS:
            return candidate

    return None


def _custom_game_id_for_word(word: str) -> str:
    return hmac.new(
        CUSTOM_GAME_TOKEN_SECRET,
        word.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _get_custom_game_id_map() -> Dict[str, str]:
    global CUSTOM_GAME_ID_TO_WORD

    if CUSTOM_GAME_ID_TO_WORD is not None:
        return CUSTOM_GAME_ID_TO_WORD

    game_id_to_word: Dict[str, str] = {}
    for word in VALID_WORDS_SORTED:
        game_id = _custom_game_id_for_word(word)
        existing = game_id_to_word.get(game_id)
        if existing is not None and existing != word:
            raise RuntimeError("Виявлено колізію ідентифікатора кастомної гри.")
        game_id_to_word[game_id] = word

    CUSTOM_GAME_ID_TO_WORD = game_id_to_word
    return CUSTOM_GAME_ID_TO_WORD


def _resolve_live_vectors_path() -> str:
    return os.path.normpath(LIVE_VECTORS_PATH.replace("\\", os.sep))


def _load_live_vectors_if_needed() -> Tuple[List[str], Dict[str, int], np.ndarray, np.ndarray]:
    global LIVE_WORDS, LIVE_WORD_TO_INDEX, LIVE_MATRIX, LIVE_NORMS

    if (
        LIVE_WORDS is not None
        and LIVE_WORD_TO_INDEX is not None
        and LIVE_MATRIX is not None
        and LIVE_NORMS is not None
    ):
        return LIVE_WORDS, LIVE_WORD_TO_INDEX, LIVE_MATRIX, LIVE_NORMS

    vectors_path = _resolve_live_vectors_path()
    if not os.path.isfile(vectors_path):
        raise FileNotFoundError(
            "Файл live-векторів не знайдено. "
            f"Очікував: '{vectors_path}'."
        )

    with np.load(vectors_path, allow_pickle=False) as payload:
        if "words" not in payload or "vectors" not in payload:
            raise ValueError("Файл live-векторів має містити масиви 'words' і 'vectors'.")

        words_arr = payload["words"]
        vectors_arr = payload["vectors"]
        norms_arr = payload["norms"] if "norms" in payload else None

    if vectors_arr.ndim != 2:
        raise ValueError("Масив 'vectors' має бути двовимірним.")

    words = [str(w) for w in words_arr.tolist()]
    matrix = vectors_arr.astype(np.float32, copy=False)

    if matrix.shape[0] != len(words):
        raise ValueError("Кількість слів у 'words' не збігається з кількістю рядків 'vectors'.")

    if norms_arr is None:
        norms = np.linalg.norm(matrix, axis=1).astype(np.float32)
    else:
        norms = norms_arr.astype(np.float32, copy=False)
        if norms.ndim != 1 or norms.shape[0] != len(words):
            raise ValueError("Масив 'norms' має бути одновимірним і відповідати довжині 'words'.")

    norms = np.where(norms == 0.0, 1e-12, norms)
    word_to_index = {word: idx for idx, word in enumerate(words)}

    LIVE_WORDS = words
    LIVE_WORD_TO_INDEX = word_to_index
    LIVE_MATRIX = matrix
    LIVE_NORMS = norms

    print(
        f"[LIVE] Завантажено live-вектори: {len(words)} слів, "
        f"розмірність={matrix.shape[1]}, файл='{vectors_path}'"
    )
    return LIVE_WORDS, LIVE_WORD_TO_INDEX, LIVE_MATRIX, LIVE_NORMS


def _build_live_ranking(target_word: str) -> List[Dict[str, Any]]:
    words, word_to_index, matrix, norms = _load_live_vectors_if_needed()
    target_idx = word_to_index.get(target_word)
    if target_idx is None:
        raise ValueError(f"Слово '{target_word}' відсутнє у live-векторах.")

    target_vector = matrix[target_idx]
    target_norm = float(norms[target_idx])
    if target_norm == 0.0:
        target_norm = 1e-12
    similarities = (matrix @ target_vector) / (norms * target_norm)
    order = np.argsort(similarities)[::-1]

    return [
        {
            "word": words[idx],
            "similarity": float(similarities[idx]),
            "rank": rank,
        }
        for rank, idx in enumerate(order, start=1)
    ]


def _get_live_ranking_cached(target_word: str) -> List[Dict[str, Any]]:
    cached = CUSTOM_RANKING_CACHE.get(target_word)
    if cached is not None:
        CUSTOM_RANKING_CACHE.move_to_end(target_word)
        return cached

    ranking = _build_live_ranking(target_word)
    CUSTOM_RANKING_CACHE[target_word] = ranking
    if len(CUSTOM_RANKING_CACHE) > CUSTOM_RANKING_CACHE_SIZE:
        CUSTOM_RANKING_CACHE.popitem(last=False)
    return ranking


def _is_dev_mode_available() -> bool:
    return DEV_MODE_ENABLED and bool(DEV_MODE_PASSWORD) and bool(DEV_MODE_PATH)


def _require_dev_mode_enabled() -> None:
    if not _is_dev_mode_available():
        abort(404)


def _build_dev_auth_cookie_value() -> str:
    if not DEV_MODE_PASSWORD:
        return ""

    return hmac.new(
        DEV_MODE_PASSWORD.encode("utf-8"),
        b"slovozviaz-dev-auth-cookie",
        hashlib.sha256,
    ).hexdigest()


def _has_dev_access() -> bool:
    if not _is_dev_mode_available():
        return False

    cookie_value = request.cookies.get(DEV_MODE_COOKIE_NAME, "")
    expected_value = _build_dev_auth_cookie_value()
    return bool(cookie_value) and bool(expected_value) and hmac.compare_digest(cookie_value, expected_value)


def _set_dev_auth_cookie(response: Response) -> None:
    response.set_cookie(
        DEV_MODE_COOKIE_NAME,
        _build_dev_auth_cookie_value(),
        max_age=DEV_MODE_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="Lax",
        secure=bool(request.is_secure),
        path="/",
    )


def _clear_dev_auth_cookie(response: Response) -> None:
    response.delete_cookie(DEV_MODE_COOKIE_NAME, path="/")


def _dev_auth_required_response():
    return jsonify({"error": "Потрібен доступ до dev-режиму."}), 401


def _parse_requested_game_date(raw_value: Any) -> date:
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ValueError("Вкажіть дату гри у форматі YYYY-MM-DD.")

    try:
        return datetime.strptime(raw_value.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Невірний формат дати. Використовуйте YYYY-MM-DD.") from exc


def _resolve_secret_word_for_archive(raw_value: Any) -> Tuple[str, str, bool]:
    normalized_word = _normalize_word(raw_value if isinstance(raw_value, str) else "")
    if not normalized_word:
        raise ValueError("Введіть секретне слово.")

    if normalized_word in VALID_WORDS:
        return normalized_word, normalized_word, False

    resolved_word = _resolve_word_to_valid_lemma(normalized_word)
    if not resolved_word:
        raise ValueError("Цього слова немає у словнику гри.")

    return normalized_word, resolved_word, resolved_word != normalized_word


def _build_dev_public_game_url(game_date: date) -> str:
    return f"{request.url_root.rstrip('/')}/?date={game_date.isoformat()}"


def _build_dev_archive_preview(game_date: date, raw_word: Any) -> Dict[str, Any]:
    requested_word, resolved_word, was_normalized = _resolve_secret_word_for_archive(raw_word)

    existing_row = _run_db_query_with_retry(
        lambda: ArchivedGame.query
        .options(load_only(ArchivedGame.game_date, ArchivedGame.secret_word, ArchivedGame.created_at))
        .filter_by(game_date=game_date)
        .first()
    )

    ranking = _get_live_ranking_cached(resolved_word)
    today_utc = datetime.utcnow().date()
    if game_date < today_utc:
        date_relation = "past"
    elif game_date > today_utc:
        date_relation = "future"
    else:
        date_relation = "today"

    return {
        "game_date": game_date.isoformat(),
        "today_utc": today_utc.isoformat(),
        "date_relation": date_relation,
        "requested_word": requested_word,
        "secret_word": resolved_word,
        "word_was_normalized": was_normalized,
        "existing_game": (
            {
                "game_date": existing_row.game_date.isoformat(),
                "secret_word": existing_row.secret_word,
                "created_at": existing_row.created_at.isoformat() if existing_row.created_at else None,
            }
            if existing_row
            else None
        ),
        "action": "replace" if existing_row else "create",
        "ranking_preview": ranking[:500],
        "total_ranking_words": len(ranking),
        "public_game_url": _build_dev_public_game_url(game_date),
    }


def _upsert_archived_game_for_date(game_date: date, secret_word: str, ranking: List[Dict[str, Any]]) -> Dict[str, Any]:
    row = ArchivedGame.query.filter_by(game_date=game_date).first()
    previous_secret_word = row.secret_word if row else None
    payload = json.dumps(ranking, ensure_ascii=False)

    if row is None:
        row = ArchivedGame(
            game_date=game_date,
            secret_word=secret_word,
            ranking_json=payload,
        )
        db.session.add(row)
        save_action = "created"
    else:
        row.secret_word = secret_word
        row.ranking_json = payload
        save_action = "replaced"

    db.session.commit()
    _invalidate_archive_caches(game_date)

    return {
        "save_action": save_action,
        "previous_secret_word": previous_secret_word,
    }

# ── Маршрути ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/create-game")
def create_game_page():
    return render_template("create_game.html")


def dev_archive_game_page():
    _require_dev_mode_enabled()
    response = Response(render_template(
        "dev_archive_game.html",
        dev_authenticated=_has_dev_access(),
        today_utc=datetime.utcnow().date().isoformat(),
        dev_login_path=f"{DEV_MODE_PATH}/login",
        dev_logout_path=f"{DEV_MODE_PATH}/logout",
        dev_preview_path=f"{DEV_MODE_PATH}/preview",
        dev_save_path=f"{DEV_MODE_PATH}/save",
    ))
    response.headers["Cache-Control"] = "private, no-store"
    return response

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
        # Використовуємо UTC-дату, щоб менше впливала таймзона хостингу
        target = datetime.utcnow().date()

    # 1) Кеш у пам'яті
    cached = RANKING_CACHE.get(target)
    if cached is not None:
        response = jsonify(cached)
        response.headers["Cache-Control"] = "public, max-age=300"
        return response

    # 2) Перша спроба читання
    try:
        data = _run_db_query_with_retry(lambda: _load_ranking_from_db(target))
        if data is None:
            return jsonify({"error": f"Рейтинг для {target.isoformat()} не знайдено."}), 404
        RANKING_CACHE[target] = data
        response = jsonify(data)
        response.headers["Cache-Control"] = "public, max-age=300"
        return response
    except (OperationalError, InterfaceError):
        return jsonify({"error": "Тимчасова помилка підключення до бази. Спробуйте ще раз."}), 503

    except Exception:
        return jsonify({"error": "Помилка даних на сервері."}), 500

@app.route("/api/wordlist")
def wordlist_api():
    response = jsonify(VALID_WORDS_SORTED)
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@app.route("/api/normalize-word")
def normalize_word_api():
    word = _normalize_word(request.args.get("word"))
    if not word:
        return jsonify({"error": "Передайте слово в query-параметрі 'word'."}), 400

    resolved_word = _resolve_word_to_valid_lemma(word)
    response = jsonify({
        "original_word": word,
        "resolved_word": resolved_word,
        "was_changed": bool(resolved_word and resolved_word != word),
    })
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.route("/api/ranked-by-word")
def ranked_by_word():
    target_word = _normalize_word(request.args.get("word"))
    if not target_word:
        return jsonify({"error": "Передайте слово в query-параметрі 'word'."}), 400

    if target_word not in VALID_WORDS:
        return jsonify({"error": "Цього слова немає у словнику гри."}), 400

    try:
        ranking = _get_live_ranking_cached(target_word)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 503
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"[LIVE] Помилка генерації рейтингу для '{target_word}': {e}")
        return jsonify({"error": "Не вдалося згенерувати live-рейтинг."}), 500

    game_id = _custom_game_id_for_word(target_word)
    response = jsonify({
        "mode": "custom",
        "game_id": game_id,
        "ranking": ranking,
    })
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.route("/api/ranked-by-game")
def ranked_by_game():
    game_id = _normalize_game_id(request.args.get("game"))
    if not game_id:
        return jsonify({"error": "Передайте id гри в query-параметрі 'game'."}), 400

    if not re.fullmatch(r"[0-9a-f]{64}", game_id):
        return jsonify({"error": "Невірний формат id гри."}), 400

    try:
        target_word = _get_custom_game_id_map().get(game_id)
    except RuntimeError:
        return jsonify({"error": "Помилка побудови id кастомних ігор."}), 500

    if not target_word:
        return jsonify({"error": "Гру за цим посиланням не знайдено."}), 404

    try:
        ranking = _get_live_ranking_cached(target_word)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 503
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"[LIVE] Помилка генерації рейтингу для game_id '{game_id}': {e}")
        return jsonify({"error": "Не вдалося згенерувати live-рейтинг."}), 500

    response = jsonify({
        "mode": "custom",
        "game_id": game_id,
        "ranking": ranking,
    })
    response.headers["Cache-Control"] = "private, no-store"
    return response


def dev_login():
    _require_dev_mode_enabled()

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Очікував JSON-об'єкт."}), 400

    password = payload.get("password")
    if not isinstance(password, str) or not hmac.compare_digest(password, DEV_MODE_PASSWORD):
        return jsonify({"error": "Невірний dev-пароль."}), 401

    response = jsonify({"ok": True})
    _set_dev_auth_cookie(response)
    response.headers["Cache-Control"] = "private, no-store"
    return response


def dev_logout():
    _require_dev_mode_enabled()

    response = jsonify({"ok": True})
    _clear_dev_auth_cookie(response)
    response.headers["Cache-Control"] = "private, no-store"
    return response


def dev_archive_game_preview():
    _require_dev_mode_enabled()
    if not _has_dev_access():
        return _dev_auth_required_response()

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Очікував JSON-об'єкт."}), 400

    try:
        game_date = _parse_requested_game_date(payload.get("game_date"))
        preview = _build_dev_archive_preview(game_date, payload.get("word"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except (OperationalError, InterfaceError):
        return jsonify({"error": "Тимчасова помилка підключення до бази. Спробуйте ще раз."}), 503
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        print(f"[DEV] Помилка preview для {payload!r}: {e}")
        return jsonify({"error": "Не вдалося підготувати preview гри."}), 500

    response = jsonify(preview)
    response.headers["Cache-Control"] = "private, no-store"
    return response


def dev_archive_game_save():
    _require_dev_mode_enabled()
    if not _has_dev_access():
        return _dev_auth_required_response()

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Очікував JSON-об'єкт."}), 400

    try:
        game_date = _parse_requested_game_date(payload.get("game_date"))
        preview = _build_dev_archive_preview(game_date, payload.get("word"))
        ranking = _get_live_ranking_cached(preview["secret_word"])
        save_result = _upsert_archived_game_for_date(game_date, preview["secret_word"], ranking)
    except ValueError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400
    except (OperationalError, InterfaceError):
        db.session.rollback()
        return jsonify({"error": "Тимчасова помилка підключення до бази. Спробуйте ще раз."}), 503
    except FileNotFoundError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        db.session.rollback()
        print(f"[DEV] Помилка save для {payload!r}: {e}")
        return jsonify({"error": "Не вдалося зберегти гру в архів."}), 500

    response = jsonify({
        **preview,
        **save_result,
    })
    response.headers["Cache-Control"] = "private, no-store"
    return response

if DEV_MODE_PATH:
    app.add_url_rule(DEV_MODE_PATH, view_func=dev_archive_game_page, methods=["GET"])
    app.add_url_rule(f"{DEV_MODE_PATH}/login", view_func=dev_login, methods=["POST"])
    app.add_url_rule(f"{DEV_MODE_PATH}/logout", view_func=dev_logout, methods=["POST"])
    app.add_url_rule(f"{DEV_MODE_PATH}/preview", view_func=dev_archive_game_preview, methods=["POST"])
    app.add_url_rule(f"{DEV_MODE_PATH}/save", view_func=dev_archive_game_save, methods=["POST"])

@app.route("/api/daily-index")
def daily_index():
    delta = (date.today() - BASE_DATE).days
    response = jsonify({"game_number": delta + 1})
    response.headers["Cache-Control"] = "public, max-age=60"
    return response

@app.route("/archive")
def archive_list():
    """
    Повертає ТІЛЬКИ список дат.
    ВАЖЛИВО: не вантажимо LONGTEXT ranking_json із MySQL.
    """
    try:
        dates = _get_archive_dates_cached()
        response = jsonify(dates)
        response.headers["Cache-Control"] = "public, max-age=120"
        return response
    except (OperationalError, InterfaceError):
        if ARCHIVE_DATES_CACHE is not None:
            response = jsonify(ARCHIVE_DATES_CACHE)
            response.headers["Cache-Control"] = "public, max-age=30"
            return response
        return jsonify({"error": "Тимчасова помилка підключення до бази. Спробуйте ще раз."}), 503
    except Exception:
        return jsonify({"error": "Помилка завантаження архіву."}), 500

@app.route("/archive/<string:game_date_str>")
def archive_by_date(game_date_str):
    try:
        d = datetime.strptime(game_date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Невірний формат дати. Використовуйте YYYY-MM-DD."}), 400

    cached = RANKING_CACHE.get(d)
    if cached is not None:
        response = jsonify({
            "game_date": d.isoformat(),
            "ranking": cached
        })
        response.headers["Cache-Control"] = "public, max-age=300"
        return response

    try:
        row = _run_db_query_with_retry(
            lambda: ArchivedGame.query
            .options(load_only(ArchivedGame.game_date, ArchivedGame.ranking_json))
            .filter_by(game_date=d)
            .first()
        )
        if not row:
            return jsonify({"error": f"Гру для {game_date_str} не знайдено."}), 404
    except (OperationalError, InterfaceError):
        return jsonify({"error": "Тимчасова помилка підключення до бази. Спробуйте ще раз."}), 503

    try:
        ranking = json.loads(row.ranking_json)
        if not isinstance(ranking, list):
            raise ValueError("Ranking data is not a list")
        response = jsonify({
            "game_date": row.game_date.isoformat(),
            "ranking": ranking
        })
        response.headers["Cache-Control"] = "public, max-age=300"
        return response
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

# ──  CLI для ручної ініціалізації ──────────────────────────────────────
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
    pass

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        import_json_into_sqlite_if_needed()
    app.run(debug=True)
