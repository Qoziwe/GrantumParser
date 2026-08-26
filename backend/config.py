import os
from pathlib import Path


def _load_env_file() -> None:
    """
    Загружает .env, если установлен python-dotenv.

    Приоритет:
      1. backend/.env
      2. .env в текущей рабочей директории

    override=False, чтобы уже выставленные переменные окружения
    имели приоритет над .env.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        # Если python-dotenv не установлен, просто не читаем .env.
        # Приложение продолжит работать с обычным окружением.
        return

    candidates = (
        Path(__file__).resolve().parent / ".env",
        Path.cwd() / ".env",
    )

    for path in candidates:
        if path.exists():
            load_dotenv(dotenv_path=path, override=False)
            break


_load_env_file()


def _get_str(key: str, default: str = "", empty_as_default: bool = False) -> str:
    value = os.getenv(key)
    if value is None:
        return default

    value = value.strip()

    if empty_as_default and value == "":
        return default

    return value


def _get_int(key: str, default: int) -> int:
    value = os.getenv(key)
    if value is None or value.strip() == "":
        return default

    try:
        return int(value.strip())
    except ValueError:
        return default


def _get_str_list(key: str, default: str = ""):
    value = _get_str(key, default)
    if not value:
        return []

    value = value.replace(";", ",").replace("\n", ",")

    result = []
    for item in value.split(","):
        item = item.strip()
        if item:
            result.append(item)

    return result


SERVER_LOCATION = _get_str("SERVER_LOCATION", "home", empty_as_default=True).lower()
if SERVER_LOCATION not in {"home", "vps"}:
    SERVER_LOCATION = "home"

CDP_URL = _get_str("CDP_URL", "http://localhost:9222", empty_as_default=True)
TELEGRAM_BOT_TOKEN = _get_str("TELEGRAM_BOT_TOKEN", "")
# Список chat_id через запятую — уведомления получат все указанные чаты.
TELEGRAM_CHAT_IDS = [
    x.strip() for x in _get_str("TELEGRAM_CHAT_ID", "").split(",") if x.strip()
]
TELEGRAM_CHAT_ID = TELEGRAM_CHAT_IDS[0] if TELEGRAM_CHAT_IDS else ""
PUBLIC_BROWSER_URL = _get_str("PUBLIC_BROWSER_URL", "")
HUMAN_WAIT_SECONDS = _get_int("HUMAN_WAIT_SECONDS", 600)

# Как часто (в секундах) слать в Telegram прогресс-уведомления о парсинге.
# 0 — отключить прогресс-уведомления.
PROGRESS_NOTIFY_EVERY_SECONDS = _get_int("PROGRESS_NOTIFY_EVERY_SECONDS", 120)

_LLM_API_KEYS = _get_str_list("LLM_API_KEYS", "")
_LLM_API_KEY = _get_str("LLM_API_KEY", "")

LLM_API_KEYS = _LLM_API_KEYS if _LLM_API_KEYS else ([_LLM_API_KEY] if _LLM_API_KEY else [])
LLM_API_KEY = LLM_API_KEYS[0] if LLM_API_KEYS else ""

LLM_MODEL = _get_str("LLM_MODEL", "gpt-4o-mini", empty_as_default=True)
LLM_BASE_URL = _get_str("LLM_BASE_URL", "https://api.openai.com/v1", empty_as_default=True)

# Лимиты для одного LLM-ключа.
LLM_RPM_LIMIT = _get_int("LLM_RPM_LIMIT", 15)
LLM_TPM_LIMIT = _get_int("LLM_TPM_LIMIT", 250000)
LLM_DAILY_REQUEST_LIMIT = _get_int("LLM_DAILY_REQUEST_LIMIT", 500)

# Сколько ждать доступный ключ, если все ключи временно в rate limit.
LLM_KEY_WAIT_SECONDS = _get_int("LLM_KEY_WAIT_SECONDS", 120)

ANALYZER_MAX_DOM_CHARS = _get_int("ANALYZER_MAX_DOM_CHARS", 200000)
PROFILE_RESCAN_COOLDOWN_SECONDS = _get_int("PROFILE_RESCAN_COOLDOWN_SECONDS", 3600)
SMART_MAX_DETAIL_PAGES = _get_int("SMART_MAX_DETAIL_PAGES", 100)
SMART_MAX_NEW_CHILD_PROFILES = _get_int("SMART_MAX_NEW_CHILD_PROFILES", 20)

# AI-структурирование карточек в smart-режиме.
# Включает/выключает обработку raw_text через LLM.
SMART_STRUCTURING_ENABLED = _get_str("SMART_STRUCTURING_ENABLED", "true").lower() in ("1", "true", "yes")
# Размер батча: сколько карточек отправлять в одном промпте LLM.
SMART_STRUCTURING_BATCH_SIZE = _get_int("SMART_STRUCTURING_BATCH_SIZE", 5)
# Максимум ретраев при невалидном JSON от LLM.
SMART_STRUCTURING_MAX_RETRIES = _get_int("SMART_STRUCTURING_MAX_RETRIES", 2)

SQLALCHEMY_DATABASE_URI = _get_str(
    "SQLALCHEMY_DATABASE_URI",
    "sqlite:///db.sqlite3",
    empty_as_default=True
)
SQLALCHEMY_TRACK_MODIFICATIONS = False

# ============================================================
# Аутентификация / безопасность
# ============================================================

# Пароль доступа к сайту. Если пусто — при старте генерируется случайный
# и печатается в консоль бекенда (один раз).
AUTH_PASSWORD = _get_str("AUTH_PASSWORD", "", empty_as_default=True)

# TTL сессии: абсолютный и по неактивности (секунды).
SESSION_TTL_SECONDS = _get_int("SESSION_TTL_SECONDS", 12 * 3600)
SESSION_IDLE_SECONDS = _get_int("SESSION_IDLE_SECONDS", 3600)

# Флаг Secure у cookie сессии. На http://localhost браузер всё равно
# принимает Secure-куки, но на чистом HTTP вне localhost лучше false.
SESSION_COOKIE_SECURE = _get_str("SESSION_COOKIE_SECURE", "auto").lower()
if SESSION_COOKIE_SECURE not in {"auto", "true", "false"}:
    SESSION_COOKIE_SECURE = "auto"

# Защита от брутфорса логина.
AUTH_MAX_FAILED_ATTEMPTS = _get_int("AUTH_MAX_FAILED_ATTEMPTS", 5)
AUTH_LOCKOUT_BASE_SECONDS = _get_int("AUTH_LOCKOUT_BASE_SECONDS", 30)
AUTH_LOCKOUT_MAX_SECONDS = _get_int("AUTH_LOCKOUT_MAX_SECONDS", 3600)
AUTH_LOGIN_RATE_LIMIT = _get_int("AUTH_LOGIN_RATE_LIMIT", 10)
AUTH_LOGIN_RATE_WINDOW = _get_int("AUTH_LOGIN_RATE_WINDOW", 300)

# Общий rate limit на /api/* (запросов в минуту с одного IP).
AUTH_API_RATE_LIMIT = _get_int("AUTH_API_RATE_LIMIT", 240)

# Минимальная длина пароля при установке/смене.
AUTH_MIN_PASSWORD_LENGTH = _get_int("AUTH_MIN_PASSWORD_LENGTH", 10)

# Разрешённые origins фронтенда для CORS (с credentials нельзя "*").
_cors = _get_str("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
CORS_ORIGINS = [o.strip() for o in _cors.replace(";", ",").split(",") if o.strip()]