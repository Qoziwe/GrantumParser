"""
Analyzer — модуль анализа страницы и создания SiteProfile.

Поток:
1. Подключение к CDP-Chrome в отдельной вкладке.
2. Переход на целевой URL.
3. Сетевое наблюдение: сбор XHR/fetch запросов того же домена.
4. Короткие прокрутки для обнаружения ленивой пагинации.
5. Сбор очищенного DOM + эвристика повторяющихся блоков.
6. Вызов LLM через llm_client.
7. Валидация JSON-схемы.
8. Smoke-test селекторов на живой странице.
9. Сохранение SiteProfile в БД.

Сериализация: анализ одного (domain, path_prefix) выполняется под lock,
чтобы две параллельные задачи не запускали два LLM-вызова.
"""

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional
from urllib.parse import urlsplit

from playwright.sync_api import Page

import config
import llm_client
from models import db, SiteProfile, ChildProfile
from url_utils import NormalizedUrl, normalize_path_prefix


# Глобальный словарь блокировок по (domain, path_prefix).
# Защищает от параллельных LLM-вызовов на один домен.
_domain_locks: dict = {}
_domain_locks_lock = threading.Lock()


# Маркеры, которые LLM НЕ должен использовать как стратегию пагинации в v1.
_RESERVED_STRATEGIES = {"api_json"}

# Допустимые стратегии пагинации v1 (согласно спецификации раздел 8).
_ALLOWED_STRATEGIES = {
    "none",
    "query_param_page",
    "next_button",
    "load_more",
    "infinite_scroll",
    "html_fragment_url",
}

# Теги, которые вырезаются из DOM перед отправкой LLM.
_STRIP_TAGS = {"script", "style", "svg", "noscript", "iframe", "path", "meta", "link"}

# Атрибуты, которые вырезаются из DOM для снижения шума.
_STRIP_ATTRS = {
    "style", "onclick", "onmouseover", "onload", "onerror",
    "data-reactid", "data-ember-action",
}

# Атрибуты, которые обязательно сохраняются — они часто используются
# в селекторах и data-* паттернах.
_KEEP_ATTR_PREFIXES = ("data-", "aria-", "id", "class", "role", "href", "src")


class AnalyzerError(Exception):
    """Базовая ошибка анализатора."""


class NoProfileCreatedError(AnalyzerError):
    """LLM не смог создать валидную инструкцию."""


@dataclass
class NetworkObservation:
    url: str
    method: str
    resource_type: str
    status: Optional[int] = None


@dataclass
class CandidateBlock:
    """Повторяющийся блок DOM, найденный эвристикой."""
    signature: str
    count: int
    example_html: str


@dataclass
class PageSignals:
    """Собранные сигналы страницы для передачи в LLM."""
    final_url: str
    title: str
    cleaned_dom: str
    dom_truncated: bool
    network_requests: List[NetworkObservation] = field(default_factory=list)
    candidate_blocks: List[CandidateBlock] = field(default_factory=list)


def _get_domain_lock(domain: str, path_prefix: str) -> threading.Lock:
    """Возвращает lock для (domain, path_prefix), создавая при необходимости."""
    key = (domain, normalize_path_prefix(path_prefix))
    with _domain_locks_lock:
        lock = _domain_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _domain_locks[key] = lock
        return lock


def _clean_element_html(html: str) -> str:
    """
    Базовая очистка HTML-фрагмента: убираем шумные атрибуты.
    Полноценный парсер HTML не используем, чтобы не тянуть bs4/lxml.
    """
    # Убираем атрибуты вида style="..." onclick="..." и т.п.
    import re
    for attr in _STRIP_ATTRS:
        html = re.sub(rf'\s+{re.escape(attr)}\s*=\s*"[^"]*"', "", html, flags=re.IGNORECASE)
        html = re.sub(rf"\s+{re.escape(attr)}\s*=\s*'[^']*'", "", html, flags=re.IGNORECASE)
    # Схлопываем пробелы
    html = re.sub(r"\s+", " ", html)
    return html.strip()


def _extract_candidate_blocks(page: Page, max_blocks: int = 5) -> List[CandidateBlock]:
    """
    Эвристика повторяющихся блоков.

    Группирует элементы DOM по сигнатуре (tagName + основные классы)
    и возвращает топ-N сигнатур с количеством повторений и примером.

    Это снижает галлюцинации LLM при выборе card_selector.
    """
    try:
        return page.evaluate(r"""() => {
            const signatureOf = (el) => {
                const tag = el.tagName.toLowerCase();
                const classes = Array.from(el.classList || [])
                    .filter(c => c.length > 1 && !/^[\d\-_]+$/.test(c))
                    .slice(0, 3)
                    .sort()
                    .join('.');
                return classes ? `${tag}.${classes}` : tag;
            };

            const counts = {};
            const examples = {};
            const all = document.querySelectorAll('body *');

            for (const el of all) {
                if (!el.children || el.children.length === 0) continue;
                const sig = signatureOf(el);
                counts[sig] = (counts[sig] || 0) + 1;
                if (!(sig in examples) && el.outerHTML.length < 2000) {
                    examples[sig] = el.outerHTML.slice(0, 800);
                }
            }

            // Топ-N по количеству повторений, минимум 3 повтора
            return Object.entries(counts)
                .filter(([_, count]) => count >= 3)
                .sort((a, b) => b[1] - a[1])
                .slice(0, 5)
                .map(([sig, count]) => ({
                    signature: sig,
                    count,
                    example_html: examples[sig] || ''
                }));
        }""")
    except Exception:
        return []


def _capture_cleaned_dom(page: Page) -> tuple:
    """
    Возвращает (cleaned_dom, was_truncated).

    Очищает DOM: удаляет script/style/svg/..., схлопывает пробелы,
    обрезает до ANALYZER_MAX_DOM_CHARS.
    """
    try:
        raw_dom = page.evaluate(r"""() => {
            const clone = document.documentElement.cloneNode(true);
            // Удаляем шумные теги
            const stripTags = new Set(%s);
            const walk = (node) => {
                const toRemove = [];
                for (const child of node.childNodes) {
                    if (child.nodeType === 1) {
                        if (stripTags.has(child.tagName.toLowerCase())) {
                            toRemove.push(child);
                        } else {
                            walk(child);
                        }
                    }
                }
                for (const r of toRemove) r.remove();
            };
            walk(clone);
            return clone.outerHTML;
        }""" % json.dumps(list(_STRIP_TAGS)))
    except Exception as exc:
        raise AnalyzerError(f"Не удалось получить DOM: {exc}") from exc

    cleaned = _clean_element_html(raw_dom)
    limit = config.ANALYZER_MAX_DOM_CHARS

    if len(cleaned) <= limit:
        return cleaned, False

    # Умное усечение: первые 70%, потом [...усечено...], потом последние 20%
    head_size = int(limit * 0.7)
    tail_size = int(limit * 0.2)
    marker = "\n\n[...DOM усечён, пропущено {} символов...]\n\n".format(
        len(cleaned) - head_size - tail_size
    )
    truncated = cleaned[:head_size] + marker + cleaned[-tail_size:]
    return truncated, True


def _collect_page_signals(
    page: Page,
    target_url: str,
    job_id: int,
    *,
    navigate: bool = True,
) -> PageSignals:
    """
    Загружает страницу либо анализирует уже открытую страницу.
    """
    from models import add_log, LogLevel

    target_host = urlsplit(target_url).hostname or ""

    captured_requests: List[NetworkObservation] = []
    request_lock = threading.Lock()

    def on_response(response):
        try:
            req = response.request
            req_url = req.url
            req_host = urlsplit(req_url).hostname or ""
            resource_type = (req.resource_type or "").lower()

            # Нас интересуют только XHR/fetch/document запросы того же домена
            if resource_type not in {"xhr", "fetch", "document"}:
                return
            if req_host != target_host:
                return

            with request_lock:
                captured_requests.append(NetworkObservation(
                    url=req_url,
                    method=req.method,
                    resource_type=resource_type,
                    status=response.status,
                ))
        except Exception:
            pass

    page.on("response", on_response)

    try:
        if navigate:
            add_log(job_id, "INFO", f"Analyzer: загружаю {target_url}")
            try:
                page.goto(target_url, timeout=60000, wait_until="domcontentloaded")
            except Exception as exc:
                raise AnalyzerError(f"Не удалось загрузить целевую страницу: {exc}") from exc

            time.sleep(2.0)

            # Короткие прокрутки для обнаружения ленивой пагинации
            for _ in range(3):
                try:
                    page.evaluate("window.scrollBy(0, window.innerHeight)")
                except Exception:
                    pass
                time.sleep(1.2)

        # Собираем сигналы
        final_url = page.url
        try:
            title = page.title() or ""
        except Exception:
            title = ""

        cleaned_dom, dom_truncated = _capture_cleaned_dom(page)
        if dom_truncated:
            add_log(
                job_id, "WARNING",
                f"Analyzer: DOM превысил лимит {config.ANALYZER_MAX_DOM_CHARS} "
                f"символов и был усечён."
            )

        raw_blocks = _extract_candidate_blocks(page)
        candidate_blocks = [
            CandidateBlock(
                signature=b.get("signature", ""),
                count=int(b.get("count", 0)),
                example_html=b.get("example_html", ""),
            )
            for b in raw_blocks
            if b.get("signature")
        ]

        return PageSignals(
            final_url=final_url,
            title=title,
            cleaned_dom=cleaned_dom,
            dom_truncated=dom_truncated,
            network_requests=list(captured_requests),
            candidate_blocks=candidate_blocks,
        )
    finally:
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass


def _build_llm_prompts(
    normalized: NormalizedUrl,
    signals: PageSignals,
) -> tuple:
    """Собирает системный и пользовательский промпты для LLM."""

    system_prompt = (
        "Ты — анализатор веб-страниц для системы автоматического парсинга. "
        "Твоя задача — по очищенному DOM и наблюдениям за сетевыми запросами "
        "составить JSON-инструкцию для детерминированного исполнителя.\n\n"
        "ТРЕБОВАНИЯ:\n"
        "1. Ответ СТРОГО в формате JSON без markdown-обёрток (без ```json).\n"
        "2. Используй ТОЛЬКО валидные CSS-селекторы.\n"
        "3. Предпочитай стабильные селекторы: id, data-*, семантические классы, "
        "aria-*, role. Избегай позиционных (nth-child) и хрупких классов "
        "(типа sc-abc123 от CSS-in-JS).\n"
        "4. Стратегию пагинации выбирай ТОЛЬКО из списка: "
        "none, query_param_page, next_button, load_more, infinite_scroll, "
        "html_fragment_url. Стратегия api_json ЗАПРЕЩЕНА.\n"
        "5. Выбирай стратегию пагинации ТОЛЬКО на основании доказательств: "
        "DOM (кнопки .next, .load-more) или сетевые запросы "
        "(повторяющиеся XHR с параметром page).\n"
        "6. Заполни validation и stop_conditions разумными значениями.\n"
        "7. В notes кратко опиши, что ты заметил на странице.\n\n"
        "=== ТОЧНАЯ СХЕМА JSON (schema_version=1) ===\n"
        "Верни РОВНО такую структуру. Ключи и типы — строго как указано.\n"
        "{"
        "    \"schema_version\": 1,\n"
        "    \"domain\": \"<заполняется из Домен выше, не выдумывай>\",\n"
        "    \"path_prefix\": \"<заполняется из Путь выше, не выдумывай>\",\n"
        "    \"card_selector\": \"<CSS-селектор одного повторяющегося элемента-карточки, строка>\",\n"
        "    \"fields\": {\n"
        "        \"title_selector\": \"<CSS-селектор заголовка внутри карточки>\",\n"
        "        \"link_selector\": \"<CSS-селектор ссылки внутри карточки, или пустая строка если сама карточка является ссылкой>\",\n"
        "        \"text_selectors\": [\"<CSS-селекторы дополнительного текста>\"],\n"
        "        \"text_fallback\": \"card_inner_text\"\n"
        "    },\n"
        "    \"detail\": {\n"
        "        \"enabled\": false,\n"
        "        \"expand_button_selector\": \"\",\n"
        "        \"full_text_selector\": \"\",\n"
        "        \"fallback_selectors\": []\n"
        "    },\n"
        "    \"pagination\": {\n"
        "        \"strategy\": \"<одна из: none, query_param_page, next_button, load_more, infinite_scroll, html_fragment_url>\",\n"
        "        \"selector\": \"<обязателен для next_button/load_more>\",\n"
        "        \"page_param\": \"<для query_param_page>\",\n"
        "        \"first_page_is_target\": true\n"
        "    },\n"
        "    \"stop_conditions\": {\n"
        "        \"max_pages\": <int>,\n"
        "        \"max_cards\": <int>,\n"
        "        \"stop_on_empty_page\": true,\n"
        "        \"stop_on_no_new_cards\": true\n"
        "    },\n"
        "    \"validation\": {\n"
        "        \"min_cards_first_page\": <int, минимум 1>,\n"
        "        \"max_empty_title_ratio\": 0.2,\n"
        "        \"max_empty_url_ratio\": 0.2,\n"
        "        \"min_text_length\": 0\n"
        "    },\n"
        "    \"auth_markers\": [\"sign in\", \"log in\"],\n"
        "    \"notes\": \"<короткое описание>\",\n"
        "    \"generator_model\": \"<не трогай>\"\n"
        "}\n"
        "ВАЖНО:\n"
        "8. Поле называется card_selector (НЕ list_selector).\n"
        "9. fields — это ОБЪЕКТ с ключами title_selector, link_selector, "
        "text_selectors, text_fallback (НЕ массив).\n"
        "10. В pagination ОБЯЗАТЕЛЬНО ключ strategy (НЕ type, НЕ next_page_selector).\n"
        "11. В validation НЕ заменяй ключи на min_items/required_fields — "
        "используй min_cards_first_page, max_empty_title_ratio, max_empty_url_ratio.\n"
        "12. domain и path_prefix бери ТОЛЬКО из данных ниже, не сочиняй.\n"
    )

    # Формируем описание сетевых запросов
    network_summary_lines = []
    # Дедупликация по пути, с сохранением параметров
    seen_paths = set()
    for req in signals.network_requests:
        try:
            parts = urlsplit(req.url)
            key = (parts.path, parts.query)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            network_summary_lines.append(
                f"- {req.method} {req.resource_type} {req.status} "
                f"{parts.path}?{parts.query}" if parts.query else
                f"- {req.method} {req.resource_type} {req.status} {parts.path}"
            )
        except Exception:
            continue
    network_summary = (
        "\n".join(network_summary_lines[:80])
        if network_summary_lines
        else "(сетевых запросов того же домена не замечено)"
    )

    # Формируем описание повторяющихся блоков
    blocks_lines = []
    for b in signals.candidate_blocks:
        blocks_lines.append(
            f"- сигнатура: `{b.signature}`, повторов: {b.count}\n"
            f"  пример: {b.example_html[:400]}"
        )
    blocks_summary = (
        "\n".join(blocks_lines)
        if blocks_lines
        else "(повторяющихся блоков не обнаружено)"
    )

    user_prompt = (
        f"Целевой URL: {normalized.original}\n"
        f"Домен: {normalized.domain}\n"
        f"Путь: {normalized.path}\n"
        f"Финальный URL после загрузки: {signals.final_url}\n"
        f"Title: {signals.title}\n\n"
        f"=== СЕТЕВЫЕ ЗАПРОСЫ ТОГО ЖЕ ДОМЕНА (XHR/fetch/document) ===\n"
        f"{network_summary}\n\n"
        f"=== КАНДИДАТНЫЕ ПОВТОРЯЮЩИЕСЯ БЛОКИ DOM ===\n"
        f"{blocks_summary}\n\n"
        f"=== ОЧИЩЕННЫЙ DOM (усечён до {config.ANALYZER_MAX_DOM_CHARS} символов) ===\n"
        f"{signals.cleaned_dom}\n\n"
        f"Верни JSON-инструкцию по схеме (schema_version=1). "
        f"domain и path_prefix уже известны: "
        f"domain=\"{normalized.domain}\", "
        f"path_prefix=\"{normalize_path_prefix(normalized.path)}\"."
    )

    return system_prompt, user_prompt


def _detail_system_prompt() -> str:
    return """Ты анализируешь detail-страницу, а не список карточек.
Верни только JSON по схеме:
{
  "schema_version": 1,
  "domain": "строка",
  "path_prefix": "строка",
  "detail": {
    "title_selector": "CSS-селектор или пустая строка",
    "text_selectors": ["CSS-селекторы основного содержимого"],
    "fallback_selectors": ["CSS-селекторы запасного содержимого"],
    "date_selector": "CSS-селектор или пустая строка",
    "author_selector": "CSS-селектор или пустая строка"
  }
}
Не добавляй card_selector, fields или pagination. Селекторы должны быть CSS-селекторами,
которые существуют в переданном DOM. Основной текст выбирай максимально содержательным,
но не включай навигацию, рекламу и комментарии."""


def _validate_detail_instruction(instruction: dict, normalized: NormalizedUrl) -> None:
    if not isinstance(instruction, dict) or instruction.get("schema_version") != 1:
        raise AnalyzerError("Detail-инструкция должна иметь schema_version=1.")
    if instruction.get("domain") != normalized.domain:
        raise AnalyzerError("Домен detail-инструкции не совпадает с URL.")
    detail = instruction.get("detail")
    if not isinstance(detail, dict):
        raise AnalyzerError("Поле detail должно быть объектом.")
    if not isinstance(detail.get("title_selector"), str):
        raise AnalyzerError("detail.title_selector должен быть строкой.")
    for key in ("text_selectors", "fallback_selectors"):
        if not isinstance(detail.get(key), list) or not all(isinstance(v, str) for v in detail[key]):
            raise AnalyzerError(f"detail.{key} должен быть массивом строк.")
    for key in ("title_selector", "date_selector", "author_selector"):
        selector = detail.get(key, "")
        if selector:
            try:
                # Синтаксис проверяется на странице в вызывающем коде.
                normalized_selector = str(selector)
            except Exception as exc:
                raise AnalyzerError(f"Некорректный detail.{key}: {exc}") from exc


def _analyze_detail_page(page: Page, normalized: NormalizedUrl, job_id: int) -> dict:
    from models import add_log

    signals = _collect_page_signals(
        page,
        normalized.original,
        job_id,
        navigate=False,
    )
    system_prompt = _detail_system_prompt()
    user_prompt = (
        f"URL: {normalized.original}\nДомен: {normalized.domain}\n"
        f"Путь: {normalized.path}\nTitle: {signals.title}\n"
        f"DOM:\n{signals.cleaned_dom}"
    )
    add_log(job_id, "INFO", f"Analyzer: вызываю LLM для detail {normalized.path}...")
    instruction = llm_client.chat_json(system_prompt, user_prompt)
    _validate_detail_instruction(instruction, normalized)
    detail = instruction["detail"]
    selectors = [detail.get("title_selector", ""), *detail.get("text_selectors", []), *detail.get("fallback_selectors", [])]
    for selector in selectors:
        if selector:
            try:
                page.query_selector(selector)
            except Exception as exc:
                raise AnalyzerError(f"Невалидный CSS detail-селектор {selector!r}: {exc}") from exc
    instruction["domain"] = normalized.domain
    path_prefix = normalize_path_prefix(normalized.path)
    if path_prefix != "/":
        path_prefix = path_prefix.rsplit("/", 1)[0] or "/"
    instruction["path_prefix"] = path_prefix
    instruction["generated_at"] = datetime.utcnow().isoformat()
    instruction.setdefault("generator_model", config.LLM_MODEL)
    return instruction


def analyze_and_create_child_profile(
    app, normalized: NormalizedUrl, parent_profile_id: int, job_id: int, page: Page
) -> ChildProfile:
    path_prefix = normalize_path_prefix(normalized.path)
    if path_prefix != "/":
        path_prefix = path_prefix.rsplit("/", 1)[0] or "/"
    lock = _get_domain_lock(f"child:{parent_profile_id}:{normalized.domain}", path_prefix)
    acquired = lock.acquire(timeout=180)
    try:
        with app.app_context():
            existing = ChildProfile.query.filter_by(
                parent_profile_id=parent_profile_id,
                domain=normalized.domain,
                path_prefix=path_prefix,
                is_active=True,
            ).first()
            if existing:
                return existing
            if not acquired:
                raise AnalyzerError("Таймаут ожидания detail-анализа.")
            instruction = _analyze_detail_page(page, normalized, job_id)
            profile = ChildProfile(
                parent_profile_id=parent_profile_id,
                domain=normalized.domain,
                path_prefix=path_prefix,
                instructions_json=json.dumps(instruction, ensure_ascii=False),
            )
            db.session.add(profile)
            db.session.commit()
            return db.session.get(ChildProfile, profile.id)
    finally:
        if acquired:
            lock.release()


def _validate_instruction(instruction: dict, normalized: NormalizedUrl) -> None:
    """
    Валидирует JSON-схему инструкции.

    Выбрасывает AnalyzerError при нарушении контракта.
    """
    if not isinstance(instruction, dict):
        raise AnalyzerError("Инструкция — не объект JSON.")

    schema_version = instruction.get("schema_version")
    if schema_version != 1:
        raise AnalyzerError(
            f"Неподдерживаемая schema_version: {schema_version}. Ожидалась 1."
        )

    domain = instruction.get("domain")
    if domain != normalized.domain:
        raise AnalyzerError(
            f"domain в инструкции ({domain!r}) не совпадает "
            f"с нормализованным ({normalized.domain!r})."
        )

    card_selector = instruction.get("card_selector")
    if not isinstance(card_selector, str) or not card_selector.strip():
        raise AnalyzerError("card_selector обязателен и должен быть непустой строкой.")

    fields = instruction.get("fields")
    if not isinstance(fields, dict):
        raise AnalyzerError("Поле fields обязательно и должно быть объектом.")

    for required_key in ("title_selector", "link_selector"):
        val = fields.get(required_key)
        if not isinstance(val, str):
            raise AnalyzerError(
                f"fields.{required_key} обязателен и должен быть строкой."
            )
        if required_key == "title_selector" and not val.strip():
            raise AnalyzerError(
                f"fields.title_selector обязателен и должен быть непустой строкой."
            )

    pagination = instruction.get("pagination")
    if not isinstance(pagination, dict):
        raise AnalyzerError("Поле pagination обязательно и должно быть объектом.")

    strategy = pagination.get("strategy")
    if strategy in _RESERVED_STRATEGIES:
        raise AnalyzerError(
            f"Стратегия {strategy!r} зарезервирована и запрещена в v1."
        )
    if strategy not in _ALLOWED_STRATEGIES:
        raise AnalyzerError(
            f"Неизвестная стратегия пагинации: {strategy!r}. "
            f"Допустимы: {sorted(_ALLOWED_STRATEGIES)}."
        )

    stop_conditions = instruction.get("stop_conditions")
    if not isinstance(stop_conditions, dict):
        raise AnalyzerError("Поле stop_conditions обязательно и должно быть объектом.")


def _smoke_test(page: Page, instruction: dict, job_id: int) -> None:
    """
    Проверяет селекторы инструкции на живой странице ДО сохранения профиля.

    Выбрасывает AnalyzerError при провале.
    """
    from models import add_log

    card_selector = instruction["card_selector"]
    fields = instruction["fields"]
    title_selector = fields["title_selector"]
    link_selector = fields["link_selector"]

    # 1. Проверка синтаксиса всех селекторов + поиск карточек
    try:
        cards = page.query_selector_all(card_selector)
    except Exception as exc:
        raise AnalyzerError(
            f"Невалидный CSS в card_selector {card_selector!r}: {exc}"
        ) from exc

    validation = instruction.get("validation", {})
    min_cards = int(validation.get("min_cards_first_page", 1))

    if len(cards) < min_cards:
        raise AnalyzerError(
            f"Smoke-test: найдено {len(cards)} карточек по "
            f"card_selector={card_selector!r}, ожидалось минимум {min_cards}."
        )

    add_log(
        job_id, "INFO",
        f"Analyzer smoke-test: {len(cards)} карточек найдено."
    )

    # 2. Проверка полей на первых карточках
    check_count = min(5, len(cards))
    empty_titles = 0
    empty_links = 0

    for i in range(check_count):
        card = cards[i]
        try:
            title_node = card.query_selector(title_selector)
            if title_node is None:
                empty_titles += 1
            else:
                t = (title_node.inner_text() or "").strip()
                if not t:
                    empty_titles += 1
        except Exception as exc:
            raise AnalyzerError(
                f"Невалидный CSS в title_selector {title_selector!r}: {exc}"
            ) from exc

        try:
            if not link_selector:
                link_node = card
            else:
                link_node = card.query_selector(link_selector)
            
            if link_node is None and card.get_attribute("href"):
                link_node = card

            if link_node is None:
                empty_links += 1
            else:
                href = link_node.get_attribute("href")
                if not href:
                    empty_links += 1
        except Exception as exc:
            raise AnalyzerError(
                f"Невалидный CSS в link_selector {link_selector!r}: {exc}"
            ) from exc

    max_empty_title_ratio = float(validation.get("max_empty_title_ratio", 0.2))
    max_empty_url_ratio = float(validation.get("max_empty_url_ratio", 0.2))

    if check_count > 0:
        title_ratio = empty_titles / check_count
        url_ratio = empty_links / check_count

        if title_ratio > max_empty_title_ratio:
            raise AnalyzerError(
                f"Smoke-test: {empty_titles}/{check_count} "
                f"({title_ratio:.0%}) карточек без заголовка. "
                f"Допустимо не более {max_empty_title_ratio:.0%}."
            )
        if url_ratio > max_empty_url_ratio:
            raise AnalyzerError(
                f"Smoke-test: {empty_links}/{check_count} "
                f"({url_ratio:.0%}) карточек без ссылки. "
                f"Допустимо не более {max_empty_url_ratio:.0%}."
            )

    # 3. Проверка селекторов detail, если включено
    detail = instruction.get("detail", {})
    if detail.get("enabled"):
        for key in ("expand_button_selector", "full_text_selector"):
            sel = detail.get(key)
            if not sel:
                continue
            try:
                page.query_selector(sel)
            except Exception as exc:
                raise AnalyzerError(
                    f"Невалидный CSS в detail.{key} {sel!r}: {exc}"
                ) from exc

    add_log(job_id, "INFO", "Analyzer smoke-test: пройден.")

def _analyze_page_and_build_instruction(
    app,
    normalized: NormalizedUrl,
    job_id: int,
) -> dict:
    """
    Ядро анализа: подключается к CDP, собирает сигналы страницы,
    вызывает LLM, валидирует и smoke-test.
    
    Возвращает готовый dict инструкции БЕЗ сохранения в БД.
    Выбрасывает AnalyzerError при неудаче.
    
    Используется как analyze_and_create_profile (создание новой строки),
    так и regenerate_profile (in-place обновление существующей строки).
    """
    from models import add_log

    add_log(
        job_id, "INFO",
        f"Analyzer: запускаю анализ {normalized.domain}{normalized.path}."
    )

    if not llm_client.is_enabled():
        raise NoProfileCreatedError(
            "LLM_API_KEY не задан в окружении. "
            "Настрой .env, чтобы парсить новые сайты."
        )

    # Подключаемся к CDP в отдельной вкладке
    from playwright.sync_api import sync_playwright

    page = None
    browser = None
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.connect_over_cdp(config.CDP_URL)
            except Exception as exc:
                raise AnalyzerError(
                    f"Analyzer: не удалось подключиться к CDP "
                    f"({config.CDP_URL}): {exc}"
                ) from exc

            if not browser.contexts:
                raise AnalyzerError(
                    "Analyzer: Chrome подключён, но нет контекстов. "
                    "Открой любую вкладку в Chrome."
                )

            context = browser.contexts[0]
            page = context.new_page()

            # 1. Сбор сигналов страницы
            signals = _collect_page_signals(
                page, normalized.original, job_id
            )

            # 2. Построение промптов и вызов LLM
            system_prompt, user_prompt = _build_llm_prompts(
                normalized, signals
            )

            add_log(job_id, "INFO", "Analyzer: вызываю LLM...")
            try:
                instruction = llm_client.chat_json(
                    system_prompt, user_prompt
                )
            except llm_client.LlmDisabledError:
                raise
            except llm_client.LlmError as exc:
                # Один ретрай с явным указанием ошибки
                add_log(
                    job_id, "WARNING",
                    f"Analyzer: первая попытка LLM не удалась: {exc}. "
                    f"Повторяю..."
                )
                retry_user = (
                    user_prompt + "\n\n"
                    f"ПРЕДЫДУЩАЯ ПОПЫТКА ЗАВЕРШИЛАСЬ ОШИБКОЙ: {exc}\n"
                    f"Исправь проблему и верни валидный JSON."
                )
                instruction = llm_client.chat_json(
                    system_prompt, retry_user
                )

            # 3. Валидация схемы
            _validate_instruction(instruction, normalized)

            # 4. Smoke-test на живой странице
            _smoke_test(page, instruction, job_id)

            # 5. Добавляем метаданные
            instruction["generated_at"] = datetime.utcnow().isoformat()
            instruction.setdefault("generator_model", config.LLM_MODEL)
            instruction["domain"] = normalized.domain
            instruction["path_prefix"] = normalize_path_prefix(normalized.path)

            return instruction

    finally:
        if page is not None:
            try:
                page.close()
            except Exception:
                pass

def analyze_and_create_profile(
    app,
    normalized: NormalizedUrl,
    job_id: int,
) -> SiteProfile:
    """
    Главная точка входа: анализирует страницу и создаёт SiteProfile.

    Возвращает созданный/найденный профиль.
    Выбрасывает AnalyzerError при неудаче.

    Сериализуется по (domain, path_prefix): если параллельная задача
    уже анализирует тот же домен, ждём её результата и возвращаем
    уже созданный профиль.
    """
    from models import add_log

    path_prefix = normalize_path_prefix(normalized.path)
    lock = _get_domain_lock(normalized.domain, path_prefix)

    # acquire с таймаутом: если кто-то уже анализирует, ждём до 3 минут
    acquired = lock.acquire(timeout=180)

    try:
        with app.app_context():
            # Возможно, профиль уже создан параллельной задачей
            existing = SiteProfile.query.filter_by(
                domain=normalized.domain,
                path_prefix=path_prefix,
            ).first()

            if existing and existing.is_active:
                add_log(
                    job_id, "INFO",
                    f"Analyzer: профиль уже создан другой задачей, использую "
                    f"{existing.domain}{existing.path_prefix} v{existing.version}."
                )
                return existing

            if not acquired:
                raise AnalyzerError(
                    "Таймаут ожидания анализа домена: другая задача "
                    "слишком долго держит lock."
                )

            add_log(
                job_id, "INFO",
                f"Analyzer: профиль не найден, запускаю анализ "
                f"{normalized.domain}{path_prefix}."
            )

            # Вызываем ядро анализа
            instruction = _analyze_page_and_build_instruction(
                app, normalized, job_id
            )

            # Создаём НОВУЮ строку профиля
            profile = SiteProfile(
                domain=normalized.domain,
                path_prefix=path_prefix,
                instructions_json=json.dumps(
                    instruction, ensure_ascii=False
                ),
                version=1,
                is_active=True,
                fail_count=0,
                last_success_at=None,
                last_failure_at=None,
                last_error=None,
            )

            db.session.add(profile)
            db.session.commit()

            add_log(
                job_id, "INFO",
                f"Analyzer: профиль создан "
                f"(id={profile.id}, v{profile.version})."
            )

            # Перечитываем из БД ПОСЛЕ всех commit-ов: иначе expire_on_commit
            # снова сбросит атрибуты, и при выходе из app_context объект станет
            # detached+expired → DetachedInstanceError в вызывающем коде.
            profile = db.session.get(SiteProfile, profile.id)

            return profile

    finally:
        if acquired:
            try:
                lock.release()
            except Exception:
                pass

def regenerate_profile(
    app,
    profile: SiteProfile,
    job_id: int,
    error_message: str,
) -> SiteProfile:
    """
    Перегенерирует инструкцию профиля при структурном сбое.
    
    In-place схема:
    - НЕ удаляет строку
    - Сохраняет старую инструкцию в previous_instructions_json
    - Обновляет instructions_json новой инструкцией
    - Инкрементирует version
    - При успехе: is_active=True, fail_count=0, retry_not_before=None
    - При неудаче: is_active=False, fail_count+=1, retry_not_before=now+cooldown
    
    Откат в executor работает корректно, потому что previous_instructions_json
    реально содержит предыдущую версию инструкции.
    """
    from models import add_log

    add_log(
        job_id, "INFO",
        f"Самолечение: перегенерация профиля {profile.domain}{profile.path_prefix} "
        f"(v{profile.version}). Причина: {error_message[:200]}"
    )

    # Сохраняем старую инструкцию ДО перегенерации
    old_instructions = profile.instructions_json
    old_version = profile.version

    # Помечаем как неактивный ДО перегенерации
    profile.is_active = False
    profile.last_error = error_message[:2000]
    profile.last_failure_at = db.func.current_timestamp()
    db.session.commit()

    # Нормализуем URL для analyzer
    try:
        normalized = normalize_target_url(
            f"https://{profile.domain}{profile.path_prefix}"
        )
    except Exception as e:
        add_log(job_id, "ERROR", f"Самолечение: ошибка нормализации: {e}")
        mark_profile_failed(profile, f"Ошибка нормализации: {e}")
        return profile

    # Вызываем ядро анализа
    try:
        instruction = _analyze_page_and_build_instruction(
            app, normalized, job_id
        )

        # In-place обновление существующей строки
        profile.previous_instructions_json = old_instructions
        profile.instructions_json = json.dumps(instruction, ensure_ascii=False)
        profile.version = old_version + 1
        profile.is_active = True
        profile.fail_count = 0
        profile.last_error = None
        profile.retry_not_before = None
        db.session.commit()

        add_log(
            job_id, "INFO",
            f"Самолечение: профиль перегенерирован "
            f"(v{old_version} → v{profile.version})."
        )
        return profile

    except AnalyzerError as e:
        add_log(
            job_id, "ERROR",
            f"Самолечение: перегенерация не удалась: {e}"
        )
        mark_profile_failed(profile, str(e))
        return profile

def mark_profile_failed(
    profile: SiteProfile,
    error_message: str,
) -> None:
    """
    Помечает профиль как неактивный после структурного сбоя.

    Устанавливает:
    - is_active=False
    - fail_count += 1
    - last_error
    - last_failure_at
    - retry_not_before = now + PROFILE_RESCAN_COOLDOWN_SECONDS
    """
    profile.is_active = False
    profile.fail_count = (profile.fail_count or 0) + 1
    profile.last_error = str(error_message)[:2000]
    profile.last_failure_at = datetime.utcnow()
    profile.retry_not_before = datetime.utcnow() + timedelta(
        seconds=config.PROFILE_RESCAN_COOLDOWN_SECONDS
    )
    db.session.commit()