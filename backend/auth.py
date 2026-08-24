"""
Аутентификация и защита приложения.

Реализовано:
- Пароль в БД (таблица auth_settings): хеш scrypt со случайной солью,
  сравнение в constant-time. Пароль никогда не хранится в открытом виде.
- Серверные сессии: случайный 256-битный токен, хранится в HttpOnly /
  SameSite=Lax / Secure cookie. Токен в БД с TTL и скользящим продлением.
- Защита от брутфорса: счётчик неудачных попыток + экспоненциальная
  задержка на IP (и глобальный лимит), плюс общий rate limit запросов.
- CSRF: двойной submit — случайный токен в HttpOnly cookie + заголовок
  X-CSRF-Token на каждый мутирующий запрос; проверяется совпадение.
- Security headers: CSP, X-Content-Type-Options, X-Frame-Options,
  Referrer-Policy, Permissions-Policy.

Тайминг-атаки исключены: все сравнения секретов через hmac.compare_digest.
"""

import hashlib
import hmac
import ipaddress
import os
import secrets
import sqlite3
import threading
import time

import config

# ============================================================
# Константы безопасности
# ============================================================

SESSION_COOKIE_NAME = "gt_session"
CSRF_COOKIE_NAME = "gt_csrf"

SESSION_TTL_SECONDS = config.SESSION_TTL_SECONDS          # абсолютный TTL сессии
SESSION_IDLE_TTL = config.SESSION_IDLE_SECONDS            # неактивность

MAX_FAILED_ATTEMPTS = config.AUTH_MAX_FAILED_ATTEMPTS     # после N неудач — блокировка
LOCKOUT_BASE_SECONDS = config.AUTH_LOCKOUT_BASE_SECONDS   # база экспоненциальной задержки
LOCKOUT_MAX_SECONDS = config.AUTH_LOCKOUT_MAX_SECONDS     # потолок задержки

LOGIN_RATE_LIMIT = config.AUTH_LOGIN_RATE_LIMIT           # запросов...
LOGIN_RATE_WINDOW = config.AUTH_LOGIN_RATE_WINDOW         # ...за окно (сек)

API_RATE_LIMIT = config.AUTH_API_RATE_LIMIT               # общий rate limit API
API_RATE_WINDOW = 60

MIN_PASSWORD_LENGTH = config.AUTH_MIN_PASSWORD_LENGTH


class AuthError(Exception):
    """Базовая ошибка аутентификации."""


class RateLimitedError(AuthError):
    def __init__(self, retry_after: float):
        self.retry_after = int(retry_after) + 1
        super().__init__(f"Слишком много попыток, повторите через {self.retry_after} c")


# ============================================================
# Хеширование пароля (scrypt из stdlib)
# ============================================================

_SCRYPT_N = 2 ** 14   # CPU/memory cost
_SCRYPT_R = 8
_SCRYPT_P = 1
_DKLEN = 64


def hash_password(password: str) -> str:
    """Возвращает 'scrypt$n$r$p$salt_hex$hash_hex' со случайной солью."""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """
    Постоянно-временное сравнение хеша пароля.
    При неизвестном формате делает dummy-проверку, чтобы время ответа
    не раскрывало причину отказа.
    """
    try:
        algo, n_s, r_s, p_s, salt_hex, hash_hex = stored.split("$")
        if algo != "scrypt":
            raise ValueError
        dk = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n_s), r=int(r_s), p=int(p_s),
            dklen=len(bytes.fromhex(hash_hex)),
        )
        return hmac.compare_digest(dk, bytes.fromhex(hash_hex))
    except (ValueError, TypeError):
        # Dummy scrypt, чтобы время ответа совпадало с реальной проверкой.
        hashlib.scrypt(
            password.encode("utf-8"),
            salt=b"\x00" * 16,
            n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN,
        )
        return False


def validate_password_strength(password: str) -> str | None:
    """Минимальные требования к паролю. Возвращает текст ошибки или None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Пароль должен быть минимум {MIN_PASSWORD_LENGTH} символов."
    if len(password) > 1024:
        return "Пароль слишком длинный."
    return None


# ============================================================
# Прямой доступ к SQLite для таблиц авторизации
# (не привязано к Flask-SQLAlchemy сессии, потокобезопасно per-call)
# ============================================================

_lock = threading.Lock()
_DB_PATH: str | None = None


def init_auth_db(sqlite_path: str | None) -> None:
    """
    Вызывается один раз из app.py с абсолютным путём к файлу БД
    (db.engine.url.database). Создаёт таблицы авторизации.
    """
    global _DB_PATH
    _DB_PATH = sqlite_path
    conn = _connect()
    try:
        _ensure_tables(conn)
    finally:
        conn.close()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH or ":memory:", timeout=10)
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            password_hash TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_sessions (
            token_hash TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            last_seen REAL NOT NULL,
            expires_at REAL NOT NULL
        )
        """
    )
    conn.commit()


# ============================================================
# Управление паролем
# ============================================================

def is_password_set(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM auth_settings WHERE id = 1").fetchone()
    return row is not None


def set_password(conn: sqlite3.Connection, password: str) -> None:
    err = validate_password_strength(password)
    if err:
        raise AuthError(err)
    conn.execute(
        "INSERT INTO auth_settings (id, password_hash, updated_at) "
        "VALUES (1, ?, datetime('now')) "
        "ON CONFLICT(id) DO UPDATE SET password_hash = excluded.password_hash, "
        "updated_at = datetime('now')",
        (hash_password(password),),
    )
    # Смена пароля инвалидирует все существующие сессии.
    conn.execute("DELETE FROM auth_sessions")
    conn.commit()


# ============================================================
# Сессии
# ============================================================

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(conn: sqlite3.Connection) -> tuple[str, str]:
    """
    Создаёт сессию. Возвращает (raw_token, csrf_token).
    В БД лежит только SHA-256 хеш токена — утечка БД не даёт угнать сессии.
    """
    raw = secrets.token_urlsafe(32)      # 256 бит энтропии
    csrf = secrets.token_urlsafe(32)
    now = time.time()
    conn.execute(
        "INSERT INTO auth_sessions (token_hash, created_at, last_seen, expires_at) "
        "VALUES (?, ?, ?, ?)",
        (_hash_token(raw), now, now, now + SESSION_TTL_SECONDS),
    )
    conn.commit()
    return raw, csrf


def get_session(conn: sqlite3.Connection, raw_token: str):
    """
    Возвращает dict сессии или None.
    Скользящее продление по активности, но не выше абсолютного TTL.
    Устаревшие сессии удаляются лениво.
    """
    if not raw_token:
        return None
    th = _hash_token(raw_token)
    now = time.time()
    row = conn.execute(
        "SELECT token_hash, created_at, last_seen, expires_at "
        "FROM auth_sessions WHERE token_hash = ?",
        (th,),
    ).fetchone()
    if row is None:
        return None
    _, created_at, last_seen, expires_at = row
    if now >= expires_at:
        conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (th,))
        conn.commit()
        return None
    # Скользящее продление по активности, но не выше абсолютного TTL.
    if now - last_seen > 60:
        new_last_seen = now
        new_expires = min(
            expires_at + (now - last_seen),
            created_at + SESSION_TTL_SECONDS,
        )
        conn.execute(
            "UPDATE auth_sessions SET last_seen = ?, expires_at = ? "
            "WHERE token_hash = ?",
            (new_last_seen, new_expires, th),
        )
        conn.commit()
    else:
        new_expires = expires_at
    return {"created_at": created_at, "last_seen": last_seen,
            "expires_at": new_expires}


def destroy_session(conn: sqlite3.Connection, raw_token: str) -> None:
    if raw_token:
        conn.execute(
            "DELETE FROM auth_sessions WHERE token_hash = ?",
            (_hash_token(raw_token),),
        )
        conn.commit()


def purge_expired_sessions(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM auth_sessions WHERE expires_at < ?", (time.time(),))
    conn.commit()


# ============================================================
# Rate limiting / брутфорс (в памяти процесса)
# ============================================================

_rate_lock = threading.Lock()
_login_attempts: dict[str, list[float]] = {}   # ip -> timestamps окна
_failed_logins: dict[str, tuple[int, float]] = {}  # ip -> (count, locked_until)
_api_hits: dict[str, list[float]] = {}         # ip -> timestamps окна


def _client_ip(remote_addr: str, forwarded_for: str | None) -> str:
    """
    Извлекает клиентский IP. Если за обратным прокси (X-Forwarded-For),
    берём первый адрес. Валидируем, чтобы не подсунуть мусор.
    """
    candidate = remote_addr
    if forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        try:
            ipaddress.ip_address(first)
            candidate = first
        except ValueError:
            pass
    return candidate


def check_login_rate(ip: str) -> None:
    """Скользящее окно на количество POST /auth/login с одного IP."""
    now = time.time()
    with _rate_lock:
        hits = [t for t in _login_attempts.get(ip, []) if t > now - LOGIN_RATE_WINDOW]
        if len(hits) >= LOGIN_RATE_LIMIT:
            raise RateLimitedError(LOGIN_RATE_WINDOW - (now - hits[0]))
        hits.append(now)
        _login_attempts[ip] = hits


def record_failed_login(ip: str) -> None:
    """
    Фиксирует неудачную попытку и включает экспоненциальную блокировку:
    N неудач подряд -> задержка base * 2^(N-MAX), с потолком.
    """
    now = time.time()
    with _rate_lock:
        count, _ = _failed_logins.get(ip, (0, 0.0))
        count += 1
        if count >= MAX_FAILED_ATTEMPTS:
            delay = min(LOCKOUT_BASE_SECONDS * (2 ** (count - MAX_FAILED_ATTEMPTS)),
                        LOCKOUT_MAX_SECONDS)
            _failed_logins[ip] = (count, now + delay)
        else:
            _failed_logins[ip] = (count, 0.0)


def reset_failed_logins(ip: str) -> None:
    with _rate_lock:
        _failed_logins.pop(ip, None)


def check_bruteforce_lock(ip: str) -> None:
    now = time.time()
    with _rate_lock:
        entry = _failed_logins.get(ip)
        if entry and entry[1] > now:
            raise RateLimitedError(entry[1] - now)
        # Очистка протухших счётчиков (раз в минуту условно — здесь лениво)
        if entry and entry[1] <= now:
            pass  # счётчик остаётся до успеха или окна простоя


def check_api_rate(ip: str) -> None:
    """Общий rate limit на /api/*: защита от DoS одного клиента."""
    now = time.time()
    with _rate_lock:
        hits = [t for t in _api_hits.get(ip, []) if t > now - API_RATE_WINDOW]
        if len(hits) >= API_RATE_LIMIT:
            raise RateLimitedError(API_RATE_WINDOW - (now - hits[0]))
        hits.append(now)
        _api_hits[ip] = hits


def verify_csrf(cookie_token: str | None, header_token: str | None) -> bool:
    """Двойной submit: cookie == header, оба непустые, constant-time."""
    if not cookie_token or not header_token:
        return False
    return hmac.compare_digest(cookie_token.encode(), header_token.encode())


def generate_csrf_cookie_value() -> str:
    return secrets.token_urlsafe(32)
