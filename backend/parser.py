import json
import random
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode

from playwright.sync_api import sync_playwright

from models import (
    db, Job, Log, ParsedItem, JobStatus, LogLevel, SiteProfile,
    ChildProfile, utcnow,
)
from url_utils import normalize_target_url, find_best_profile, normalize_path_prefix
import analyzer
import notifier
import config

BASE_DIR = Path(__file__).resolve().parent
DEBUG_DIR = BASE_DIR / "debug"

# Глобальные предохранители
MAX_PAGES = 1500
MAX_CARDS = 20000

# Антибот-защита
CAPTCHA_POLL_SECONDS = 5
PAGE_DELAY = (3.0, 6.0)
REST_EVERY = (40, 60)
REST_DELAY = (30.0, 60.0)

BLOCK_MARKERS = [
    "we think you might be a bot",
    "checking your browser",
    "attention required",
    "hcaptcha",
]

# Специфичные маркеры страницы проверки Cloudflare.
# Одно слово "cloudflare" не используется: оно может встречаться
# в обычном содержимом страницы.
CLOUDFLARE_MARKERS = [
    "ray id",
    "checking your browser",
    "ddos protection",
    "cf-chl-",
    "challenge-platform",
]

# Дефолтные маркеры авторизации (используются если инструкция не содержит своих)
DEFAULT_AUTH_MARKERS = [
    "sign in",
    "log in",
    "login",
    "create account",
    "register",
    "please sign in",
]


def _add_log(job_id, level, message):
    try:
        db.session.add(Log(job_id=job_id, level=level, message=str(message)))
        db.session.commit()
    except Exception:
        db.session.rollback()


def _set_job_status(job_id, status, total_found=None):
    try:
        job = db.session.get(Job, job_id)
        if not job:
            return
        job.status = status
        if total_found is not None:
            job.total_found = total_found
        db.session.commit()
    except Exception:
        db.session.rollback()


def _is_blocked(page):
    try:
        low = page.content().lower()
    except Exception:
        return False, ""
    for m in BLOCK_MARKERS:
        if m in low:
            return True, m
    return False, ""


def _classify_block(page):
    """
    Классифицирует тип блока.
    
    Возвращает: (is_blocked, block_reason, marker)
    block_reason может быть: 'captcha', 'cloudflare', 'auth_required', None
    """
    try:
        low = page.content().lower()
    except Exception:
        return False, None, ""
    
    # Проверяем Cloudflare
    for m in CLOUDFLARE_MARKERS:
        if m in low:
            return True, "cloudflare", m
    
    # Проверяем общие маркеры блока (включая капчу)
    for m in BLOCK_MARKERS:
        if m in low:
            return True, "captcha", m
    
    return False, None, ""


def _check_auth_required(page, instruction):
    """
    Проверяет, требует ли страница авторизации.
    
    Логика:
    - Если карточек нет И страница содержит auth_markers → auth_required
    - auth_markers берутся из инструкции или из дефолтного списка
    """
    try:
        low = page.content().lower()
    except Exception:
        return False
    
    auth_markers = instruction.get("auth_markers") or DEFAULT_AUTH_MARKERS
    
    for marker in auth_markers:
        if marker.lower() in low:
            # Проверяем, есть ли карточки
            card_selector = instruction.get("card_selector")
            if card_selector:
                try:
                    cards = page.query_selector_all(card_selector)
                    if not cards:
                        return True
                except Exception:
                    pass
    
    return False


def _wait_human(app, page, job_id, target_url, domain, block_reason, instruction=None):
    """
    Ожидание действия человека.

    Используется для:
    - captcha;
    - cloudflare;
    - auth_required.

    Логика:
    1. Переводит задачу в WAITING_HUMAN.
    2. Отправляет Telegram-уведомление через notifier.
    3. Ждёт до HUMAN_WAIT_SECONDS.
    4. Если проблема исчезла — переводит задачу в RUNNING и возвращает True.
    5. Если таймаут — возвращает False.
    """
    wait_seconds = config.HUMAN_WAIT_SECONDS

    reason_text = {
        "captcha": "капча/блок",
        "cloudflare": "блок Cloudflare",
        "auth_required": "авторизация",
    }.get(block_reason, block_reason)

    try:
        job = db.session.get(Job, job_id)
        if job:
            job.status = JobStatus.WAITING_HUMAN
            job.block_reason = block_reason
            job.human_requested_at = utcnow()
            db.session.commit()
    except Exception:
        db.session.rollback()

    _add_log(
        job_id,
        LogLevel.WARNING,
        f"Требуется действие человека ({reason_text}). "
        f"Жду до {wait_seconds // 60} мин."
    )

    notifier.notify_human_required(
        app,
        job_id,
        target_url,
        domain,
        block_reason
    )

    deadline = time.time() + wait_seconds
    resolved = False

    try:
        while time.time() < deadline:
            time.sleep(CAPTCHA_POLL_SECONDS)

            try:
                if block_reason == "auth_required":
                    if instruction and not _check_auth_required(page, instruction):
                        resolved = True
                        break
                else:
                    is_blocked, _, _ = _classify_block(page)
                    if not is_blocked:
                        resolved = True
                        break
            except Exception:
                continue

        if resolved:
            _add_log(
                job_id,
                LogLevel.INFO,
                "Действие человека обнаружено, продолжаю."
            )

            try:
                job = db.session.get(Job, job_id)
                if job:
                    job.status = JobStatus.RUNNING
                    db.session.commit()
            except Exception:
                db.session.rollback()

            time.sleep(random.uniform(2.0, 4.0))
            return True

        _add_log(
            job_id,
            LogLevel.ERROR,
            f"Действие человека не выполнено за {wait_seconds // 60} мин."
        )
        return False

    finally:
        notifier.mark_human_episode_finished(job_id)


def _save_debug(page, job_id, tag):
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        png = DEBUG_DIR / f"job{job_id}{tag}.png"
        html_path = DEBUG_DIR / f"job{job_id}_{tag}.html"
        try:
            page.screenshot(path=str(png), full_page=False)
        except Exception:
            pass
        try:
            html_path.write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
        _add_log(job_id, LogLevel.INFO,
                 f"[diag] Дамп сохранён: {png.name}, {html_path.name}")
    except Exception as e:
        _add_log(job_id, LogLevel.WARNING, f"[diag] Не удалось сохранить дамп: {e}")


def _norm(text):
    if not text:
        return ""
    return " ".join(text.split())


def _extract_text_by_selectors(element, selectors):
    parts = []
    for selector in selectors:
        try:
            node = element.query_selector(selector)
            if node:
                text = _norm(node.inner_text())
                if text:
                    parts.append(text)
        except Exception:
            continue
    return "\n".join(parts)


def _card_title(card, instruction):
    selector = instruction["fields"]["title_selector"]
    try:
        node = card.query_selector(selector)
        if node:
            text = (node.inner_text() or "").strip()
            if text:
                return text
    except Exception:
        pass

    try:
        node = card.query_selector(".title")
        if node:
            text = (node.inner_text() or "").strip()
            if text:
                return text
    except Exception:
        pass

    return "Без названия"


def _card_url(card, instruction, base_url):
    selector = instruction["fields"]["link_selector"]
    href = None
    try:
        node = card.query_selector(selector)
        if node:
            href = node.get_attribute("href")
    except Exception:
        href = None

    if not href:
        try:
            node = card.query_selector("a[href]")
            if node:
                href = node.get_attribute("href")
        except Exception:
            href = None

    if not href:
        return None

    return urljoin(base_url, href)


def _card_listing_text(card, instruction):
    text_selectors = instruction["fields"].get("text_selectors") or []
    if text_selectors:
        return _extract_text_by_selectors(card, text_selectors)

    try:
        return _norm(card.inner_text())
    except Exception:
        return ""


def _child_path_prefix(path):
    normalized_path = normalize_path_prefix(path)
    if normalized_path == "/":
        return normalized_path
    parent_path = normalized_path.rsplit("/", 1)[0]
    return parent_path or "/"


def find_best_child_profile(parent_profile_id, domain, path):
    normalized_path = normalize_path_prefix(path)
    candidates = ChildProfile.query.filter_by(
        parent_profile_id=parent_profile_id,
        domain=domain,
        is_active=True,
    ).all()
    matching = []
    for candidate in candidates:
        candidate_prefix = normalize_path_prefix(candidate.path_prefix)
        if (
            candidate_prefix == "/"
            or normalized_path == candidate_prefix
            or normalized_path.startswith(candidate_prefix + "/")
        ):
            matching.append(candidate)
    return max(
        matching,
        key=lambda candidate: len(normalize_path_prefix(candidate.path_prefix)),
        default=None,
    )


def _extract_child_text(detail_page, instruction):
    detail = instruction.get("detail") or {}
    sections = []
    title_selector = detail.get("title_selector")
    if title_selector:
        try:
            node = detail_page.query_selector(title_selector)
            if node:
                title = (node.inner_text() or "").strip()
                if title:
                    sections.append(title)
        except Exception:
            pass
    for key in ("text_selectors", "fallback_selectors"):
        for selector in detail.get(key) or []:
            try:
                node = detail_page.query_selector(selector)
                if node:
                    text = (node.inner_text() or "").strip()
                    if text and text not in sections:
                        sections.append(text)
            except Exception:
                pass
    return "\n\n".join(sections)


def _detail_text(detail_page, url, instruction):
    try:
        detail_page.goto(url, timeout=45000, wait_until="domcontentloaded")
    except Exception:
        return ""
    time.sleep(random.uniform(0.8, 1.6))

    detail_config = instruction.get("detail") or {}
    if not detail_config.get("enabled", False):
        return ""

    expand_btn = detail_config.get("expand_button_selector")
    if expand_btn:
        try:
            toggle_btn = detail_page.query_selector(expand_btn)
            if toggle_btn:
                toggle_btn.click()
                time.sleep(0.5)
        except Exception:
            pass

    sections = []

    full_text_selector = detail_config.get("full_text_selector")
    if full_text_selector:
        try:
            node = detail_page.query_selector(full_text_selector)
            if node:
                text = (node.inner_text() or "").strip()
                if text and len(text) > 40:
                    sections.append(text)
        except Exception:
            pass

    if not sections:
        fallback_selectors = detail_config.get("fallback_selectors") or []
        for selector in fallback_selectors:
            try:
                node = detail_page.query_selector(selector)
                if node:
                    text = (node.inner_text() or "").strip()
                    if text and len(text) > 40:
                        sections.append(text)
                        break
            except Exception:
                continue

    return "\n\n".join(sections)


def _build_fragment_url(target_url, page_num, pagination_config):
    parts = urlparse(target_url)

    if pagination_config.get("keep_original_query", True):
        pairs = parse_qsl(parts.query, keep_blank_values=True)
    else:
        pairs = []

    drop_params = pagination_config.get("drop_query_params") or []
    pairs = [(k, v) for (k, v) in pairs if k not in drop_params]

    force_params = pagination_config.get("force_query_params") or {}
    for key, value in force_params.items():
        pairs.append((key, value))

    add_if_missing = pagination_config.get("add_if_missing") or {}
    existing_keys = {k for k, _ in pairs}
    for key, value in add_if_missing.items():
        if key not in existing_keys:
            pairs.append((key, value))

    page_param = pagination_config.get("page_param", "page")
    pairs.append((page_param, str(page_num)))

    return parts._replace(query=urlencode(pairs)).geturl()

def _wait_for_new_cards(page, card_selector, old_count, timeout_ms=10000, poll_seconds=0.5):
    """
    Ждёт, пока число карточек по card_selector вырастет больше old_count.

    Используется для DOM-стратегий пагинации:
    - next_button: после клика по кнопке «следующая»;
    - load_more: после клика по кнопке «показать ещё»;
    - infinite_scroll: после прокрутки вниз.

    Возвращает новое число карточек. При таймауте возвращает текущее
    число (которое может быть равно old_count — это сигнал естественного конца).

    Не бросает исключений: любые ошибки чтения DOM приводят к возврату old_count.
    """
    deadline = time.time() + timeout_ms / 1000

    while time.time() < deadline:
        try:
            current = len(page.query_selector_all(card_selector))
            if current > old_count:
                # Небольшая стабилизационная пауза: иногда DOM ещё дозаписывается
                time.sleep(0.3)
                try:
                    return len(page.query_selector_all(card_selector))
                except Exception:
                    return current
        except Exception:
            pass
        time.sleep(poll_seconds)

    try:
        return len(page.query_selector_all(card_selector))
    except Exception:
        return old_count

def _validate_page(page, instruction, job_id):
    """
    Валидирует страницу по правилам из инструкции.
    
    Возвращает: (is_valid, error_message, cards_count)
    """
    card_selector = instruction.get("card_selector")
    if not card_selector:
        return False, "card_selector отсутствует в инструкции", 0
    
    try:
        cards = page.query_selector_all(card_selector)
    except Exception as e:
        return False, f"Невалидный CSS в card_selector: {e}", 0
    
    cards_count = len(cards)
    validation = instruction.get("validation") or {}
    min_cards = int(validation.get("min_cards_first_page", 1))
    
    if cards_count < min_cards:
        return False, (
            f"Найдено {cards_count} карточек, ожидалось минимум {min_cards} "
            f"(validation.min_cards_first_page)"
        ), cards_count
    
    # Проверяем поля на первых карточках
    check_count = min(5, cards_count)
    if check_count > 0:
        empty_titles = 0
        empty_links = 0
        
        fields = instruction.get("fields") or {}
        title_selector = fields.get("title_selector")
        link_selector = fields.get("link_selector")
        
        for i in range(check_count):
            card = cards[i]
            
            if title_selector:
                try:
                    title_node = card.query_selector(title_selector)
                    if title_node is None:
                        empty_titles += 1
                    else:
                        t = (title_node.inner_text() or "").strip()
                        if not t:
                            empty_titles += 1
                except Exception as e:
                    return False, f"Невалидный CSS в title_selector: {e}", cards_count
            
            if link_selector:
                try:
                    link_node = card.query_selector(link_selector)
                    if link_node is None:
                        empty_links += 1
                    else:
                        href = link_node.get_attribute("href")
                        if not href:
                            empty_links += 1
                except Exception as e:
                    return False, f"Невалидный CSS в link_selector: {e}", cards_count
        
        max_empty_title_ratio = float(validation.get("max_empty_title_ratio", 0.2))
        max_empty_url_ratio = float(validation.get("max_empty_url_ratio", 0.2))
        
        title_ratio = empty_titles / check_count
        url_ratio = empty_links / check_count
        
        if title_ratio > max_empty_title_ratio:
            return False, (
                f"{empty_titles}/{check_count} ({title_ratio:.0%}) карточек без заголовка. "
                f"Допустимо не более {max_empty_title_ratio:.0%}"
            ), cards_count
        
        if url_ratio > max_empty_url_ratio:
            return False, (
                f"{empty_links}/{check_count} ({url_ratio:.0%}) карточек без ссылки. "
                f"Допустимо не более {max_empty_url_ratio:.0%}"
            ), cards_count
    
    return True, None, cards_count


def run_universal_parser(app, job_id, target_url):
    """
    Универсальный executor парсинга по JSON-инструкции из SiteProfile.
    
    Фаза 4: добавлена классификация ошибок и самолечение.
    """
    with app.app_context():
        page = None
        detail_page = None
        browser = None
        processed = 0
        total_found = 0
        profile = None
        retry_after_heal = False  # Флаг для одного ретрая после самолечения

        try:
            _set_job_status(job_id, JobStatus.RUNNING)
            _add_log(job_id, LogLevel.INFO, f"Запуск парсера: {target_url}")

            # 1. Нормализация и поиск профиля
            try:
                normalized = normalize_target_url(target_url)
            except Exception as e:
                _add_log(job_id, LogLevel.ERROR, f"Ошибка нормализации URL: {e}")
                _set_job_status(job_id, JobStatus.FAILED)
                return

            profile = find_best_profile(normalized)

            # 2. Если профиля нет или он неактивен — запускаем analyzer
            if profile is None:
                _add_log(
                    job_id, LogLevel.INFO,
                    f"Профиль для {normalized.domain} не найден. "
                    f"Запускаю анализатор страницы."
                )
                _add_log(job_id, LogLevel.INFO, "анализ страницы")
                _add_log(job_id, LogLevel.INFO, "запуск нейро анализа страницы")
                try:
                    profile = analyzer.analyze_and_create_profile(
                        app, normalized, job_id
                    )
                except analyzer.NoProfileCreatedError as e:
                    _add_log(job_id, LogLevel.ERROR, str(e))
                    _set_job_status(job_id, JobStatus.FAILED)
                    return
                except analyzer.AnalyzerError as e:
                    _add_log(
                        job_id, LogLevel.ERROR,
                        f"Анализатор не смог создать профиль: {e}"
                    )
                    _set_job_status(job_id, JobStatus.FAILED)
                    return

            elif not profile.is_active:
                # Профиль сломан, проверяем кулдаун (используем Python utcnow())
                if profile.retry_not_before and profile.retry_not_before > utcnow():
                    _add_log(
                        job_id, LogLevel.ERROR,
                        f"Профиль {profile.domain}{profile.path_prefix} "
                        f"признан сломанным и находится на кулдауне. "
                        f"Последняя ошибка: {profile.last_error}"
                    )
                    _set_job_status(job_id, JobStatus.FAILED)
                    return
                else:
                    # Кулдаун прошёл — пытаемся пересканировать
                    _add_log(
                        job_id, LogLevel.INFO,
                        f"Профиль {profile.domain}{profile.path_prefix} "
                        f"неактивен, кулдаун прошёл. Пересканирую."
                    )
                    _add_log(job_id, LogLevel.INFO, "анализ страницы")
                    _add_log(job_id, LogLevel.INFO, "запуск нейро анализа страницы")
                    try:
                        profile = analyzer.regenerate_profile(
                            app, profile, job_id, "Кулдаун прошёл"
                        )
                        if not profile.is_active:
                            _add_log(
                                job_id, LogLevel.ERROR,
                                f"Пересканирование не удалось. "
                                f"Ошибка: {profile.last_error}"
                            )
                            _set_job_status(job_id, JobStatus.FAILED)
                            return
                    except analyzer.AnalyzerError as e:
                        _add_log(
                            job_id, LogLevel.ERROR,
                            f"Повторный анализ не удался: {e}"
                        )
                        _set_job_status(job_id, JobStatus.FAILED)
                        return

            # Пере-привязываем профиль к текущей сессии: analyzer мог вернуть
            # detached-объект (собственный app_context был уже закрыт).
            try:
                profile = db.session.get(SiteProfile, profile.id)
            except Exception:
                db.session.rollback()
                _add_log(
                    job_id, LogLevel.ERROR,
                    f"Не удалось загрузить профиль #{profile.id} из БД."
                )
                _set_job_status(job_id, JobStatus.FAILED)
                return

            _add_log(
                job_id, LogLevel.INFO,
                f"Используется профиль: {profile.domain}{profile.path_prefix} "
                f"(v{profile.version})"
            )

            # Привязываем job к профилю
            try:
                job = db.session.get(Job, job_id)
                if job is not None:
                    job.profile_id = profile.id
                    db.session.commit()
            except Exception:
                db.session.rollback()

            # 3. Загружаем инструкцию
            try:
                instruction = json.loads(profile.instructions_json)
            except Exception as e:
                _add_log(job_id, LogLevel.ERROR, f"Ошибка парсинга инструкции: {e}")
                _set_job_status(job_id, JobStatus.FAILED)
                return

            schema_version = instruction.get("schema_version")
            if schema_version != 1:
                _add_log(
                    job_id, LogLevel.ERROR,
                    f"Неподдерживаемая версия схемы: {schema_version}"
                )
                _set_job_status(job_id, JobStatus.FAILED)
                return

            # 4. Извлекаем настройки из инструкции
            card_selector = instruction["card_selector"]
            pagination_config = instruction.get("pagination") or {}
            pagination_strategy = pagination_config.get("strategy")

            # Валидация стратегии пагинации
            if not pagination_strategy:
                _add_log(
                    job_id, LogLevel.ERROR,
                    "В инструкции отсутствует pagination.strategy."
                )
                _set_job_status(job_id, JobStatus.FAILED)
                return

            if pagination_strategy not in {
                "html_fragment_url", "none", "query_param_page",
                "next_button", "load_more", "infinite_scroll",
            }:
                _add_log(
                    job_id, LogLevel.ERROR,
                    f"Стратегия пагинации {pagination_strategy!r} "
                    f"не поддерживается в executor."
                )
                _set_job_status(job_id, JobStatus.FAILED)
                return

            # Проверка обязательных параметров для DOM-стратегий
            if pagination_strategy == "next_button":
                if not pagination_config.get("selector"):
                    _add_log(
                        job_id, LogLevel.ERROR,
                        "Для стратегии next_button обязателен pagination.selector."
                    )
                    _set_job_status(job_id, JobStatus.FAILED)
                    return
            elif pagination_strategy == "load_more":
                if not pagination_config.get("selector"):
                    _add_log(
                        job_id, LogLevel.ERROR,
                        "Для стратегии load_more обязателен pagination.selector."
                    )
                    _set_job_status(job_id, JobStatus.FAILED)
                    return

            stop_conditions = instruction.get("stop_conditions") or {}
            job = db.session.get(Job, job_id)
            parse_mode = (job.parse_mode if job else "fast") or "fast"
            detail_enabled = parse_mode == "smart"

            requested_max_pages = job.max_pages if job else 1
            max_pages = min(
                int(requested_max_pages or stop_conditions.get("max_pages", MAX_PAGES)),
                MAX_PAGES,
            )
            max_child_profiles = (
                int(job.max_child_profiles or 20) if job else 20
            )
            max_cards = min(
                int(stop_conditions.get("max_cards", MAX_CARDS)),
                MAX_CARDS,
            )

            first_page_is_target = pagination_config.get(
                "first_page_is_target", True
            )

            # 5. Подключение к CDP
            with sync_playwright() as p:
                try:
                    browser = p.chromium.connect_over_cdp(config.CDP_URL)
                except Exception:
                    _add_log(
                        job_id, LogLevel.ERROR,
                        f"Не удалось подключиться к Chrome на {config.CDP_URL}. "
                        f"Запусти backend/start_chrome.command."
                    )
                    _set_job_status(job_id, JobStatus.FAILED)
                    return

                if not browser.contexts:
                    _add_log(
                        job_id, LogLevel.ERROR,
                        "Chrome подключён, но вкладок нет. "
                        "Открой в нём любую страницу."
                    )
                    _set_job_status(job_id, JobStatus.FAILED)
                    return

                context = browser.contexts[0]
                page = context.new_page()
                _add_log(job_id, LogLevel.INFO,
                         "Подключён к Chrome (CDP). Загружаю листинг...")

                if detail_enabled:
                    detail_page = context.new_page()

                mode = ("полный текст (с переходами)" if detail_enabled
                        else "быстрый (только листинг)")
                _add_log(job_id, LogLevel.INFO, f"Режим: {mode}")

                seen_urls = set()
                detail_pages_processed = 0
                new_child_profiles = 0
                pages_since_rest = 0

                # 6. Основной цикл пагинации
                # Поддерживаемые стратегии:
                # - html_fragment_url: новый URL на каждой итерации (прямой GET)
                # - query_param_page:  новый URL на каждой итерации (параметр page=N)
                # - next_button:       клик по кнопке «следующая» на той же странице
                # - load_more:         клик по кнопке «показать ещё»
                # - infinite_scroll:   прокрутка страницы вниз
                # - none:              только первая страница

                for page_num in range(1, max_pages + 1):
                    # === ШАГ A: Навигация / действие пагинации ===

                    if page_num == 1:
                        # Первая итерация: для всех стратегий загружаем исходный URL
                        page_url = target_url
                        _add_log(
                            job_id, LogLevel.INFO,
                            f"--- Страница {page_num} ---"
                        )
                        try:
                            page.goto(
                                page_url, timeout=60000,
                                wait_until="domcontentloaded"
                            )
                        except Exception as e:
                            _add_log(
                                job_id, LogLevel.ERROR,
                                f"Не удалось загрузить страницу {page_num}: {e}"
                            )
                            _set_job_status(job_id, JobStatus.FAILED)
                            return

                        time.sleep(random.uniform(2.0, 3.5))

                    else:
                        # Последующие итерации: действие зависит от стратегии

                        if pagination_strategy == "none":
                            # Стратегия none: только первая страница
                            _add_log(
                                job_id, LogLevel.INFO,
                                "Стратегия 'none': только первая страница."
                            )
                            break

                        elif pagination_strategy in (
                            "html_fragment_url", "query_param_page"
                        ):
                            # URL-стратегии: переход на новый URL
                            page_url = _build_fragment_url(
                                target_url, page_num, pagination_config
                            )
                            _add_log(
                                job_id, LogLevel.INFO,
                                f"--- Страница {page_num} ---"
                            )
                            try:
                                page.goto(
                                    page_url, timeout=60000,
                                    wait_until="domcontentloaded"
                                )
                            except Exception as e:
                                _add_log(
                                    job_id, LogLevel.ERROR,
                                    f"Не удалось загрузить страницу "
                                    f"{page_num}: {e}"
                                )
                                _set_job_status(job_id, JobStatus.FAILED)
                                return

                            time.sleep(random.uniform(2.0, 3.5))

                        elif pagination_strategy == "next_button":
                            # Поиск кнопки «следующая»
                            btn_selector = pagination_config.get("selector")
                            if not btn_selector:
                                _add_log(
                                    job_id, LogLevel.ERROR,
                                    "next_button: pagination.selector "
                                    "не указан в инструкции."
                                )
                                _set_job_status(job_id, JobStatus.FAILED)
                                return

                            button = None
                            try:
                                button = page.query_selector(btn_selector)
                            except Exception as e:
                                _add_log(
                                    job_id, LogLevel.ERROR,
                                    f"Невалидный CSS в next_button.selector "
                                    f"{btn_selector!r}: {e}"
                                )
                                _set_job_status(job_id, JobStatus.FAILED)
                                return

                            if button is None:
                                _add_log(
                                    job_id, LogLevel.INFO,
                                    "Кнопка 'следующая' не найдена — "
                                    "естественный конец."
                                )
                                break

                            # Проверка disabled
                            is_disabled = False
                            try:
                                is_disabled = (
                                    button.is_disabled()
                                    or button.get_attribute("disabled") is not None
                                )
                            except Exception:
                                pass

                            if is_disabled:
                                _add_log(
                                    job_id, LogLevel.INFO,
                                    "Кнопка 'следующая' неактивна (disabled) — "
                                    "естественный конец."
                                )
                                break

                            _add_log(
                                job_id, LogLevel.INFO,
                                f"--- Итерация {page_num} (next_button) ---"
                            )

                            old_count = len(
                                page.query_selector_all(card_selector)
                            )
                            old_url = page.url

                            try:
                                button.click()
                            except Exception as e:
                                _add_log(
                                    job_id, LogLevel.ERROR,
                                    f"Не удалось кликнуть next_button: {e}"
                                )
                                _set_job_status(job_id, JobStatus.FAILED)
                                return

                            # Некоторые сайты (например, Hacker News) при
                            # переходе на следующую страницу заменяют DOM:
                            # количество карточек остаётся тем же, но URL меняется.
                            deadline = time.time() + 15
                            new_count = old_count
                            navigated = False
                            while time.time() < deadline:
                                try:
                                    new_count = len(
                                        page.query_selector_all(card_selector)
                                    )
                                    navigated = page.url != old_url
                                    if new_count > old_count or navigated:
                                        break
                                except Exception:
                                    pass
                                time.sleep(0.5)

                            if navigated:
                                page_url = page.url

                            if new_count <= old_count and not navigated:
                                _add_log(
                                    job_id, LogLevel.INFO,
                                    "Новые карточки не подгрузились после "
                                    "клика 'следующая' — естественный конец."
                                )
                                break

                        elif pagination_strategy == "load_more":
                            # Поиск кнопки «показать ещё»
                            btn_selector = pagination_config.get("selector")
                            if not btn_selector:
                                _add_log(
                                    job_id, LogLevel.ERROR,
                                    "load_more: pagination.selector "
                                    "не указан в инструкции."
                                )
                                _set_job_status(job_id, JobStatus.FAILED)
                                return

                            button = None
                            try:
                                button = page.query_selector(btn_selector)
                            except Exception as e:
                                _add_log(
                                    job_id, LogLevel.ERROR,
                                    f"Невалидный CSS в load_more.selector "
                                    f"{btn_selector!r}: {e}"
                                )
                                _set_job_status(job_id, JobStatus.FAILED)
                                return

                            if button is None:
                                _add_log(
                                    job_id, LogLevel.INFO,
                                    "Кнопка 'Load more' не найдена — "
                                    "естественный конец."
                                )
                                break

                            is_disabled = False
                            try:
                                is_disabled = (
                                    button.is_disabled()
                                    or button.get_attribute("disabled") is not None
                                )
                            except Exception:
                                pass

                            if is_disabled:
                                _add_log(
                                    job_id, LogLevel.INFO,
                                    "Кнопка 'Load more' неактивна (disabled) — "
                                    "естественный конец."
                                )
                                break

                            _add_log(
                                job_id, LogLevel.INFO,
                                f"--- Итерация {page_num} (load_more) ---"
                            )

                            old_count = len(
                                page.query_selector_all(card_selector)
                            )

                            try:
                                button.click()
                            except Exception as e:
                                _add_log(
                                    job_id, LogLevel.ERROR,
                                    f"Не удалось кликнуть load_more: {e}"
                                )
                                _set_job_status(job_id, JobStatus.FAILED)
                                return

                            # Если задан wait_selector — ждём его появления
                            wait_selector = pagination_config.get("wait_selector")
                            if wait_selector:
                                try:
                                    page.wait_for_selector(
                                        wait_selector, timeout=15000
                                    )
                                except Exception:
                                    pass

                            new_count = _wait_for_new_cards(
                                page, card_selector, old_count,
                                timeout_ms=15000,
                            )

                            if new_count <= old_count:
                                _add_log(
                                    job_id, LogLevel.INFO,
                                    "Новые карточки не подгрузились после "
                                    "клика 'Load more' — естественный конец."
                                )
                                break

                        elif pagination_strategy == "infinite_scroll":
                            _add_log(
                                job_id, LogLevel.INFO,
                                f"--- Итерация {page_num} (infinite_scroll) ---"
                            )

                            old_count = len(
                                page.query_selector_all(card_selector)
                            )

                            try:
                                page.evaluate(
                                    "window.scrollBy(0, window.innerHeight * 0.9)"
                                )
                            except Exception as e:
                                _add_log(
                                    job_id, LogLevel.WARNING,
                                    f"Ошибка прокрутки: {e}"
                                )

                            time.sleep(random.uniform(1.0, 2.0))

                            # Таймаут для infinite_scroll короче: если сайт
                            # не подгрузил карточки за 8 сек — скорее всего,
                            # это естественный конец листинга.
                            new_count = _wait_for_new_cards(
                                page, card_selector, old_count,
                                timeout_ms=8000,
                            )

                            if new_count <= old_count:
                                _add_log(
                                    job_id, LogLevel.INFO,
                                    "Таймаут ожидания новых карточек при "
                                    "прокрутке — естественный конец."
                                )
                                break

                        else:
                            _add_log(
                                job_id, LogLevel.ERROR,
                                f"Неизвестная стратегия пагинации: "
                                f"{pagination_strategy!r}."
                            )
                            _set_job_status(job_id, JobStatus.FAILED)
                            return

                    # === ШАГ B: Проверка блока (captcha/cloudflare) ===
                    is_blocked, block_reason, marker = _classify_block(page)
                    if is_blocked:
                        _add_log(
                            job_id, LogLevel.WARNING,
                            f"Обнаружен блок ({block_reason}, "
                            f"маркер: '{marker}')."
                        )
                        _save_debug(page, job_id, f"blocked_{block_reason}")

                        if not _wait_human(
                            app, page, job_id, target_url,
                            normalized.domain, block_reason,
                            instruction=instruction,
                        ):
                            _set_job_status(job_id, JobStatus.FAILED)
                            return

                        continue

                    # === ШАГ C: Проверка авторизации (только на 1-й странице) ===
                    if page_num == 1 and _check_auth_required(page, instruction):
                        _add_log(
                            job_id, LogLevel.WARNING,
                            "Страница требует авторизации (auth_markers)."
                        )
                        _save_debug(page, job_id, "auth_required")

                        if not _wait_human(
                            app, page, job_id, target_url,
                            normalized.domain, "auth_required",
                            instruction=instruction,
                        ):
                            _set_job_status(job_id, JobStatus.FAILED)
                            return

                        continue

                    # === ШАГ D: Валидация страницы (только на 1-й итерации) ===
                    # Для DOM-стратегий (next_button/load_more/infinite_scroll)
                    # на итерациях 2+ валидация не нужна — инструкция уже
                    # подтверждена на первой странице.
                    if page_num == 1:
                        is_valid, error_message, cards_count = _validate_page(
                            page, instruction, job_id
                        )

                        if not is_valid:
                            _add_log(
                                job_id, LogLevel.ERROR,
                                f"Структурный сбой на странице {page_num}: "
                                f"{error_message}"
                            )
                            _save_debug(
                                page, job_id, f"structural_fail_p{page_num}"
                            )

                            if not retry_after_heal:
                                _add_log(
                                    job_id, LogLevel.INFO,
                                    "Запускаю самолечение профиля..."
                                )
                                try:
                                    old_version = profile.version
                                    profile = analyzer.regenerate_profile(
                                        app, profile, job_id, error_message
                                    )

                                    if profile.is_active:
                                        _add_log(
                                            job_id, LogLevel.INFO,
                                            f"Самолечение успешно: "
                                            f"v{old_version} → "
                                            f"v{profile.version}. "
                                            f"Повторяю попытку."
                                        )
                                        instruction = json.loads(
                                            profile.instructions_json
                                        )
                                        card_selector = instruction[
                                            "card_selector"
                                        ]
                                        pagination_config = (
                                            instruction.get("pagination") or {}
                                        )
                                        pagination_strategy = (
                                            pagination_config.get("strategy")
                                        )
                                        retry_after_heal = True
                                        continue
                                    else:
                                        _add_log(
                                            job_id, LogLevel.ERROR,
                                            "Самолечение не удалось: "
                                            f"{profile.last_error}"
                                        )
                                        _set_job_status(
                                            job_id, JobStatus.FAILED
                                        )
                                        return
                                except Exception as e:
                                    _add_log(
                                        job_id, LogLevel.ERROR,
                                        f"Ошибка самолечения: {e}"
                                    )
                                    _set_job_status(job_id, JobStatus.FAILED)
                                    return
                            else:
                                _add_log(
                                    job_id, LogLevel.ERROR,
                                    "Повторный структурный сбой после "
                                    "самолечения. Откат инструкции."
                                )
                                if profile.previous_instructions_json:
                                    profile.instructions_json = (
                                        profile.previous_instructions_json
                                    )
                                    profile.previous_instructions_json = None
                                    profile.version = max(
                                        1, profile.version - 1
                                    )
                                    profile.is_active = False
                                    profile.fail_count = (profile.fail_count or 0) + 1
                                    profile.last_error = (
                                        "Откат после повторного сбоя: "
                                        f"{error_message}"
                                    )
                                    # Используем utcnow() и timedelta вместо SQL функций
                                    now = utcnow()
                                    profile.last_failure_at = now
                                    profile.retry_not_before = (
                                        now + timedelta(seconds=config.PROFILE_RESCAN_COOLDOWN_SECONDS)
                                    )
                                    db.session.commit()
                                    _add_log(
                                        job_id, LogLevel.INFO,
                                        f"Инструкция откачена на "
                                        f"v{profile.version}. "
                                        f"Профиль на кулдауне."
                                    )
                                else:
                                    _add_log(
                                        job_id, LogLevel.WARNING,
                                        "previous_instructions_json пуст — "
                                        "откат невозможен. Профиль остаётся сломанным."
                                    )
                                    analyzer.mark_profile_failed(
                                        profile,
                                        f"Откат невозможен: {error_message}"
                                    )
                                _set_job_status(job_id, JobStatus.FAILED)
                                return

                    # === ШАГ E: Извлечение карточек ===
                    cards = page.query_selector_all(card_selector)
                    if not cards:
                        _add_log(
                            job_id, LogLevel.INFO,
                            f"Итерация {page_num}: пусто — конец листинга."
                        )
                        break

                    new_on_page = 0
                    for index, card in enumerate(cards, start=1):
                        try:
                            item_url = _card_url(card, instruction, page_url)
                            if not item_url or item_url in seen_urls:
                                continue
                            seen_urls.add(item_url)
                            new_on_page += 1

                            title = _card_title(card, instruction)
                            listing_text = _card_listing_text(card, instruction)
                            raw_text = listing_text

                            detail_url = item_url
                            detail_processed_for_item = False
                            if detail_enabled:
                                if detail_pages_processed >= max(1, int(job.max_detail_pages if job else config.SMART_MAX_DETAIL_PAGES)):
                                    _add_log(
                                        job_id,
                                        LogLevel.WARNING,
                                        "Достигнут лимит detail-страниц; дальше сохраняю только карточки.",
                                    )
                                else:
                                    detail_pages_processed += 1
                                    detail_processed_for_item = True
                                    time.sleep(random.uniform(1.5, 3.5))
                                    try:
                                        detail_page.goto(
                                            item_url,
                                            timeout=45000,
                                            wait_until="domcontentloaded",
                                        )
                                        time.sleep(random.uniform(0.8, 1.6))
                                        detail_normalized = normalize_target_url(detail_page.url)
                                        child = find_best_child_profile(
                                            parent_profile_id=profile.id,
                                            domain=detail_normalized.domain,
                                            path=detail_normalized.path,
                                        )
                                        if child:
                                            child_instruction = json.loads(child.instructions_json)
                                        else:
                                            if new_child_profiles >= max(1, max_child_profiles):
                                                raise RuntimeError(
                                                    "Достигнут лимит новых detail-профилей."
                                                )
                                            _add_log(job_id, LogLevel.INFO, "анализ страницы")
                                            _add_log(job_id, LogLevel.INFO, "запуск нейро анализа страницы")
                                            from analyzer import analyze_and_create_child_profile
                                            child = analyze_and_create_child_profile(
                                                app,
                                                detail_normalized,
                                                profile.id,
                                                job_id,
                                                detail_page,
                                            )
                                            new_child_profiles += 1
                                            child_instruction = json.loads(child.instructions_json)
                                        extra = _extract_child_text(detail_page, child_instruction)
                                        detail_url = detail_page.url
                                    except Exception as detail_error:
                                        _add_log(
                                            job_id,
                                            LogLevel.WARNING,
                                            f"Не удалось обработать detail-страницу {item_url}: {detail_error}",
                                        )
                                        extra = ""

                                    if extra:
                                        raw_text = (
                                            (listing_text + "\n\n--- описание ---\n" + extra)
                                            if listing_text else extra
                                        )

                            db.session.add(ParsedItem(
                                job_id=job_id,
                                title=title,
                                url=detail_url if detail_processed_for_item else item_url,
                                raw_text=raw_text,
                            ))
                            db.session.commit()
                            processed += 1
                            _add_log(
                                job_id, LogLevel.INFO,
                                f"обработана карточка {processed} / "
                                f"{total_found + len(cards)}: "
                                f"{title[:60]}"
                            )
                            if processed >= max_cards:
                                break
                        except Exception as e:
                            db.session.rollback()
                            _add_log(
                                job_id, LogLevel.ERROR,
                                f"Ошибка карточки {index}/{len(cards)} "
                                f"(итерация {page_num}): {str(e)}"
                            )
                            continue

                    total_found = len(seen_urls)
                    _set_job_status(
                        job_id, JobStatus.RUNNING, total_found=total_found
                    )

                    iter_label = (
                        "Страница"
                        if pagination_strategy in (
                            "html_fragment_url", "query_param_page", "none"
                        )
                        else "Итерация"
                    )
                    _add_log(
                        job_id, LogLevel.INFO,
                        f"{iter_label} {page_num}: всего карточек "
                        f"на странице {len(cards)}, новых {new_on_page}, "
                        f"всего уникальных {total_found}."
                    )

                    # Естественный конец: нет новых карточек
                    if new_on_page == 0:
                        _add_log(
                            job_id, LogLevel.INFO,
                            "Новых карточек нет — листинг закончился."
                        )
                        break

                    if processed >= max_cards:
                        _add_log(
                            job_id, LogLevel.INFO,
                            f"Достигнут лимит карточек: {max_cards}."
                        )
                        break

                    # === ШАГ F: Пейсинг ===
                    pages_since_rest += 1
                    if pages_since_rest >= random.randint(*REST_EVERY):
                        rest = random.uniform(*REST_DELAY)
                        _add_log(
                            job_id, LogLevel.INFO,
                            f"Передышка {rest:.0f} сек, "
                            f"чтобы не триггерить защиту."
                        )
                        time.sleep(rest)
                        pages_since_rest = 0
                    else:
                        time.sleep(random.uniform(*PAGE_DELAY))

            _add_log(
                job_id, LogLevel.INFO,
                f"Готово. Сохранено карточек: {processed}"
            )
            _set_job_status(job_id, JobStatus.COMPLETED, total_found=total_found)

            # 7. Обновляем профиль при успехе (используем Python utcnow())
            try:
                profile.last_success_at = utcnow()
                profile.fail_count = 0
                profile.is_active = True
                db.session.commit()
            except Exception:
                db.session.rollback()

        except Exception as e:
            db.session.rollback()
            _add_log(job_id, LogLevel.ERROR,
                     f"Критическая ошибка парсера: {str(e)}")
            _set_job_status(job_id, JobStatus.FAILED)
        finally:
            for pg in (detail_page, page):
                try:
                    if pg:
                        pg.close()
                except Exception:
                    pass


def run_f6s_parser(app, job_id, target_url):
    """
    Обёртка для обратной совместимости.
    Вызывает универсальный executor.
    """
    run_universal_parser(app, job_id, target_url)
