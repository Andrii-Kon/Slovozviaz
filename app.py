# app.py
from flask import Flask, render_template, jsonify, request, Response, abort, redirect, session, url_for, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date, timedelta
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
import secrets
import urllib.error
import urllib.parse
import urllib.request
from typing import List, Dict, Any, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np

# ── ENV / конфіг ───────────────────────────────────────────────────────────────
load_dotenv()

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False  # коректна UTF-8 відповідь
app.config["SECRET_KEY"] = (
    os.getenv("FLASK_SECRET_KEY")
    or os.getenv("SECRET_KEY")
    or "slovozviaz-dev-secret"
)

KYIV_TZ = ZoneInfo("Europe/Kyiv")


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


class TwitchChatEvent(db.Model):
    __tablename__ = "twitch_chat_event"

    id = db.Column(db.Integer, primary_key=True)
    channel = db.Column(db.String(100), nullable=False, index=True)
    game_scope = db.Column(db.String(160), nullable=False, default="", index=True)
    source_message_id = db.Column(db.String(120), nullable=True, index=True)
    chatter_user_login = db.Column(db.String(100), nullable=False)
    chatter_display_name = db.Column(db.String(100), nullable=False)
    raw_message = db.Column(db.String(500), nullable=False)
    guessed_word = db.Column(db.String(100), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)


class TwitchChatActiveTarget(db.Model):
    __tablename__ = "twitch_chat_active_target"

    id = db.Column(db.Integer, primary_key=True)
    channel = db.Column(db.String(100), nullable=False, unique=True, index=True)
    game_scope = db.Column(db.String(160), nullable=False, default="", index=True)
    page_url = db.Column(db.String(500), nullable=False, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
        index=True,
    )


class TwitchConnection(db.Model):
    __tablename__ = "twitch_connection"

    id = db.Column(db.Integer, primary_key=True)
    twitch_user_id = db.Column(db.String(100), nullable=False, unique=True, index=True)
    twitch_login = db.Column(db.String(100), nullable=False, unique=True, index=True)
    twitch_display_name = db.Column(db.String(120), nullable=False)
    access_token = db.Column(db.Text().with_variant(LONGTEXT, "mysql"), nullable=False)
    refresh_token = db.Column(db.Text().with_variant(LONGTEXT, "mysql"), nullable=False)
    token_type = db.Column(db.String(50), nullable=False, default="bearer")
    token_scopes = db.Column(db.String(500), nullable=False, default="")
    token_expires_at = db.Column(db.DateTime, nullable=True, index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    connected_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    disconnected_at = db.Column(db.DateTime, nullable=True)
    last_validated_at = db.Column(db.DateTime, nullable=True)
    last_worker_seen_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
        index=True,
    )

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
TWITCH_CHAT_BRIDGE_SECRET = (os.getenv("TWITCH_CHAT_BRIDGE_SECRET") or "").strip()
TWITCH_CLIENT_ID = (os.getenv("TWITCH_CLIENT_ID") or "").strip()
TWITCH_CLIENT_SECRET = (os.getenv("TWITCH_CLIENT_SECRET") or "").strip()
TWITCH_OAUTH_REDIRECT_URI = (os.getenv("TWITCH_OAUTH_REDIRECT_URI") or "").strip()
TWITCH_MOCK_ENABLED = _env_flag("TWITCH_MOCK_ENABLED")
TWITCH_MOCK_LOGIN = (os.getenv("TWITCH_MOCK_LOGIN") or "espero_n").strip()
TWITCH_MOCK_DISPLAY_NAME = (os.getenv("TWITCH_MOCK_DISPLAY_NAME") or "").strip()
TWITCH_SHARED_BOT_USERNAME = (os.getenv("TWITCH_BOT_USERNAME") or "").strip().lower()
TWITCH_SHARED_BOT_TOKEN = (os.getenv("TWITCH_OAUTH_TOKEN") or "").strip()
TWITCH_CHAT_EVENT_RETENTION_HOURS = _env_int("TWITCH_CHAT_EVENT_RETENTION_HOURS", 24, minimum=1)
TWITCH_CHAT_EVENT_POLL_LIMIT = _env_int("TWITCH_CHAT_EVENT_POLL_LIMIT", 25, minimum=1)
TWITCH_CHAT_POLL_INTERVAL_MS = _env_int("TWITCH_CHAT_POLL_INTERVAL_MS", 1500, minimum=500)
TWITCH_CHAT_TARGET_TTL_SECONDS = _env_int("TWITCH_CHAT_TARGET_TTL_SECONDS", 120, minimum=30)
TWITCH_CHAT_PRUNE_INTERVAL_SECONDS = _env_int(
    "TWITCH_CHAT_PRUNE_INTERVAL_SECONDS",
    300,
    minimum=30,
)
TWITCH_CHAT_MAX_STORED_EVENTS = _env_int("TWITCH_CHAT_MAX_STORED_EVENTS", 5000, minimum=100)
TWITCH_CHAT_MAX_FETCH_LIMIT = 100
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


def ensure_twitch_chat_event_schema() -> None:
    """Додає службові колонки для Twitch-черги, якщо таблиця вже існувала раніше."""
    with app.app_context():
        insp = inspect(db.engine)
        if "twitch_chat_event" not in insp.get_table_names():
            return

        column_names = {column["name"] for column in insp.get_columns("twitch_chat_event")}
        alter_sqls: List[str] = []

        if "game_scope" not in column_names:
            alter_sqls.append(
                "ALTER TABLE twitch_chat_event "
                "ADD COLUMN game_scope VARCHAR(160) NOT NULL DEFAULT ''"
            )

        if "source_message_id" not in column_names:
            alter_sqls.append(
                "ALTER TABLE twitch_chat_event "
                "ADD COLUMN source_message_id VARCHAR(120) DEFAULT NULL"
            )

        if not alter_sqls:
            return

        with db.engine.begin() as connection:
            for alter_sql in alter_sqls:
                connection.exec_driver_sql(alter_sql)

# ──  Ініціалізація БД при імпорті (працює і для `flask run`)  ──────────────────
with app.app_context():
    # Гарантуємо наявність усіх таблиць, включно з новими службовими.
    db.create_all()
    ensure_twitch_chat_event_schema()
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
LAST_TWITCH_CHAT_PRUNE_AT = 0.0

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


def _now_in_kyiv() -> datetime:
    return datetime.now(KYIV_TZ)


def _today_in_kyiv() -> date:
    return _now_in_kyiv().date()


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


def _is_twitch_chat_bridge_enabled() -> bool:
    return bool(TWITCH_CHAT_BRIDGE_SECRET)


def _has_real_twitch_oauth_config() -> bool:
    return bool(TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET and TWITCH_OAUTH_REDIRECT_URI)


def _should_use_twitch_mock_oauth() -> bool:
    return bool(TWITCH_MOCK_ENABLED and not _has_real_twitch_oauth_config())


def _is_twitch_oauth_enabled() -> bool:
    return bool(_should_use_twitch_mock_oauth() or _has_real_twitch_oauth_config())


def _is_twitch_worker_ready() -> bool:
    return bool(_should_use_twitch_mock_oauth() or (TWITCH_CLIENT_ID and _is_twitch_chat_bridge_enabled()))


def _normalize_twitch_channel(raw_channel: Optional[str]) -> str:
    channel = (raw_channel or "").strip().lower()
    if channel.startswith("#"):
        channel = channel[1:]
    return re.sub(r"[^a-z0-9_]", "", channel)


def _normalize_twitch_game_scope(raw_scope: Optional[str]) -> str:
    scope = (raw_scope or "").strip().lower()
    if not scope:
        return ""

    if len(scope) > 160:
        scope = scope[:160]

    return re.sub(r"[^a-z0-9:_-]", "", scope)


def _normalize_twitch_text(raw_value: Optional[str], fallback: str = "", max_length: int = 100) -> str:
    value = (raw_value or "").strip()
    if not value:
        value = fallback.strip()
    return value[:max_length]


def _normalize_twitch_page_url(raw_value: Optional[str]) -> str:
    return (raw_value or "").strip()[:500]


def _normalize_twitch_scope(raw_value: Any) -> str:
    scope = str(raw_value or "").strip()
    legacy_map = {
        "chat:read": "user:read:chat",
        "chat:write": "user:write:chat",
    }
    return legacy_map.get(scope, scope)


def _split_twitch_scope_values(raw_value: Any) -> List[str]:
    if isinstance(raw_value, list):
        parts = raw_value
    else:
        parts = str(raw_value or "").split()

    ordered_scopes: List[str] = []
    seen: set[str] = set()
    for part in parts:
        scope = _normalize_twitch_scope(part)
        if not scope or scope in seen:
            continue
        seen.add(scope)
        ordered_scopes.append(scope)
    return ordered_scopes


def _build_twitch_scope_text(raw_value: Any, required_scopes: Optional[List[str]] = None) -> str:
    ordered_scopes = _split_twitch_scope_values(raw_value)
    seen = set(ordered_scopes)
    for scope in required_scopes or []:
        normalized_scope = _normalize_twitch_scope(scope)
        if not normalized_scope or normalized_scope in seen:
            continue
        ordered_scopes.append(normalized_scope)
        seen.add(normalized_scope)
    return " ".join(ordered_scopes)


TWITCH_EVENTSUB_REQUIRED_SCOPES = ["user:read:chat"]
TWITCH_OAUTH_SCOPES = _build_twitch_scope_text(
    os.getenv("TWITCH_OAUTH_SCOPES"),
    required_scopes=TWITCH_EVENTSUB_REQUIRED_SCOPES,
)


def _serialize_twitch_connection(row: Optional[TwitchConnection]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None

    scope_values = _split_twitch_scope_values(row.token_scopes)
    missing_scopes = [
        scope
        for scope in TWITCH_EVENTSUB_REQUIRED_SCOPES
        if scope not in scope_values
    ]
    return {
        "id": row.id,
        "twitch_user_id": row.twitch_user_id,
        "twitch_login": row.twitch_login,
        "twitch_display_name": row.twitch_display_name,
        "token_scopes": scope_values,
        "eventsub_ready": not missing_scopes,
        "eventsub_missing_scopes": missing_scopes,
        "connected_at": row.connected_at.isoformat() + "Z" if row.connected_at else None,
        "last_validated_at": row.last_validated_at.isoformat() + "Z" if row.last_validated_at else None,
        "is_active": bool(row.is_active),
    }


def _twitch_connection_session_key() -> str:
    return "twitch_connection_id"


def _twitch_oauth_state_session_key() -> str:
    return "twitch_oauth_state"


def _twitch_oauth_next_session_key() -> str:
    return "twitch_oauth_next"


def _sanitize_local_redirect_target(raw_target: Optional[str]) -> str:
    target = (raw_target or "").strip()
    if not target.startswith("/"):
        return "/"
    if target.startswith("//"):
        return "/"
    return target


def _build_twitch_authorize_url(state: str) -> str:
    params = {
        "response_type": "code",
        "client_id": TWITCH_CLIENT_ID,
        "redirect_uri": TWITCH_OAUTH_REDIRECT_URI,
        "state": state,
    }
    if TWITCH_OAUTH_SCOPES:
        params["scope"] = TWITCH_OAUTH_SCOPES
    return f"https://id.twitch.tv/oauth2/authorize?{urllib.parse.urlencode(params)}"


def _http_json_request(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    encoded_data = None
    request_headers = dict(headers or {})
    if data is not None:
        encoded_data = urllib.parse.urlencode(data).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

    req = urllib.request.Request(url, method=method, headers=request_headers, data=encoded_data)
    with urllib.request.urlopen(req, timeout=15) as response:
        body = response.read().decode("utf-8")
        return json.loads(body or "{}")


def _exchange_twitch_authorization_code(code: str) -> Dict[str, Any]:
    return _http_json_request(
        "https://id.twitch.tv/oauth2/token",
        method="POST",
        data={
            "client_id": TWITCH_CLIENT_ID,
            "client_secret": TWITCH_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": TWITCH_OAUTH_REDIRECT_URI,
        },
    )


def _refresh_twitch_access_token(refresh_token: str) -> Dict[str, Any]:
    return _http_json_request(
        "https://id.twitch.tv/oauth2/token",
        method="POST",
        data={
            "client_id": TWITCH_CLIENT_ID,
            "client_secret": TWITCH_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
    )


def _validate_twitch_access_token(access_token: str) -> Dict[str, Any]:
    return _http_json_request(
        "https://id.twitch.tv/oauth2/validate",
        headers={"Authorization": f"OAuth {access_token}"},
    )


def _fetch_twitch_user_profile(access_token: str) -> Dict[str, Any]:
    payload = _http_json_request(
        "https://api.twitch.tv/helix/users",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Client-Id": TWITCH_CLIENT_ID,
        },
    )
    users = payload.get("data")
    if isinstance(users, list) and users:
        first = users[0]
        if isinstance(first, dict):
            return first
    return {}


def _load_current_twitch_connection() -> Optional[TwitchConnection]:
    connection_id = session.get(_twitch_connection_session_key())
    if not isinstance(connection_id, int):
        return None

    row = TwitchConnection.query.filter_by(id=connection_id, is_active=True).first()
    if row is None:
        session.pop(_twitch_connection_session_key(), None)
    return row


def _load_twitch_connection_by_login(twitch_login: str) -> Optional[TwitchConnection]:
    twitch_login = _normalize_twitch_channel(twitch_login)
    if not twitch_login:
        return None
    return TwitchConnection.query.filter_by(twitch_login=twitch_login, is_active=True).first()


def _load_active_twitch_connections() -> List[TwitchConnection]:
    return TwitchConnection.query.filter_by(is_active=True).order_by(TwitchConnection.twitch_login.asc()).all()


def _twitch_connection_scope_values(row: TwitchConnection) -> List[str]:
    return _split_twitch_scope_values(row.token_scopes)


def _twitch_connection_missing_required_scopes(row: TwitchConnection) -> List[str]:
    scope_values = set(_twitch_connection_scope_values(row))
    return [scope for scope in TWITCH_EVENTSUB_REQUIRED_SCOPES if scope not in scope_values]


def _twitch_connection_eventsub_ready(row: TwitchConnection) -> bool:
    return not _twitch_connection_missing_required_scopes(row)


def _load_worker_active_twitch_channels() -> List[str]:
    cutoff = datetime.utcnow() - timedelta(seconds=TWITCH_CHAT_TARGET_TTL_SECONDS)
    rows = (
        db.session.query(TwitchChatActiveTarget.channel)
        .join(
            TwitchConnection,
            TwitchConnection.twitch_login == TwitchChatActiveTarget.channel,
        )
        .filter(
            TwitchConnection.is_active.is_(True),
            TwitchChatActiveTarget.updated_at >= cutoff,
        )
        .distinct()
        .order_by(TwitchChatActiveTarget.channel.asc())
        .all()
    )
    return [row[0] for row in rows if row and row[0]]


def _load_worker_active_twitch_connections() -> List[Tuple[TwitchConnection, TwitchChatActiveTarget]]:
    cutoff = datetime.utcnow() - timedelta(seconds=TWITCH_CHAT_TARGET_TTL_SECONDS)
    return (
        db.session.query(TwitchConnection, TwitchChatActiveTarget)
        .join(
            TwitchChatActiveTarget,
            TwitchChatActiveTarget.channel == TwitchConnection.twitch_login,
        )
        .filter(
            TwitchConnection.is_active.is_(True),
            TwitchChatActiveTarget.updated_at >= cutoff,
        )
        .order_by(TwitchConnection.twitch_login.asc())
        .all()
    )


def _upsert_twitch_connection(
    validate_payload: Dict[str, Any],
    token_payload: Dict[str, Any],
    profile_payload: Optional[Dict[str, Any]] = None,
) -> TwitchConnection:
    twitch_user_id = str(validate_payload.get("user_id") or "").strip()
    twitch_login = _normalize_twitch_channel(validate_payload.get("login"))
    if not twitch_user_id or not twitch_login:
        raise ValueError("Twitch OAuth не повернув user_id або login.")

    profile_payload = profile_payload or {}
    display_name = _normalize_twitch_text(
        profile_payload.get("display_name") if isinstance(profile_payload, dict) else None,
        fallback=twitch_login,
        max_length=120,
    )
    access_token = _normalize_twitch_text(token_payload.get("access_token"), max_length=2000)
    refresh_token = _normalize_twitch_text(token_payload.get("refresh_token"), max_length=2000)
    token_type = _normalize_twitch_text(token_payload.get("token_type"), fallback="bearer", max_length=50).lower()

    scopes = token_payload.get("scope")
    if not isinstance(scopes, list):
        scopes = validate_payload.get("scopes")
    scope_text = _build_twitch_scope_text(
        scopes,
        required_scopes=TWITCH_EVENTSUB_REQUIRED_SCOPES,
    )

    expires_in = token_payload.get("expires_in")
    expires_at = None
    if isinstance(expires_in, int) and expires_in > 0:
        expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

    row = TwitchConnection.query.filter_by(twitch_user_id=twitch_user_id).first()
    if row is None:
        row = TwitchConnection(
            twitch_user_id=twitch_user_id,
            twitch_login=twitch_login,
            twitch_display_name=display_name,
            access_token=access_token,
            refresh_token=refresh_token,
            token_type=token_type,
            token_scopes=scope_text,
            token_expires_at=expires_at,
            is_active=True,
            connected_at=datetime.utcnow(),
            last_validated_at=datetime.utcnow(),
        )
        db.session.add(row)
    else:
        row.twitch_login = twitch_login
        row.twitch_display_name = display_name
        row.access_token = access_token
        row.refresh_token = refresh_token
        row.token_type = token_type
        row.token_scopes = scope_text
        row.token_expires_at = expires_at
        row.is_active = True
        row.disconnected_at = None
        row.last_validated_at = datetime.utcnow()

    db.session.commit()
    return row


def _upsert_mock_twitch_connection() -> TwitchConnection:
    mock_login = _normalize_twitch_channel(TWITCH_MOCK_LOGIN) or "espero_n"
    mock_display_name = _normalize_twitch_text(
        TWITCH_MOCK_DISPLAY_NAME,
        fallback=mock_login,
        max_length=120,
    )
    mock_user_id = f"mock-{mock_login}"

    row = TwitchConnection.query.filter_by(twitch_user_id=mock_user_id).first()
    if row is None:
        row = TwitchConnection(
            twitch_user_id=mock_user_id,
            twitch_login=mock_login,
            twitch_display_name=mock_display_name,
            access_token="mock-access-token",
            refresh_token="mock-refresh-token",
            token_type="bearer",
            token_scopes=_build_twitch_scope_text(
                "user:read:chat",
                required_scopes=TWITCH_EVENTSUB_REQUIRED_SCOPES,
            ),
            token_expires_at=datetime.utcnow() + timedelta(days=3650),
            is_active=True,
            connected_at=datetime.utcnow(),
            last_validated_at=datetime.utcnow(),
        )
        db.session.add(row)
    else:
        row.twitch_login = mock_login
        row.twitch_display_name = mock_display_name
        row.access_token = "mock-access-token"
        row.refresh_token = "mock-refresh-token"
        row.token_type = "bearer"
        row.token_scopes = _build_twitch_scope_text(
            "user:read:chat",
            required_scopes=TWITCH_EVENTSUB_REQUIRED_SCOPES,
        )
        row.token_expires_at = datetime.utcnow() + timedelta(days=3650)
        row.is_active = True
        row.disconnected_at = None
        row.last_validated_at = datetime.utcnow()

    db.session.commit()
    return row


def _refresh_twitch_connection_if_needed(row: TwitchConnection, force: bool = False) -> TwitchConnection:
    refresh_before = datetime.utcnow() + timedelta(minutes=10)
    if (
        not force
        and row.token_expires_at is not None
        and row.token_expires_at > refresh_before
    ):
        return row

    token_payload = _refresh_twitch_access_token(row.refresh_token)
    access_token = _normalize_twitch_text(token_payload.get("access_token"), max_length=2000)
    validate_payload = _validate_twitch_access_token(access_token)
    profile_payload = _fetch_twitch_user_profile(access_token)
    return _upsert_twitch_connection(validate_payload, token_payload, profile_payload)


def _resolve_twitch_guess_word(raw_word: Any) -> Optional[str]:
    normalized_word = _normalize_word(raw_word if isinstance(raw_word, str) else "")
    if not normalized_word:
        return None

    if normalized_word in VALID_WORDS:
        return normalized_word

    return _resolve_word_to_valid_lemma(normalized_word)


def _serialize_twitch_chat_event(row: TwitchChatEvent) -> Dict[str, Any]:
    return {
        "id": row.id,
        "channel": row.channel,
        "game_scope": row.game_scope,
        "message_id": row.source_message_id,
        "user_login": row.chatter_user_login,
        "user_name": row.chatter_display_name,
        "message": row.raw_message,
        "word": row.guessed_word,
        "created_at": row.created_at.isoformat() + "Z",
    }


def _load_twitch_chat_latest_event_id(channel: str = "", game_scope: str = "") -> int:
    query = db.session.query(TwitchChatEvent.id)
    if channel:
        query = query.filter(TwitchChatEvent.channel == channel)
    if game_scope:
        query = query.filter(TwitchChatEvent.game_scope == game_scope)

    row = query.order_by(TwitchChatEvent.id.desc()).first()
    return int(row[0]) if row else 0


def _load_twitch_chat_event_by_source_message(
    channel: str,
    game_scope: str,
    source_message_id: str,
) -> Optional[TwitchChatEvent]:
    if not source_message_id:
        return None

    query = TwitchChatEvent.query.filter(
        TwitchChatEvent.channel == channel,
        TwitchChatEvent.source_message_id == source_message_id,
    )
    if game_scope:
        query = query.filter(TwitchChatEvent.game_scope == game_scope)
    return query.order_by(TwitchChatEvent.id.desc()).first()


def _load_twitch_chat_active_target(channel: str) -> Optional[TwitchChatActiveTarget]:
    if not channel:
        return None

    row = TwitchChatActiveTarget.query.filter(
        TwitchChatActiveTarget.channel == channel,
        TwitchChatActiveTarget.updated_at >= datetime.utcnow() - timedelta(seconds=TWITCH_CHAT_TARGET_TTL_SECONDS),
    ).first()
    return row


def _upsert_twitch_chat_active_target(channel: str, game_scope: str, page_url: str) -> TwitchChatActiveTarget:
    row = TwitchChatActiveTarget.query.filter_by(channel=channel).first()
    if row is None:
        row = TwitchChatActiveTarget(
            channel=channel,
            game_scope=game_scope,
            page_url=page_url,
        )
        db.session.add(row)
    else:
        row.game_scope = game_scope
        row.page_url = page_url
        row.updated_at = datetime.utcnow()

    db.session.commit()
    return row


def _resolve_active_twitch_game_scope(channel: str) -> Optional[str]:
    active_target = _load_twitch_chat_active_target(channel)
    if active_target is None:
        return None

    game_scope = _normalize_twitch_game_scope(active_target.game_scope)
    return game_scope or None


def _load_twitch_chat_events(after_id: int, channel: str, game_scope: str, limit: int) -> List[TwitchChatEvent]:
    query = TwitchChatEvent.query.filter(TwitchChatEvent.id > after_id)
    if channel:
        query = query.filter(TwitchChatEvent.channel == channel)
    if game_scope:
        query = query.filter(TwitchChatEvent.game_scope == game_scope)

    return query.order_by(TwitchChatEvent.id.asc()).limit(limit).all()


@lru_cache(maxsize=2048)
def _resolve_secret_word_for_twitch_game_scope(game_scope: str) -> str:
    normalized_scope = _normalize_twitch_game_scope(game_scope)
    if not normalized_scope:
        return ""

    if normalized_scope == "daily:current":
        return _normalize_word(guess_secret_word_for_date(_today_in_kyiv()))

    if normalized_scope.startswith("date:"):
        raw_date = normalized_scope[5:]
        try:
            target_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            return ""

        row = (
            ArchivedGame.query
            .options(load_only(ArchivedGame.secret_word))
            .filter(ArchivedGame.game_date == target_date)
            .first()
        )
        if row and row.secret_word:
            return _normalize_word(row.secret_word)
        return _normalize_word(guess_secret_word_for_date(target_date))

    if normalized_scope.startswith("custom:"):
        game_id = _normalize_game_id(normalized_scope[7:])
        if not game_id:
            return ""
        try:
            return _get_custom_game_id_map().get(game_id, "")
        except RuntimeError:
            return ""

    return ""


def _resolve_twitch_event_date_kyiv(created_at: Optional[datetime]) -> date:
    if created_at is None:
        return _today_in_kyiv()

    event_dt = created_at
    if event_dt.tzinfo is None:
        event_dt = event_dt.replace(tzinfo=ZoneInfo("UTC"))
    return event_dt.astimezone(KYIV_TZ).date()


def _resolve_twitch_game_instance_key(game_scope: str, created_at: Optional[datetime]) -> str:
    normalized_scope = _normalize_twitch_game_scope(game_scope)
    if not normalized_scope:
        return ""

    if normalized_scope == "daily:current":
        return f"date:{_resolve_twitch_event_date_kyiv(created_at).isoformat()}"

    if normalized_scope.startswith("date:"):
        raw_date = normalized_scope[5:]
        try:
            target_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            return ""
        return f"date:{target_date.isoformat()}"

    if normalized_scope.startswith("custom:"):
        game_id = _normalize_game_id(normalized_scope[7:])
        if not game_id:
            return ""
        return f"custom:{game_id}"

    return normalized_scope


def _resolve_secret_word_for_twitch_event(game_scope: str, created_at: Optional[datetime]) -> str:
    normalized_scope = _normalize_twitch_game_scope(game_scope)
    if not normalized_scope:
        return ""

    if normalized_scope == "daily:current":
        return _normalize_word(guess_secret_word_for_date(_resolve_twitch_event_date_kyiv(created_at)))

    return _resolve_secret_word_for_twitch_game_scope(normalized_scope)


def _load_twitch_chat_solver_leaderboard(channel: str, limit: int) -> List[Dict[str, Any]]:
    if not channel:
        return []

    rows = (
        TwitchChatEvent.query
        .options(load_only(
            TwitchChatEvent.game_scope,
            TwitchChatEvent.chatter_user_login,
            TwitchChatEvent.chatter_display_name,
            TwitchChatEvent.guessed_word,
            TwitchChatEvent.created_at,
        ))
        .filter(TwitchChatEvent.channel == channel)
        .order_by(TwitchChatEvent.id.asc())
        .all()
    )

    scope_secret_cache: Dict[str, str] = {}
    solved_pairs: set[Tuple[str, str]] = set()
    solver_map: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        game_scope = _normalize_twitch_game_scope(row.game_scope)
        chatter_login = _normalize_twitch_channel(row.chatter_user_login)
        guessed_word = _normalize_word(row.guessed_word)
        if not game_scope or not chatter_login or not guessed_word:
            continue

        game_instance_key = _resolve_twitch_game_instance_key(game_scope, row.created_at)
        if not game_instance_key:
            continue

        secret_word = scope_secret_cache.get(game_instance_key)
        if secret_word is None:
            secret_word = _resolve_secret_word_for_twitch_event(game_scope, row.created_at)
            scope_secret_cache[game_instance_key] = secret_word

        if not secret_word or guessed_word != secret_word:
            continue

        solved_key = (game_instance_key, chatter_login)
        if solved_key in solved_pairs:
            continue
        solved_pairs.add(solved_key)

        chatter_name = _normalize_twitch_text(
            row.chatter_display_name,
            fallback=chatter_login,
            max_length=100,
        )
        solver_entry = solver_map.get(chatter_login)
        if solver_entry is None:
            solver_entry = {
                "user_login": chatter_login,
                "user_name": chatter_name or chatter_login,
                "solved_count": 0,
                "last_solved_at": row.created_at,
            }
            solver_map[chatter_login] = solver_entry

        solver_entry["solved_count"] += 1
        solver_entry["last_solved_at"] = row.created_at
        if chatter_name:
            solver_entry["user_name"] = chatter_name

    leaderboard = list(solver_map.values())
    leaderboard.sort(
        key=lambda item: (
            -int(item["solved_count"]),
            -(item["last_solved_at"].timestamp() if item.get("last_solved_at") else 0),
            item["user_login"],
        )
    )

    trimmed = leaderboard[:limit]
    return [
        {
            "user_login": item["user_login"],
            "user_name": item["user_name"],
            "solved_count": int(item["solved_count"]),
        }
        for item in trimmed
    ]


def _prune_twitch_chat_events_if_needed(force: bool = False) -> None:
    global LAST_TWITCH_CHAT_PRUNE_AT

    if not _is_twitch_chat_bridge_enabled():
        return

    now = time.time()
    if not force and (now - LAST_TWITCH_CHAT_PRUNE_AT) < TWITCH_CHAT_PRUNE_INTERVAL_SECONDS:
        return

    LAST_TWITCH_CHAT_PRUNE_AT = now
    cutoff = datetime.utcnow() - timedelta(hours=TWITCH_CHAT_EVENT_RETENTION_HOURS)

    db.session.query(TwitchChatEvent).filter(
        TwitchChatEvent.created_at < cutoff
    ).delete(synchronize_session=False)

    total_rows = db.session.query(TwitchChatEvent.id).count()
    overflow = total_rows - TWITCH_CHAT_MAX_STORED_EVENTS
    if overflow > 0:
        overflow_ids = [
            row.id
            for row in TwitchChatEvent.query
            .order_by(TwitchChatEvent.id.asc())
            .limit(overflow)
            .all()
        ]
        if overflow_ids:
            db.session.query(TwitchChatEvent).filter(
                TwitchChatEvent.id.in_(overflow_ids)
            ).delete(synchronize_session=False)

    db.session.commit()


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
    today_kyiv = _today_in_kyiv()
    if game_date < today_kyiv:
        date_relation = "past"
    elif game_date > today_kyiv:
        date_relation = "future"
    else:
        date_relation = "today"

    return {
        "game_date": game_date.isoformat(),
        "today_kyiv": today_kyiv.isoformat(),
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
    return render_template(
        "index.html",
        twitch_oauth_enabled=_is_twitch_oauth_enabled(),
    )


@app.route("/create-game")
def create_game_page():
    return render_template("create_game.html")


@app.route("/auth/twitch/start")
def twitch_oauth_start():
    if not _is_twitch_oauth_enabled():
        abort(404)

    if _should_use_twitch_mock_oauth():
        return redirect(url_for("twitch_mock_start", next=request.args.get("next") or request.referrer or "/"))

    state = secrets.token_urlsafe(24)
    session[_twitch_oauth_state_session_key()] = state
    session[_twitch_oauth_next_session_key()] = _sanitize_local_redirect_target(
        request.args.get("next") or request.referrer or "/"
    )
    return redirect(_build_twitch_authorize_url(state))


@app.route("/auth/twitch/callback")
def twitch_oauth_callback():
    if not _is_twitch_oauth_enabled():
        abort(404)

    error = request.args.get("error")
    if error:
        target = _sanitize_local_redirect_target(session.pop(_twitch_oauth_next_session_key(), "/"))
        session.pop(_twitch_oauth_state_session_key(), None)
        separator = "&" if "?" in target else "?"
        return redirect(f"{target}{separator}twitch_error={urllib.parse.quote(error)}")

    expected_state = session.pop(_twitch_oauth_state_session_key(), None)
    provided_state = request.args.get("state", "")
    if not expected_state or not isinstance(expected_state, str) or not hmac.compare_digest(expected_state, provided_state):
        abort(400)

    code = (request.args.get("code") or "").strip()
    if not code:
        abort(400)

    try:
        token_payload = _exchange_twitch_authorization_code(code)
        access_token = _normalize_twitch_text(token_payload.get("access_token"), max_length=2000)
        validate_payload = _validate_twitch_access_token(access_token)
        profile_payload = _fetch_twitch_user_profile(access_token)
        row = _upsert_twitch_connection(validate_payload, token_payload, profile_payload)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        print(f"[TWITCH OAUTH] HTTP error during callback: {exc.code} {body}")
        abort(502)
    except Exception as exc:
        print(f"[TWITCH OAUTH] Callback failed: {exc}")
        abort(500)

    session[_twitch_connection_session_key()] = row.id
    target = _sanitize_local_redirect_target(session.pop(_twitch_oauth_next_session_key(), "/"))
    separator = "&" if "?" in target else "?"
    return redirect(f"{target}{separator}twitch_connected=1")


@app.route("/auth/twitch/mock-start")
def twitch_mock_start():
    if not _should_use_twitch_mock_oauth():
        abort(404)

    try:
        row = _upsert_mock_twitch_connection()
    except Exception as exc:
        db.session.rollback()
        print(f"[TWITCH MOCK] Failed to create mock connection: {exc}")
        abort(500)

    session[_twitch_connection_session_key()] = row.id
    target = _sanitize_local_redirect_target(request.args.get("next") or request.referrer or "/")
    separator = "&" if "?" in target else "?"
    return redirect(f"{target}{separator}twitch_connected=1")


@app.route("/api/twitch-connection/status")
def twitch_connection_status():
    connection = _load_current_twitch_connection()
    use_mock_oauth = _should_use_twitch_mock_oauth()
    response = jsonify({
        "oauth_enabled": _is_twitch_oauth_enabled(),
        "worker_ready": _is_twitch_worker_ready(),
        "connected": bool(connection),
        "connection": _serialize_twitch_connection(connection),
        "connect_url": url_for(
            "twitch_mock_start" if use_mock_oauth else "twitch_oauth_start",
            next=request.args.get("next") or request.referrer or "/",
        )
        if _is_twitch_oauth_enabled()
        else None,
    })
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.route("/api/twitch-connection/disconnect", methods=["POST"])
def twitch_connection_disconnect():
    connection = _load_current_twitch_connection()
    if connection is None:
        return jsonify({"ok": True, "connected": False})

    try:
        connection.is_active = False
        connection.disconnected_at = datetime.utcnow()
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f"[TWITCH OAUTH] Disconnect failed for {connection.twitch_login}: {exc}")
        return jsonify({"error": "Не вдалося відключити Twitch."}), 500

    session.pop(_twitch_connection_session_key(), None)
    response = jsonify({"ok": True, "connected": False})
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.route("/api/twitch-worker/channels")
def twitch_worker_channels():
    if not _is_twitch_chat_bridge_enabled():
        return jsonify({"error": "Twitch worker не налаштований на сервері."}), 503

    provided_secret = request.headers.get("X-Twitch-Bridge-Secret", "")
    if not provided_secret or not hmac.compare_digest(provided_secret, TWITCH_CHAT_BRIDGE_SECRET):
        return jsonify({"error": "Недійсний ключ Twitch worker."}), 401

    try:
        channels = _run_db_query_with_retry(_load_worker_active_twitch_channels)
    except (OperationalError, InterfaceError):
        return jsonify({"error": "Тимчасова помилка читання Twitch-підключень."}), 503
    except Exception as exc:
        print(f"[TWITCH WORKER] Failed to load channels: {exc}")
        return jsonify({"error": "Не вдалося прочитати Twitch-підключення."}), 500

    response = jsonify({
        "channels": channels,
        "count": len(channels),
    })
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.route("/api/twitch-worker/connections")
def twitch_worker_connections():
    if not _is_twitch_chat_bridge_enabled():
        return jsonify({"error": "Twitch worker не налаштований на сервері."}), 503

    provided_secret = request.headers.get("X-Twitch-Bridge-Secret", "")
    if not provided_secret or not hmac.compare_digest(provided_secret, TWITCH_CHAT_BRIDGE_SECRET):
        return jsonify({"error": "Недійсний ключ Twitch worker."}), 401

    try:
        rows = _run_db_query_with_retry(_load_worker_active_twitch_connections)
    except (OperationalError, InterfaceError):
        return jsonify({"error": "Тимчасова помилка читання Twitch-підключень."}), 503
    except Exception as exc:
        print(f"[TWITCH WORKER] Failed to load connections: {exc}")
        return jsonify({"error": "Не вдалося прочитати Twitch-підключення."}), 500

    active_connections: List[Dict[str, Any]] = []
    skipped_connections: List[Dict[str, Any]] = []

    for connection, active_target in rows:
        missing_scopes = _twitch_connection_missing_required_scopes(connection)
        if missing_scopes:
            skipped_connections.append({
                "twitch_login": connection.twitch_login,
                "twitch_user_id": connection.twitch_user_id,
                "reason": "missing_scopes",
                "missing_scopes": missing_scopes,
            })
            continue

        try:
            refreshed_connection = _run_db_query_with_retry(
                lambda connection_id=connection.id: _refresh_twitch_connection_if_needed(
                    TwitchConnection.query.get(connection_id)
                )
            )
        except Exception as exc:
            skipped_connections.append({
                "twitch_login": connection.twitch_login,
                "twitch_user_id": connection.twitch_user_id,
                "reason": "token_refresh_failed",
                "error": str(exc),
            })
            print(f"[TWITCH WORKER] Failed to refresh token for {connection.twitch_login}: {exc}")
            continue

        active_connections.append({
            "connection_id": refreshed_connection.id,
            "twitch_user_id": refreshed_connection.twitch_user_id,
            "twitch_login": refreshed_connection.twitch_login,
            "twitch_display_name": refreshed_connection.twitch_display_name,
            "access_token": refreshed_connection.access_token,
            "token_scopes": _twitch_connection_scope_values(refreshed_connection),
            "game_scope": active_target.game_scope,
            "page_url": active_target.page_url or None,
        })

    response = jsonify({
        "connections": active_connections,
        "count": len(active_connections),
        "required_scopes": TWITCH_EVENTSUB_REQUIRED_SCOPES,
        "skipped": skipped_connections,
    })
    response.headers["Cache-Control"] = "private, no-store"
    return response


def dev_archive_game_page():
    _require_dev_mode_enabled()
    response = Response(render_template(
        "dev_archive_game.html",
        dev_authenticated=_has_dev_access(),
        today_kyiv=_today_in_kyiv().isoformat(),
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
        # Базова "сьогоднішня" гра перемикається о 00:00 за Києвом
        target = _today_in_kyiv()

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


@app.route("/api/twitch-chat/target", methods=["POST"])
def twitch_chat_target():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Очікував JSON-об'єкт."}), 400

    current_connection = _load_current_twitch_connection()
    channel = current_connection.twitch_login if current_connection else _normalize_twitch_channel(payload.get("channel"))
    game_scope = _normalize_twitch_game_scope(payload.get("game_scope"))
    page_url = _normalize_twitch_page_url(payload.get("page_url"))

    if not channel:
        return jsonify({"error": "Передайте назву Twitch-каналу в полі 'channel'."}), 400
    if not game_scope:
        return jsonify({"error": "Передайте scope активної гри в полі 'game_scope'."}), 400

    try:
        row = _run_db_query_with_retry(
            lambda: _upsert_twitch_chat_active_target(channel, game_scope, page_url)
        )
        latest_event_id = _run_db_query_with_retry(
            lambda: _load_twitch_chat_latest_event_id(channel, game_scope)
        )
    except (OperationalError, InterfaceError):
        db.session.rollback()
        return jsonify({"error": "Тимчасова помилка збереження активної Twitch-гри."}), 503
    except Exception as e:
        db.session.rollback()
        print(f"[TWITCH CHAT] Помилка target для channel='{channel}', scope='{game_scope}': {e}")
        return jsonify({"error": "Не вдалося зберегти активну гру для Twitch."}), 500

    response = jsonify({
        "ok": True,
        "channel": row.channel,
        "game_scope": row.game_scope,
        "page_url": row.page_url or None,
        "latest_event_id": latest_event_id,
        "poll_interval_ms": TWITCH_CHAT_POLL_INTERVAL_MS,
        "target_ttl_seconds": TWITCH_CHAT_TARGET_TTL_SECONDS,
    })
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.route("/api/twitch-chat/status")
def twitch_chat_status():
    channel = _normalize_twitch_channel(request.args.get("channel"))
    game_scope = _normalize_twitch_game_scope(request.args.get("game_scope"))

    try:
        latest_event_id = _run_db_query_with_retry(
            lambda: _load_twitch_chat_latest_event_id(channel, game_scope)
        )
    except (OperationalError, InterfaceError):
        return jsonify({"error": "Тимчасова помилка підключення до Twitch-черги."}), 503
    except Exception as e:
        print(f"[TWITCH CHAT] Помилка status для channel='{channel}', scope='{game_scope}': {e}")
        return jsonify({"error": "Не вдалося прочитати Twitch-чергу."}), 500

    response = jsonify({
        "bridge_enabled": _is_twitch_chat_bridge_enabled(),
        "channel": channel or None,
        "game_scope": game_scope or None,
        "latest_event_id": latest_event_id,
        "poll_interval_ms": TWITCH_CHAT_POLL_INTERVAL_MS,
        "target_ttl_seconds": TWITCH_CHAT_TARGET_TTL_SECONDS,
    })
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.route("/api/twitch-chat/events")
def twitch_chat_events():
    raw_after_id = request.args.get("after_id", "0")
    raw_limit = request.args.get("limit", str(TWITCH_CHAT_EVENT_POLL_LIMIT))
    channel = _normalize_twitch_channel(request.args.get("channel"))
    game_scope = _normalize_twitch_game_scope(request.args.get("game_scope"))

    try:
        after_id = max(0, int(raw_after_id))
    except ValueError:
        return jsonify({"error": "Параметр after_id має бути цілим числом."}), 400

    try:
        limit = int(raw_limit)
    except ValueError:
        return jsonify({"error": "Параметр limit має бути цілим числом."}), 400

    limit = max(1, min(limit, TWITCH_CHAT_MAX_FETCH_LIMIT))

    try:
        rows = _run_db_query_with_retry(
            lambda: _load_twitch_chat_events(after_id, channel, game_scope, limit)
        )
    except (OperationalError, InterfaceError):
        return jsonify({"error": "Тимчасова помилка підключення до Twitch-черги."}), 503
    except Exception as e:
        print(f"[TWITCH CHAT] Помилка читання подій для channel='{channel}', scope='{game_scope}': {e}")
        return jsonify({"error": "Не вдалося прочитати Twitch-події."}), 500

    response = jsonify({
        "events": [_serialize_twitch_chat_event(row) for row in rows],
        "next_after_id": rows[-1].id if rows else after_id,
    })
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.route("/api/twitch-chat/solvers")
def twitch_chat_solvers():
    channel = _normalize_twitch_channel(request.args.get("channel"))
    if not channel:
        return jsonify({"error": "Передайте Twitch-канал у параметрі 'channel'."}), 400

    raw_limit = request.args.get("limit", "50")
    try:
        limit = int(raw_limit)
    except ValueError:
        return jsonify({"error": "Параметр limit має бути цілим числом."}), 400

    limit = max(1, min(limit, 200))

    try:
        solvers = _run_db_query_with_retry(
            lambda: _load_twitch_chat_solver_leaderboard(channel, limit)
        )
    except (OperationalError, InterfaceError):
        return jsonify({"error": "Тимчасова помилка читання Twitch-рейтингу."}), 503
    except Exception as e:
        print(f"[TWITCH CHAT] Помилка solvers для channel='{channel}': {e}")
        return jsonify({"error": "Не вдалося прочитати рейтинг Twitch-чату."}), 500

    response = jsonify({
        "channel": channel,
        "solvers": solvers,
        "count": len(solvers),
    })
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.route("/api/twitch-chat/publish", methods=["POST"])
def twitch_chat_publish():
    if not _is_twitch_chat_bridge_enabled():
        return jsonify({"error": "Twitch bridge не налаштований на сервері."}), 503

    provided_secret = request.headers.get("X-Twitch-Bridge-Secret", "")
    if not provided_secret or not hmac.compare_digest(provided_secret, TWITCH_CHAT_BRIDGE_SECRET):
        return jsonify({"error": "Недійсний ключ Twitch bridge."}), 401

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Очікував JSON-об'єкт."}), 400

    channel = _normalize_twitch_channel(payload.get("channel"))
    if not channel:
        return jsonify({"error": "Передайте назву Twitch-каналу в полі 'channel'."}), 400

    game_scope = _normalize_twitch_game_scope(payload.get("game_scope"))
    if not game_scope:
        try:
            game_scope = _run_db_query_with_retry(
                lambda: _resolve_active_twitch_game_scope(channel)
            ) or ""
        except (OperationalError, InterfaceError):
            return jsonify({"error": "Тимчасова помилка пошуку активної Twitch-гри."}), 503
        except Exception as e:
            print(f"[TWITCH CHAT] Помилка resolve active scope для channel='{channel}': {e}")
            return jsonify({"error": "Не вдалося визначити активну гру для Twitch-каналу."}), 500

    if not game_scope:
        response = jsonify({"accepted": False, "reason": "no_active_game"})
        response.headers["Cache-Control"] = "private, no-store"
        return response, 202

    source_message_id = _normalize_twitch_text(
        payload.get("message_id"),
        fallback="",
        max_length=120,
    )
    resolved_word = _resolve_twitch_guess_word(payload.get("word"))
    if not resolved_word:
        response = jsonify({"accepted": False, "reason": "unknown_word"})
        response.headers["Cache-Control"] = "private, no-store"
        return response, 202

    chatter_user_login = _normalize_twitch_channel(payload.get("user_login")) or "chat"
    chatter_display_name = _normalize_twitch_text(
        payload.get("user_name"),
        fallback=chatter_user_login,
        max_length=100,
    )
    raw_message = _normalize_twitch_text(
        payload.get("message"),
        fallback=resolved_word,
        max_length=500,
    )

    if source_message_id:
        try:
            existing_row = _run_db_query_with_retry(
                lambda: _load_twitch_chat_event_by_source_message(
                    channel,
                    game_scope,
                    source_message_id,
                )
            )
        except (OperationalError, InterfaceError):
            return jsonify({"error": "Тимчасова помилка перевірки дубля Twitch-події."}), 503
        except Exception as e:
            print(f"[TWITCH CHAT] Помилка duplicate-check для payload={payload!r}: {e}")
            return jsonify({"error": "Не вдалося перевірити дублікат Twitch-події."}), 500

        if existing_row is not None:
            response = jsonify({
                "accepted": True,
                "duplicate": True,
                "event_id": existing_row.id,
                "game_scope": existing_row.game_scope,
            })
            response.headers["Cache-Control"] = "private, no-store"
            return response

    row = TwitchChatEvent(
        channel=channel,
        game_scope=game_scope,
        source_message_id=source_message_id or None,
        chatter_user_login=chatter_user_login,
        chatter_display_name=chatter_display_name,
        raw_message=raw_message,
        guessed_word=resolved_word,
    )

    try:
        db.session.add(row)
        db.session.commit()
    except (OperationalError, InterfaceError):
        db.session.rollback()
        return jsonify({"error": "Тимчасова помилка запису Twitch-події."}), 503
    except Exception as e:
        db.session.rollback()
        print(f"[TWITCH CHAT] Помилка publish для payload={payload!r}: {e}")
        return jsonify({"error": "Не вдалося зберегти Twitch-подію."}), 500

    try:
        _prune_twitch_chat_events_if_needed()
    except Exception as e:
        db.session.rollback()
        print(f"[TWITCH CHAT] Не вдалося почистити старі події: {e}")

    response = jsonify({
        "accepted": True,
        "event_id": row.id,
        "word": resolved_word,
        "channel": channel,
        "game_scope": game_scope,
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
    delta = (_today_in_kyiv() - BASE_DATE).days
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

@app.route("/.well-known/assetlinks.json")
def assetlinks():
    well_known_dir = os.path.join(app.static_folder or "", ".well-known")
    return send_from_directory(well_known_dir, "assetlinks.json", mimetype="application/json")

@app.route("/robots.txt")
def robots_txt():
    sitemap_url = request.url_root.rstrip('/') + '/sitemap.xml'
    return Response(f"User-agent: *\nAllow: /\n\nSitemap: {sitemap_url}", mimetype='text/plain')

@app.route("/sitemap.xml")
def sitemap_xml():
    base_url = request.url_root.rstrip('/')
    last_mod = _today_in_kyiv().isoformat()
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
            ensure_twitch_chat_event_schema()
            import_json_into_sqlite_if_needed()
        click.echo("DB initialized (and imported from JSON if applicable).")
except Exception:
    pass

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        ensure_twitch_chat_event_schema()
        import_json_into_sqlite_if_needed()
    app.run(debug=True)
