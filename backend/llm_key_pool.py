"""
Пул LLM API-ключей.

Отвечает за:
- дневной лимит запросов на ключ: 500/день по умолчанию;
- минутный лимит запросов: 15 RPM по умолчанию;
- минутный лимит токенов: 250k TPM по умолчанию;
- временные cooldown для минутных лимитов;
- блокировку ключа до следующего UTC-дня при дневном лимите;
- блокировку ключа при auth/billing ошибках.

Дневные счётчики хранятся в SQLite-таблице llm_key_pool_state.
Минутные лимиты хранятся в памяти процесса.
"""

import hashlib
import sqlite3
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config


class LlmKeyPoolError(Exception):
    """Базовая ошибка пула ключей."""


class LlmNoAvailableKeyError(LlmKeyPoolError):
    """Нет доступного ключа прямо сейчас."""


class LlmDailyLimitError(LlmKeyPoolError):
    """Все ключи исчерпали дневной лимит или заблокированы."""


_WINDOW_SECONDS = 60
_YEAR_3000 = 32503680000.0

_lock = threading.RLock()
_schema_ready = False

_windows = defaultdict(lambda: {
    "requests": deque(),
    "tokens": deque(),
    "pending_tokens": 0,
    "cooldown_until": 0.0,
})


class KeyReservation:
    __slots__ = (
        "key",
        "key_hash",
        "estimated_tokens",
        "started_at",
        "finished",
    )

    def __init__(self, key: str, key_hash: str, estimated_tokens: int):
        self.key = key
        self.key_hash = key_hash
        self.estimated_tokens = estimated_tokens
        self.started_at = time.time()
        self.finished = False


def has_keys() -> bool:
    return bool(getattr(config, "LLM_API_KEYS", []))


def _db_path() -> Path:
    uri = (config.SQLALCHEMY_DATABASE_URI or "").strip()

    if not uri.startswith("sqlite:"):
        raise LlmKeyPoolError(
            "LLM key pool пока поддерживает только SQLite из SQLALCHEMY_DATABASE_URI."
        )

    raw = uri.split("sqlite:///", 1)[-1]
    raw = raw.split("?", 1)[0]

    if not raw or raw == ":memory:":
        raise LlmKeyPoolError(
            "LLM key pool не работает с этой SQLite URI."
        )

    path = Path(raw)

    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path

    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_key_pool_state (
            key_hash TEXT PRIMARY KEY,
            key_mask TEXT NOT NULL,
            daily_date TEXT NOT NULL,
            daily_requests INTEGER NOT NULL DEFAULT 0,
            disabled_until REAL NOT NULL DEFAULT 0,
            last_error TEXT,
            updated_at REAL NOT NULL
        )
        """
    )
    conn.commit()


def _prepare_schema(conn: sqlite3.Connection) -> None:
    global _schema_ready

    if not _schema_ready:
        _ensure_schema(conn)
        _schema_ready = True


def _hash_key(key: str) -> str:
    return hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:32]


def _mask_key(key: str) -> str:
    key = str(key)

    if len(key) <= 12:
        return "***"

    return f"{key[:6]}...{key[-4:]}"


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _now_ts() -> float:
    return time.time()


def _next_midnight_ts() -> float:
    now = datetime.now(timezone.utc)
    next_day = (now + timedelta(days=1)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return next_day.timestamp()


def _load_state(conn: sqlite3.Connection, key_hash: str, key_mask: str) -> dict:
    today = _today_utc()
    now = _now_ts()

    row = conn.execute(
        "SELECT * FROM llm_key_pool_state WHERE key_hash = ?",
        (key_hash,),
    ).fetchone()

    if row is None:
        conn.execute(
            """
            INSERT INTO llm_key_pool_state (
                key_hash,
                key_mask,
                daily_date,
                daily_requests,
                disabled_until,
                last_error,
                updated_at
            ) VALUES (?, ?, ?, 0, 0, NULL, ?)
            """,
            (key_hash, key_mask, today, now),
        )
        conn.commit()

        return {
            "key_hash": key_hash,
            "daily_date": today,
            "daily_requests": 0,
            "disabled_until": 0.0,
            "last_error": None,
        }

    state = dict(row)

    if state.get("daily_date") != today:
        conn.execute(
            """
            UPDATE llm_key_pool_state
            SET daily_date = ?, daily_requests = 0, updated_at = ?
            WHERE key_hash = ?
            """,
            (today, now, key_hash),
        )
        conn.commit()

        state["daily_date"] = today
        state["daily_requests"] = 0

    return state


def _increment_daily(conn: sqlite3.Connection, key_hash: str, today: str) -> None:
    now = _now_ts()

    cur = conn.execute(
        """
        UPDATE llm_key_pool_state
        SET daily_date = ?, daily_requests = daily_requests + 1, updated_at = ?
        WHERE key_hash = ? AND daily_date = ?
        """,
        (today, now, key_hash, today),
    )

    if cur.rowcount == 0:
        conn.execute(
            """
            UPDATE llm_key_pool_state
            SET daily_date = ?, daily_requests = 1, updated_at = ?
            WHERE key_hash = ?
            """,
            (today, now, key_hash),
        )

    conn.commit()


def _set_disabled(key_hash: str, until_ts: float, error_message: str) -> None:
    conn = _connect()

    try:
        cur = conn.execute(
            """
            UPDATE llm_key_pool_state
            SET disabled_until = ?, last_error = ?, updated_at = ?
            WHERE key_hash = ?
            """,
            (float(until_ts), str(error_message or ""), _now_ts(), key_hash),
        )

        if cur.rowcount == 0:
            conn.execute(
                """
                INSERT INTO llm_key_pool_state (
                    key_hash,
                    key_mask,
                    daily_date,
                    daily_requests,
                    disabled_until,
                    last_error,
                    updated_at
                ) VALUES (?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    key_hash,
                    "***",
                    _today_utc(),
                    float(until_ts),
                    str(error_message or ""),
                    _now_ts(),
                ),
            )

        conn.commit()
    finally:
        conn.close()


def _prune_window(win: dict, now: float) -> None:
    cutoff = now - _WINDOW_SECONDS

    while win["requests"] and win["requests"][0] < cutoff:
        win["requests"].popleft()

    while win["tokens"] and win["tokens"][0][0] < cutoff:
        win["tokens"].popleft()


def _current_limits(win: dict, now: float) -> tuple:
    _prune_window(win, now)

    rpm = len(win["requests"])
    tpm = sum(item[1] for item in win["tokens"]) + win["pending_tokens"]

    return rpm, tpm


def acquire_key(estimated_tokens: int = 0, wait_timeout=None) -> KeyReservation:
    keys = list(getattr(config, "LLM_API_KEYS", []))

    if not keys:
        raise LlmKeyPoolError("LLM_API_KEYS не задан.")

    if wait_timeout is None:
        wait_timeout = getattr(config, "LLM_KEY_WAIT_SECONDS", 120)

    estimated_tokens = max(0, int(estimated_tokens or 0))

    rpm_limit = int(getattr(config, "LLM_RPM_LIMIT", 15) or 0)
    tpm_limit = int(getattr(config, "LLM_TPM_LIMIT", 250000) or 0)
    daily_limit = int(getattr(config, "LLM_DAILY_REQUEST_LIMIT", 500) or 0)

    if rpm_limit <= 0:
        rpm_limit = 10**9

    if tpm_limit <= 0:
        tpm_limit = 10**9

    if daily_limit <= 0:
        daily_limit = 10**9

    deadline = time.monotonic() + max(0.0, float(wait_timeout))

    while True:
        now = time.time()
        sleep_for = 1.0

        has_temporary_block = False
        has_daily_block = False
        has_permanent_block = False

        for key in keys:
            key_hash = _hash_key(key)
            key_mask = _mask_key(key)

            with _lock:
                conn = _connect()
                try:
                    _prepare_schema(conn)
                    state = _load_state(conn, key_hash, key_mask)
                finally:
                    conn.close()

                disabled_until = float(state.get("disabled_until") or 0.0)

                if disabled_until > now:
                    if disabled_until >= _YEAR_3000 - 86400:
                        has_permanent_block = True
                    else:
                        has_daily_block = True
                    continue

                daily_requests = int(state.get("daily_requests") or 0)

                if daily_requests >= daily_limit:
                    until = _next_midnight_ts()
                    _set_disabled(key_hash, until, "Local daily request limit reached")
                    has_daily_block = True
                    continue

                win = _windows[key_hash]
                rpm, tpm = _current_limits(win, now)

                cooldown_until = float(win.get("cooldown_until") or 0.0)

                if cooldown_until > now:
                    has_temporary_block = True
                    sleep_for = min(sleep_for, max(cooldown_until - now, 0.2))
                    continue

                if rpm >= rpm_limit:
                    has_temporary_block = True

                    if win["requests"]:
                        sleep_for = min(
                            sleep_for,
                            max(win["requests"][0] + _WINDOW_SECONDS - now, 0.2),
                        )

                    continue

                if tpm + estimated_tokens > tpm_limit:
                    has_temporary_block = True

                    if win["tokens"]:
                        sleep_for = min(
                            sleep_for,
                            max(win["tokens"][0][0] + _WINDOW_SECONDS - now, 0.2),
                        )
                    else:
                        sleep_for = min(sleep_for, 1.0)

                    continue

                # Резервируем ключ.
                win["requests"].append(now)
                win["pending_tokens"] += estimated_tokens

                conn = _connect()
                try:
                    _increment_daily(conn, key_hash, _today_utc())
                except Exception:
                    if win["requests"] and win["requests"][-1] == now:
                        win["requests"].pop()

                    win["pending_tokens"] = max(
                        0,
                        win["pending_tokens"] - estimated_tokens,
                    )
                    raise
                finally:
                    conn.close()

                return KeyReservation(key, key_hash, estimated_tokens)

        if not has_temporary_block:
            if has_daily_block and not has_permanent_block:
                raise LlmDailyLimitError("Все LLM-ключи исчерпали дневной лимит.")

            raise LlmNoAvailableKeyError(
                "Нет доступного LLM-ключа: ключи отключены или недоступны."
            )

        if time.monotonic() >= deadline:
            raise LlmNoAvailableKeyError(
                "Нет доступного LLM-ключа: все ключи временно ограничены."
            )

        time.sleep(min(max(sleep_for, 0.2), 1.0))


def finish_success(reservation: KeyReservation, tokens: int = 0) -> None:
    if reservation is None:
        return

    with _lock:
        if reservation.finished:
            return

        win = _windows[reservation.key_hash]

        win["pending_tokens"] = max(
            0,
            win["pending_tokens"] - reservation.estimated_tokens,
        )

        tokens = max(0, int(tokens or 0))

        if tokens > 0:
            win["tokens"].append((time.time(), tokens))

        reservation.finished = True


def finish_failure(reservation: KeyReservation) -> None:
    if reservation is None:
        return

    with _lock:
        if reservation.finished:
            return

        win = _windows[reservation.key_hash]

        win["pending_tokens"] = max(
            0,
            win["pending_tokens"] - reservation.estimated_tokens,
        )

        reservation.finished = True


def mark_rate_limit(
    reservation: KeyReservation,
    kind: str,
    retry_after=None,
    message: str = "",
) -> None:
    if reservation is None:
        return

    with _lock:
        if reservation.finished:
            return

        win = _windows[reservation.key_hash]

        win["pending_tokens"] = max(
            0,
            win["pending_tokens"] - reservation.estimated_tokens,
        )

        now = time.time()
        kind = str(kind or "rpm").lower()

        if kind in {"rpm", "tpm", "minute", "minutes", "requests", "tokens"}:
            try:
                cooldown_seconds = float(retry_after or 0)
            except Exception:
                cooldown_seconds = 0.0

            if cooldown_seconds <= 0:
                cooldown_seconds = 60.0

            cooldown_seconds = min(max(cooldown_seconds, 5.0), 3600.0)

            win["cooldown_until"] = max(
                float(win.get("cooldown_until") or 0.0),
                now + cooldown_seconds,
            )

        elif kind == "daily":
            until = _next_midnight_ts()

            try:
                retry_seconds = float(retry_after or 0)
            except Exception:
                retry_seconds = 0.0

            if retry_seconds > 0:
                until = max(until, now + retry_seconds)

            _set_disabled(reservation.key_hash, until, message or "Daily limit")

        elif kind == "auth":
            _set_disabled(reservation.key_hash, _YEAR_3000, message or "Auth error")

        else:
            win["cooldown_until"] = max(
                float(win.get("cooldown_until") or 0.0),
                now + 60.0,
            )

        reservation.finished = True


def mark_auth(reservation: KeyReservation, message: str = "") -> None:
    mark_rate_limit(reservation, "auth", None, message)