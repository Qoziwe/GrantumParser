"""
Тонкий клиент для OpenAI-совместимого API.

Поддерживает провайдеров:
- OpenAI (api.openai.com/v1)
- OpenRouter, Together, Groq
- локальные: Ollama, vLLM, LM Studio (указываешь LLM_BASE_URL)

Если LLM_API_KEY пустой — клиент считается выключенным и вызовы
возвращают LlmDisabledError.
"""

import json
import re
from typing import Optional

import requests

import config


class LlmError(Exception):
    """Базовая ошибка LLM-клиента."""


class LlmDisabledError(LlmError):
    """LLM не настроен (нет API-ключа)."""


class LlmResponseError(LlmError):
    """LLM вернул невалидный ответ."""


class LlmHttpError(LlmError):
    """HTTP-ошибка при обращении к LLM."""


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def is_enabled() -> bool:
    return bool(config.LLM_API_KEY.strip())


def _build_url() -> str:
    base = config.LLM_BASE_URL.rstrip("/")
    return f"{base}/chat/completions"


def _build_headers() -> dict:
    return {
        "Authorization": f"Bearer {config.LLM_API_KEY}",
        "Content-Type": "application/json",
    }


def _strip_markdown_fence(raw: str) -> str:
    """
    Убирает ```json ... ``` обёртку, если LLM её добавил.
    Возвращает исходную строку, если обёртки нет.
    """
    text = raw.strip()
    match = _JSON_FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    # Иногда LLM оборачивает в тройные кавычки без языка
    if text.startswith("```") and text.endswith("```"):
        return text[3:-3].strip()
    return text


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
    - LlmDisabledError: API-ключ не задан;
    - LlmHttpError: сеть/HTTP;
    - LlmResponseError: не удалось распарсить JSON.
    """
    if not is_enabled():
        raise LlmDisabledError(
            "LLM_API_KEY не задан. Analyzer не может проанализировать сайт."
        )

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

    try:
        response = requests.post(
            _build_url(),
            headers=_build_headers(),
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise LlmHttpError(f"Сетевая ошибка при обращении к LLM: {exc}") from exc

    if response.status_code >= 400:
        raise LlmHttpError(
            f"LLM вернул HTTP {response.status_code}: {response.text[:500]}"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise LlmResponseError(
            f"LLM вернул не-JSON тело: {response.text[:300]}"
        ) from exc

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