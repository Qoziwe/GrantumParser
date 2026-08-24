"""
Тонкий клиент для OpenAI-совместимого API.

Поддерживает провайдеров:
- OpenAI (api.openai.com/v1)
- OpenRouter, Together, Groq
- локальные: Ollama, vLLM, LM Studio (указываешь LLM_BASE_URL)

Теперь поддерживает пул API-ключей:
- LLM_API_KEYS=sk-1,sk-2,sk-3
- fallback на старый LLM_API_KEY
- ротацию ключей при дневных, минутных и auth-лимитах
"""

import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import requests

import config
import llm_key_pool as key_pool


class LlmError(Exception):
    """Базовая ошибка LLM-клиента."""


class LlmDisabledError(LlmError):
    """LLM не настроен (нет API-ключа)."""


class LlmResponseError(LlmError):
    """LLM вернул невалидный ответ."""


class LlmHttpError(LlmError):
    """HTTP-ошибка при обращении к LLM."""


class LlmRateLimitError(LlmHttpError):
    """Rate limit от LLM-провайдера."""


class LlmRpmLimitError(LlmRateLimitError):
    """Лимит запросов в минуту."""


class LlmTpmLimitError(LlmRateLimitError):
    """Лимит токенов в минуту."""


class LlmDailyLimitError(LlmRateLimitError):
    """Дневной лимит или quota."""


class LlmAuthError(LlmHttpError):
    """Ключ отклонён: auth, billing, invalid key."""


class LlmNoAvailableKeyError(LlmError):
    """Ни один LLM-ключ сейчас недоступен."""


_JSON_FENCE_RE = re.compile(r"`(?:json)?\s*(\{.*?\})\s*`", re.DOTALL)


def is_enabled() -> bool:
    return key_pool.has_keys()


def _build_url() -> str:
    base = config.LLM_BASE_URL.rstrip("/")
    return f"{base}/chat/completions"


def _build_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _strip_markdown_fence(raw: str) -> str:
    """
    Убирает ```json ...``` обёртку, если LLM её добавил.
    Возвращает исходную строку, если обёртки нет.
    """
    text = raw.strip()

    match = _JSON_FENCE_RE.search(text)
    if match:
        return match.group(1).strip()

    # Иногда LLM оборачивает в тройные кавычки без языка.
    if text.startswith("```") and text.endswith("```"):
        return text[3:-3].strip()

    return text


def _estimate_tokens(system_prompt: str, user_prompt: str, max_tokens: Optional[int]) -> int:
    try:
        text_len = len(system_prompt or "") + len(user_prompt or "")
        estimated = text_len // 4 + max(0, int(max_tokens or 0)) + 50
    except Exception:
        estimated = 1000

    tpm_limit = getattr(config, "LLM_TPM_LIMIT", 250000)

    if tpm_limit and tpm_limit > 0:
        estimated = min(estimated, tpm_limit)

    return max(1, estimated)


def _parse_retry_after(response: requests.Response) -> Optional[int]:
    value = response.headers.get("Retry-After")

    if not value:
        return None

    value = value.strip()

    if value.isdigit():
        return max(0, int(value))

    try:
        dt = parsedate_to_datetime(value)

        if dt is None:
            return None

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        delta = (dt - datetime.now(timezone.utc)).total_seconds()
        return max(0, int(delta))
    except Exception:
        return None


def _classify_rate_limit(response: requests.Response) -> tuple:
    """
    Возвращает (kind, retry_after_seconds).

    kind:
    - rpm
    - tpm
    - daily
    """
    retry_after = _parse_retry_after(response)

    try:
        lower = (response.text or "").lower()
    except Exception:
        lower = ""

    try:
        body = response.json()

        if isinstance(body, dict):
            error = body.get("error")

            if isinstance(error, dict):
                lower += " " + str(error.get("message", "")).lower()
                lower += " " + str(error.get("code", "")).lower()
            elif error is not None:
                lower += " " + str(error).lower()
    except Exception:
        pass

    token_markers = (
        "token",
        "tokens",
        "tpm",
        "tokens per minute",
    )

    request_markers = (
        "request",
        "requests",
        "rpm",
        "requests per minute",
    )

    minute_markers = (
        "minute",
        "min",
        "per m",
        "rpm",
        "tpm",
    )

    daily_markers = (
        "daily",
        "per day",
        "day limit",
        "quota",
        "insufficient",
        "billing",
        "free tier",
        "monthly",
    )

    # Сначала явно распознаём минутные лимиты.
    if any(marker in lower for marker in token_markers) and any(marker in lower for marker in minute_markers):
        return "tpm", retry_after or 60

    if any(marker in lower for marker in request_markers) and any(marker in lower for marker in minute_markers):
        return "rpm", retry_after or 60

    # Потом дневные/quota/billing.
    if any(marker in lower for marker in daily_markers):
        return "daily", retry_after

    # Если Retry-After большой, скорее всего это не минутный лимит.
    if retry_after is not None and retry_after > 300:
        return "daily", retry_after

    # Generic 429 считаем RPM, чтобы поставить короткий cooldown.
    return "rpm", retry_after or 60


def _extract_usage_tokens(data: dict, estimated_tokens: int) -> int:
    try:
        usage = data.get("usage") if isinstance(data, dict) else None

        if isinstance(usage, dict):
            total = usage.get("total_tokens")

            if total is None:
                prompt = int(usage.get("prompt_tokens") or 0)
                completion = int(usage.get("completion_tokens") or 0)
                total = prompt + completion

            total = int(total or 0)

            if total > 0:
                return total
    except Exception:
        pass

    return max(0, int(estimated_tokens or 0))


def chat_json(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0.2,
    max_tokens: Optional[int] = 4000,
    timeout: int = 120,
) -> dict:
    """
    Вызов LLM с ожиданием JSON-ответа.

    Возвращает распарсенный dict.

    Исключения:
    - LlmDisabledError: API-ключи не заданы;
    - LlmNoAvailableKeyError: нет доступного ключа;
    - LlmDailyLimitError: дневной лимит/quota;
    - LlmRpmLimitError: лимит запросов в минуту;
    - LlmTpmLimitError: лимит токенов в минуту;
    - LlmAuthError: ключ отклонён;
    - LlmHttpError: сеть/HTTP;
    - LlmResponseError: не удалось распарсить JSON.
    """
    if not is_enabled():
        raise LlmDisabledError(
            "LLM_API_KEYS/LLM_API_KEY не задан. Analyzer не может проанализировать сайт."
        )

    estimated_tokens = _estimate_tokens(system_prompt, user_prompt, max_tokens)

    payload = {
        "model": config.LLM_MODEL,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
    }

    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    keys_count = len(getattr(config, "LLM_API_KEYS", []))
    max_attempts = max(3, keys_count * 2)
    wait_timeout = getattr(config, "LLM_KEY_WAIT_SECONDS", 120)

    last_error = None

    for _attempt in range(max_attempts):
        try:
            reservation = key_pool.acquire_key(
                estimated_tokens=estimated_tokens,
                wait_timeout=wait_timeout,
            )
        except key_pool.LlmDailyLimitError as exc:
            raise LlmDailyLimitError(str(exc)) from exc
        except key_pool.LlmKeyPoolError as exc:
            if last_error is not None:
                raise last_error from exc
            raise LlmNoAvailableKeyError(str(exc)) from exc

        try:
            try:
                response = requests.post(
                    _build_url(),
                    headers=_build_headers(reservation.key),
                    json=payload,
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                key_pool.finish_failure(reservation)
                last_error = LlmHttpError(f"Сетевая ошибка при обращении к LLM: {exc}")
                continue

            if response.status_code in (401, 403, 402):
                key_pool.mark_auth(reservation, response.text[:500])
                last_error = LlmAuthError(
                    f"LLM ключ отклонён: HTTP {response.status_code}: {response.text[:300]}"
                )
                continue

            if response.status_code == 429:
                kind, retry_after = _classify_rate_limit(response)

                key_pool.mark_rate_limit(
                    reservation,
                    kind,
                    retry_after,
                    response.text[:500],
                )

                if kind == "daily":
                    last_error = LlmDailyLimitError(
                        f"LLM вернул HTTP 429 (дневной лимит/quota): {response.text[:300]}"
                    )
                elif kind == "tpm":
                    last_error = LlmTpmLimitError(
                        f"LLM вернул HTTP 429 (лимит токенов в минуту): {response.text[:300]}"
                    )
                else:
                    last_error = LlmRpmLimitError(
                        f"LLM вернул HTTP 429 (лимит запросов в минуту): {response.text[:300]}"
                    )

                continue

            if response.status_code >= 400:
                key_pool.finish_failure(reservation)
                error_text = response.text[:500]

                # 5xx можно пробовать на другом ключе.
                if 500 <= response.status_code < 600:
                    last_error = LlmHttpError(
                        f"LLM вернул HTTP {response.status_code}: {error_text}"
                    )
                    continue

                raise LlmHttpError(
                    f"LLM вернул HTTP {response.status_code}: {error_text}"
                )

            try:
                data = response.json()
            except ValueError as exc:
                key_pool.finish_failure(reservation)
                raise LlmResponseError(
                    f"LLM вернул не-JSON тело: {response.text[:300]}"
                ) from exc

            usage_tokens = _extract_usage_tokens(data, estimated_tokens)
            key_pool.finish_success(reservation, usage_tokens)

            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise LlmResponseError(
                    f"Неожиданная структура ответа LLM: {str(data)[:300]}"
                ) from exc

            cleaned = _strip_markdown_fence(content)

            try:
                return json.loads(cleaned)
            except json.JSONDecodeError as exc:
                raise LlmResponseError(
                    f"Не удалось распарсить JSON от LLM: {exc}. "
                    f"Сырой ответ: {cleaned[:500]}"
                ) from exc

        finally:
            if not reservation.finished:
                key_pool.finish_failure(reservation)

    if last_error is not None:
        raise last_error

    raise LlmError("Не удалось выполнить запрос к LLM.")