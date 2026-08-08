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


SERVER_LOCATION = _get_str("SERVER_LOCATION", "home", empty_as_default=True).lower()
if SERVER_LOCATION not in {"home", "vps"}:
    SERVER_LOCATION = "home"

CDP_URL = _get_str("CDP_URL", "http://localhost:9222", empty_as_default=True)

TELEGRAM_BOT_TOKEN = _get_str("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = _get_str("TELEGRAM_CHAT_ID", "")

PUBLIC_BROWSER_URL = _get_str("PUBLIC_BROWSER_URL", "")

HUMAN_WAIT_SECONDS = _get_int("HUMAN_WAIT_SECONDS", 600)

LLM_API_KEY = _get_str("LLM_API_KEY", "")
LLM_MODEL = _get_str("LLM_MODEL", "gpt-4o-mini", empty_as_default=True)
LLM_BASE_URL = _get_str("LLM_BASE_URL", "https://api.openai.com/v1", empty_as_default=True)

ANALYZER_MAX_DOM_CHARS = _get_int("ANALYZER_MAX_DOM_CHARS", 200000)
PROFILE_RESCAN_COOLDOWN_SECONDS = _get_int("PROFILE_RESCAN_COOLDOWN_SECONDS", 3600)
SMART_MAX_DETAIL_PAGES = _get_int("SMART_MAX_DETAIL_PAGES", 100)
SMART_MAX_NEW_CHILD_PROFILES = _get_int("SMART_MAX_NEW_CHILD_PROFILES", 20)

SQLALCHEMY_DATABASE_URI = _get_str(
    "SQLALCHEMY_DATABASE_URI",
    "sqlite:///db.sqlite3",
    empty_as_default=True
)

SQLALCHEMY_TRACK_MODIFICATIONS = False