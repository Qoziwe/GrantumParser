# Grantum Parser — Universal Edition

Детальнейшая документация по проекту Grantum Parser, описывающая архитектуру, структуру директорий, каждый файл, функции и эндпоинты как для бекенда, так и для фронтенда. Данная документация предназначена для полного погружения в проект и возможности бесшовной интеграции нового кода.

**Версия:** Universal Parser Edition (Фазы 1-6 завершены)

## Что нового в Universal Edition

Проект эволюционировал из F6S-специфичного парсера в универсальный движок:

- **Автоматический анализ сайтов:** LLM анализирует любую страницу и создаёт JSON-инструкцию (профиль сайта)
- **Самолечение:** При смене вёрстки система автоматически пересканирует профиль
- **Human-in-the-loop:** Telegram-уведомления при капче/авторизации
- **Кэширование:** Повторный запуск по тому же сайту НЕ вызывает LLM
- **Управление профилями:** Страница `/profiles` для просмотра, пересканирования и удаления
- **Два режима парсинга:** Быстрый (`fast`) — только карточки листинга; Умный (`smart`) — дополнительно переходит в detail-страницы карточек и извлекает их содержимое
- **Дочерние профили (ChildProfile):** В умном режиме селекторы detail-страниц сохраняются отдельно и привязываются к родительскому профилю листинга. При повторных заходах на однотипные detail-страницы профиль переиспользуется, LLM вызывается только для незнакомых структур.

## Режимы парсинга и дочерние профили (ChildProfile)

### Быстрый режим (`fast`)

- Извлекает только карточки листинга по инструкции родительского `SiteProfile`.
- По ссылкам карточек не переходит.
- Обрабатывает заданное пользователем количество страниц/итераций.

### Умный режим (`smart`)

- Извлекает карточки листинга, как в быстром режиме.
- Для каждой карточки берёт URL detail-страницы и переходит по нему.
- Пытается найти подходящий **дочерний профиль** (`ChildProfile`) для структуры detail-страницы:
  - если найден — загружает его JSON-инструкцию, LLM **не вызывается**;
  - если не найден — вызывает LLM для анализа detail-страницы, сохраняет новую инструкцию в базу как `ChildProfile`, привязанный к родительскому профилю (`parent_profile_id`), и использует её.
- Для всех страниц с одинаковым URL-шаблоном (например `/item?id=123` и `/item?id=456` → `path_prefix=/item`) создаётся **один** дочерний профиль, а не по одному на каждую карточку.

Архитектура привязки:

```text
SiteProfile (листинг: news.ycombinator.com/)
    └── ChildProfile (detail: news.ycombinator.com/item?id=...)
```

Материнский профиль → дочерние профили: при удалении родителя каскадно удаляются и дочерние.

### Ограничения умного режима

- `Job.max_detail_pages` (задаётся на главном экране как «Detail-страниц»): максимум переходов на detail-страницы за запуск. По умолчанию — `100`; верхнего ограничения нет. Например, чтобы обработать все `1004` карточки, можно указать `1004` или больше.
- `Job.max_child_profiles` (задаётся на главном экране как «Лимит профилей»): максимум новых дочерних профилей (вызовов LLM) за запуск. Значение должно быть целым числом не меньше `1`; верхнего ограничения нет. При превышении — оставшиеся карточки сохраняются только как листинг.
- Если лимит detail-страниц достигнут, парсер не останавливает задачу: он продолжает сохранять карточки листинга, но больше не открывает их detail-страницы.
- Если для detail-страницы найден существующий `ChildProfile`, нейронка повторно не вызывается. Анализ LLM выполняется только для неизвестной структуры или при необходимости пересоздать профиль.

### Логирование вызовов нейросети

Перед каждым анализом страницы через LLM парсер пишет в лог две записи:

```text
анализ страницы
запуск нейро анализа страницы
```

Эти сообщения появляются в следующих случаях:

- первичный анализ неизвестного сайта при создании `SiteProfile`;
- повторный анализ неактивного профиля после окончания кулдауна;
- анализ неизвестной detail-структуры при создании `ChildProfile`.

Обычная обработка карточки без вызова LLM таких сообщений не создаёт.


## Архитектура проекта

Проект разделен на две основные части:

- **Backend (Python / Flask / SQLAlchemy / Playwright / OpenAI API)** — отвечает за управление задачами (Jobs), анализ сайтов через LLM, запуск парсера через headless/CDP Chrome, сохранение результатов в SQLite и выдачу данных по REST API.
- **Frontend (React / Vite / React Router)** — пользовательский интерфейс (SPA), предоставляющий функционал запуска парсинга, просмотра логов в реальном времени, выгрузки спарщенных результатов в CSV и управления профилями сайтов.

## Backend

Бекенд написан на Python с использованием микрофреймворка Flask. Для работы с базой данных (SQLite) используется SQLAlchemy. Парсинг осуществляется с помощью Playwright, который подключается к существующему экземпляру Chrome по протоколу CDP (Chrome DevTools Protocol). Анализ сайтов выполняется через OpenAI-совместимый API (GPT-4o-mini или аналоги).

### Структура директории `backend/`

#### 1. `config.py` (НОВЫЙ)

Модуль централизованной конфигурации через переменные окружения.

**Функции:**

- `_load_env_file()`: Загружает `.env` через `python-dotenv` (приоритет: `backend/.env` → `.env` в cwd). `override=False`, чтобы системные переменные имели приоритет.
- `_get_str(key, default, empty_as_default)`: Читает строковую переменную с опциональной обработкой пустых значений.
- `_get_int(key, default)`: Читает целочисленную переменную с fallback на default при ошибке парсинга.

**Переменные конфигурации:**

- `SERVER_LOCATION`: `"home"` | `"vps"` (дефолт `"home"`). Влияет на текст Telegram-уведомлений.
- `CDP_URL`: Адрес CDP-браузера (дефолт `"http://localhost:9222"`).
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`: Для notifier. Если пустые — уведомления выключены.
- `PUBLIC_BROWSER_URL`: Используется только при `SERVER_LOCATION=vps` (ссылка на noVNC).
- `HUMAN_WAIT_SECONDS`: Сколько ждать действия человека при капче/авторизации (дефолт `600`).
- `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL`: Настройки OpenAI-совместимого API.
- `ANALYZER_MAX_DOM_CHARS`: Лимит размера DOM для analyzer (дефолт `200000`).
- `PROFILE_RESCAN_COOLDOWN_SECONDS`: Кулдаун после неудачного пересканирования (дефолт `3600`).
- `SMART_MAX_DETAIL_PAGES`: Резервное значение максимума переходов на detail-страницы карточек за запуск (дефолт `100`). Для новых задач значение из этого параметра используется, если пользователь не передал `maxDetailPages`.
- `SMART_MAX_NEW_CHILD_PROFILES`: Резервное значение максимума новых дочерних профилей (вызовов LLM) за запуск (дефолт `20`). Для задач используется персональный лимит `Job.max_child_profiles`, который пользователь задаёт на главном экране.
- `maxDetailPages` не имеет верхнего ограничения: проверяется только целое значение не меньше `1`.
- `maxChildProfiles` не имеет верхнего ограничения: проверяется только целое значение не меньше `1`.
- `SQLALCHEMY_DATABASE_URI`: URI базы данных (дефолт `"sqlite:///db.sqlite3"`).

#### 2. `url_utils.py` (НОВЫЙ)

Утилиты для нормализации URL и матчинга профилей сайтов.

**Классы:**

- `UrlValidationError`: Исключение при невалидном URL (не http/https, пустой host).
- `NormalizedUrl` (dataclass): Нормализованное представление URL. Поля:
  - `original`: Исходная строка после `strip()`.
  - `scheme`: `"http"` или `"https"`.
  - `domain`: Host в нижнем регистре без `www.`.
  - `path`: Путь (если пуст — `"/"`).
  - `query`, `fragment`: Сохраняются для executor, но НЕ используются для матчинга.

**Функции:**

- `normalize_path_prefix(prefix)`: Приводит `path_prefix` профиля к каноническому виду (`""` → `"/"`, `"/programs/"` → `"/programs"`).
- `normalize_target_url(raw_url)`: Нормализует URL пользователя. Правила: только http/https, host без `www.`, путь с `/`, query/fragment игнорируются для матчинга.
- `find_best_profile(normalized)`: Ищет лучший `SiteProfile` для нормализованного URL. Логика: домен должен совпадать, `path_prefix` должен подходить под путь (точный или префикс + `/`), из подходящих выбирается самый длинный `path_prefix`.
- `find_best_profile_for_url(raw_url)`: Удобная обёртка: нормализует URL и сразу ищет профиль.

#### 3. `seed_profiles.py` (НОВЫЙ)

Модуль для создания seed-профилей известных сайтов при старте приложения.

**Константы:**

- `F6S_PROGRAMS_INSTRUCTION`: JSON-инструкция для `f6s.com/programs` (карточка `.result-item`, селекторы `.result-description .title a`, пагинация `html_fragment_url`, детальная страница с `#description-toggle`).

**Функции:**

- `ensure_f6s_seed_profile()`: Идемпотентно создаёт seed-профиль F6S, если его нет в БД. Проверяет наличие по `(domain, path_prefix)`. Возвращает существующий или созданный профиль.

#### 4. `llm_client.py` (НОВЫЙ)

Тонкий клиент для OpenAI-совместимого API (без SDK).

**Исключения:**

- `LlmError`: Базовая ошибка LLM-клиента.
- `LlmDisabledError`: LLM не настроен (нет `LLM_API_KEY`).
- `LlmResponseError`: LLM вернул невалидный JSON или неожиданную структуру.
- `LlmHttpError`: Сетевая/HTTP-ошибка при обращении к LLM.

**Функции:**

- `is_enabled()`: Возвращает `True`, если `LLM_API_KEY` не пустой.
- `_build_url()`: Строит URL `{LLM_BASE_URL}/chat/completions`.
- `_build_headers()`: Возвращает `{"Authorization": "Bearer {LLM_API_KEY}", "Content-Type": "application/json"}`.
- `_strip_markdown_fence(raw)`: Убирает ` ```json ... ``` ` обёртку, если LLM её добавил.
- `chat_json(system_prompt, user_prompt, temperature=0.2, max_tokens=None, timeout=120)`:
  - Вызов LLM с ожиданием JSON-ответа.
  - Payload: `model`, `temperature`, `messages` (system + user), `response_format: {"type": "json_object"}`.
  - Парсит `data["choices"][0]["message"]["content"]`, убирает markdown-обёртку, возвращает `dict`.
  - Исключения: `LlmDisabledError`, `LlmHttpError`, `LlmResponseError`.

#### 5. `analyzer.py` (НОВЫЙ)

Модуль анализа страницы и создания `SiteProfile`.

**Исключения:**

- `AnalyzerError`: Базовая ошибка анализатора.
- `NoProfileCreatedError`: LLM не смог создать валидную инструкцию.

**Классы:**

- `NetworkObservation` (dataclass): Наблюдение сетевого запроса. Поля: `url`, `method`, `resource_type`, `status`.
- `CandidateBlock` (dataclass): Повторяющийся блок DOM. Поля: `signature` (тег+классы), `count`, `example_html`.
- `PageSignals` (dataclass): Собранные сигналы страницы. Поля: `final_url`, `title`, `cleaned_dom`, `dom_truncated`, `network_requests`, `candidate_blocks`.

**Глобальные переменные:**

- `_domain_locks`: Словарь `{(domain, path_prefix): threading.Lock()}` для сериализации анализа одного домена.
- `_domain_locks_lock`: Lock для безопасного доступа к `_domain_locks`.
- `_ALLOWED_STRATEGIES`: Допустимые стратегии пагинации v1 (`none`, `query_param_page`, `next_button`, `load_more`, `infinite_scroll`, `html_fragment_url`).
- `_STRIP_TAGS`: Теги, вырезаемые из DOM (`script`, `style`, `svg`, `noscript`, `iframe`, `path`, `meta`, `link`).
- `_STRIP_ATTRS`: Атрибуты, вырезаемые из DOM (`style`, `onclick`, `onmouseover`, `onload`, `onerror`, `data-reactid`, `data-ember-action`).

**Функции:**

- `_get_domain_lock(domain, path_prefix)`: Возвращает lock для `(domain, path_prefix)`, создавая при необходимости.
- `_clean_element_html(html)`: Базовая очистка HTML: убирает шумные атрибуты через regex, схлопывает пробелы.
- `_extract_candidate_blocks(page, max_blocks=5)`: Эвристика повторяющихся блоков. Группирует элементы DOM по сигнатуре (tagName + классы), возвращает топ-N сигнатур с количеством повторений и примером. Снижает галлюцинации LLM.
- `_capture_cleaned_dom(page)`: Возвращает `(cleaned_dom, was_truncated)`. Удаляет `_STRIP_TAGS`, схлопывает пробелы, обрезает до `ANALYZER_MAX_DOM_CHARS` (умное усечение: первые 70%, маркер `[...усечено...]`, последние 20%).
- `_collect_page_signals(page, target_url, job_id, navigate=True)`: Загружает страницу, собирает сетевые сигналы (XHR/fetch/document того же домена), делает короткие прокрутки для обнаружения ленивой пагинации, возвращает `PageSignals`. Параметр `navigate=False` позволяет проанализировать **уже открытую** страницу без повторной навигации (используется для detail-анализа по открытой вкладке).
- `_build_llm_prompts(normalized, signals)`: Строит system и user промпты для LLM. System требует: строго JSON, валидные CSS, предпочитать стабильные селекторы (id, data-_, aria-_), стратегию из `_ALLOWED_STRATEGIES`, заполнять `validation` и `stop_conditions`. User содержит: URL, домен, путь, финальный URL, title, сетевые запросы (первые 80), кандидатные блоки, очищенный DOM.
- `_validate_instruction(instruction, normalized)`: Валидирует JSON-схему. Проверяет: `schema_version=1`, `domain` совпадает, `card_selector` непустой, `fields.title_selector` и `fields.link_selector` непустые, `pagination.strategy` из `_ALLOWED_STRATEGIES`, `stop_conditions` — объект.
- `_smoke_test(page, instruction, job_id)`: Проверяет селекторы на живой странице. Проверяет: `card_selector` даёт не менее `validation.min_cards_first_page` карточек, на первых до 5 карточках проверяет `title_selector`/`link_selector` с учётом допустимых долей пустых значений (`max_empty_title_ratio`, `max_empty_url_ratio`), синтаксис всех селекторов через `query_selector`.
- `analyze_and_create_profile(app, normalized, job_id)`: Главная точка входа. Сериализуется по `(domain, path_prefix)` (lock с таймаутом 180 сек). Логика: если профиль уже создан — возвращает его; иначе загружает страницу, собирает сигналы, вызывает LLM (с одним ретраем при ошибке), валидирует, smoke-test, сохраняет `SiteProfile` в БД.
- `regenerate_profile(app, profile, job_id, error_message)`: Перегенерирует инструкцию при структурном сбое. Сохраняет старую инструкцию в `previous_instructions_json`, помечает профиль как неактивный, удаляет старый профиль, запускает `analyze_and_create_profile`, возвращает обновлённый профиль.
- `mark_profile_failed(profile, error_message)`: Помечает профиль как неактивный: `is_active=False`, `fail_count+=1`, `last_error`, `last_failure_at`, `retry_not_before=now+PROFILE_RESCAN_COOLDOWN_SECONDS`.

**Detail-анализатор (умный режим / дочерние профили):**

- `_detail_system_prompt()`: Системный промпт для LLM при анализе detail-страницы. Требует вернуть только JSON-схему `schema_version=1` с полем `detail` (`title_selector`, `text_selectors`, `fallback_selectors`, опционально `date_selector`, `author_selector`). Явно **запрещает** листинговые поля (`card_selector`, `fields`, `pagination`), чтобы не смешивать схемы.
- `_validate_detail_instruction(instruction, normalized)`: Валидирует detail-инструкцию: `schema_version=1`, `domain` совпадает, `detail` — объект, `title_selector` — строка, `text_selectors`/`fallback_selectors` — массивы строк.
- `_analyze_detail_page(page, normalized, job_id)`: Собирает сигналы (**без** повторной навигации — `navigate=False`), вызывает LLM с detail-промптом, валидирует JSON, проверяет detail-селекторы через `query_selector`, вычисляет `path_prefix` (для `.../item?id=123` → `/item`, без учёта query).
- `analyze_and_create_child_profile(app, normalized, parent_profile_id, job_id, page)`: Создаёт `ChildProfile` для detail-страницы. Вычисляет `path_prefix`, берёт lock `child:{parent_profile_id}:{domain}:{path}`, повторно проверяет существование дочернего профиля (чтобы не вызывать LLM повторно), вызывает `_analyze_detail_page` только при отсутствии, сохраняет JSON в `ChildProfile`, через `db.session.get()` возвращает свежий ORM-объект (чтобы избежать `DetachedInstanceError`).

#### 6. `notifier.py` (НОВЫЙ)

Модуль отправки Telegram-уведомлений о событиях, требующих человека.

**Глобальные переменные:**

- `GLOBAL_COOLDOWN_SECONDS`: 60 секунд между отправками.
- `AGGREGATION_WINDOW_SECONDS`: 2 секунды для агрегации параллельных задач одного домена.
- `_lock`: Глобальный lock для потокобезопасности.
- `_last_sent_at`: Timestamp последней отправки.
- `_job_episodes`: Словарь `{job_id: {"reason": block_reason, "domain": domain}}` для дедупликации.
- `_domain_pending`: Словарь `{domain: {"app", "job_ids", "urls", "reasons", "timer"}}` для агрегации.

**Функции:**

- `is_enabled()`: Возвращает `True`, если `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID` не пустые.
- `_compose_message(job_ids, domain, reasons, urls)`: Собирает текст Telegram-сообщения. Ветвление по `SERVER_LOCATION`:
  - `home`: «Подойди к компьютеру — открыто окно Chrome».
  - `vps`: «Открыть браузер сервера: {PUBLIC_BROWSER_URL}».
  - Тексты различаются для `captcha`/`cloudflare` и `auth_required`.
- `_send_text(text)`: Отправка через `requests.post` на `https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage`. Payload: `{"chat_id": TELEGRAM_CHAT_ID, "text": text}`.
- `notify_human_required(app, job_id, target_url, domain, block_reason)`: Вызывается executor'ом при переходе в `WAITING_HUMAN`. Логика: если эпизод уже уведомлён — `False`; иначе добавляет job в `_domain_pending`, запускает `threading.Timer` на `AGGREGATION_WINDOW_SECONDS` для отложенной отправки.
- `mark_human_episode_finished(job_id)`: Вызывается при завершении эпизода (человек решил, таймаут, задача упала). Удаляет job из `_job_episodes`, очищает `_domain_pending` если нет других задач.
- `_flush_domain(domain)`: Отложенная отправка агрегированного уведомления. Проверяет глобальный кулдаун, если не прошёл — переносит отправку. Иначе вызывает `_compose_message` и `_send_text`, логирует результат.

#### 7. `models.py` (ИЗМЕНЁННЫЙ)

**Добавления:**

- `JobStatus.WAITING_HUMAN`: Новый статус `"waiting_human"` (ожидание действия человека).
- `SiteProfile` (новая модель, таблица `site_profiles`):
  - Поля: `id`, `domain` (индекс), `path_prefix`, `instructions_json` (TEXT), `previous_instructions_json` (TEXT NULL), `version` (INT), `is_active` (BOOL), `fail_count` (INT), `retry_not_before` (DATETIME NULL), `last_success_at`, `last_failure_at`, `last_error` (TEXT NULL), `created_at`, `updated_at`.
  - Уникальный составной индекс `uq_site_profiles_domain_path_prefix` по `(domain, path_prefix)`.
  - Метод `to_dict(include_instructions=False)`: Сериализует в словарь. Если `include_instructions=True`, добавляет `instructions_json` и `previous_instructions_json`.

- `Job` (расширенная модель):
  - Новые поля: `profile_id` (FK на `site_profiles`, `ondelete="SET NULL"`), `block_reason` (TEXT NULL), `human_requested_at` (DATETIME NULL).
  - Поля режимов парсинга: `parse_mode` (`"fast"` | `"smart"`, дефолт `"fast"`), `max_pages` (страницы/итерации, дефолт `1`), `max_child_profiles` (лимит новых detail-профилей за запуск, дефолт `20`), `max_detail_pages` (лимит переходов на detail-страницы, дефолт `100`). Верхнего ограничения для двух пользовательских лимитов нет; допускаются целые значения от `1`. 
  - Связь `profile = relationship("SiteProfile")`.
  - `to_dict()` возвращает новые поля.

- `ChildProfile` (новая модель, таблица `child_profiles`):
  - **Назначение:** Хранит отдельную JSON-инструкцию для detail-страниц карточек в умном режиме, связанную с родительским `SiteProfile`. Не смешивает листинговую и detail-схемы.
  - Поля: `id`, `parent_profile_id` (FK на `site_profiles`, `ondelete="CASCADE"`, индекс), `domain`, `path_prefix`, `instructions_json` (TEXT), `version` (INT), `is_active` (BOOL), `created_at`, `updated_at`.
  - Уникальный составной индекс `uq_child_profiles_parent_domain_path` по `(parent_profile_id, domain, path_prefix)` — предотвращает создание дублирующих дочерних профилей.
  - Связь `parent_profile = relationship("SiteProfile", backref="child_profiles")` с `cascade="all, delete-orphan"` (при удалении родителя удаляются дочерние).

**Функции без изменений:**

- `add_log`, `set_job_status`, `save_parsed_item` — без изменений.

#### 8. `parser.py` (СИЛЬНО ИЗМЕНЁННЫЙ)

Ядро бизнес-логики — универсальный executor парсинга по JSON-инструкции из `SiteProfile`.

**Новые импорты:**

- `import json` (для парсинга `instructions_json`).
- `import analyzer` (для запуска анализатора).
- `import notifier` (для Telegram-уведомлений).
- `from url_utils import normalize_target_url, find_best_profile, normalize_path_prefix`.
- `from models import utcnow` (для `human_requested_at`).

**Новые константы:**

- `CLOUDFLARE_MARKERS`: Список маркеров Cloudflare (`"cloudflare"`, `"ray id"`, `"checking your browser"`, `"ddos protection"`).
- `DEFAULT_AUTH_MARKERS`: Дефолтные маркеры авторизации (`"sign in"`, `"log in"`, `"login"`, `"create account"`, `"register"`, `"please sign in"`).

**Новые функции:**

- `_classify_block(page)`: Классифицирует тип блока. Возвращает `(is_blocked, block_reason, marker)`, где `block_reason` может быть `"captcha"`, `"cloudflare"`, `None`.
- `_check_auth_required(page, instruction)`: Проверяет, требует ли страница авторизации. Логика: если карточек нет И страница содержит `auth_markers` (из инструкции или `DEFAULT_AUTH_MARKERS`) → `True`.
- `_wait_human(app, page, job_id, target_url, domain, block_reason, instruction=None)`: Ожидание действия человека. Заменяет старую `_wait_captcha`. Логика:
  1. Переводит задачу в `WAITING_HUMAN`, ставит `block_reason`, `human_requested_at`.
  2. Вызывает `notifier.notify_human_required`.
  3. Ждёт до `HUMAN_WAIT_SECONDS`, опрашивая страницу каждые `CAPTCHA_POLL_SECONDS`.
  4. Для `auth_required`: проверяет `_check_auth_required(page, instruction)`.
  5. Для `captcha`/`cloudflare`: проверяет `_classify_block(page)`.
  6. Если проблема исчезла — переводит задачу в `RUNNING`, возвращает `True`.
  7. Если таймаут — возвращает `False`.
  8. В `finally` вызывает `notifier.mark_human_episode_finished`.

- `_validate_page(page, instruction, job_id)`: Валидирует страницу по правилам из инструкции. Возвращает `(is_valid, error_message, cards_count)`. Проверяет: `card_selector` даёт не менее `validation.min_cards_first_page` карточек, на первых до 5 карточках проверяет `title_selector`/`link_selector` с учётом допустимых долей пустых значений.

**Новые функции (умный режим / дочерние профили):**

- `find_best_child_profile(parent_profile_id, domain, path)`: Ищет подходящий `ChildProfile` для detail-URL. Ограничивает поиск дочерними профилями конкретного родителя и домена, приводит `path_prefix` к каноническому виду через `normalize_path_prefix`. Подходит, если префикс совпадает точно, является префиксом + `/`, либо равен `/`. Из подходящих выбирает самый длинный `path_prefix` (наиболее специфичный). Возвращает `None`, если профиля нет.
- `_extract_child_text(detail_page, instruction)`: Извлекает текст detail-страницы по схеме child-инструкции: `detail.title_selector`, затем `detail.text_selectors` и `detail.fallback_selectors`. Собирает разделы через `\n\n` и удаляет дубликаты. Обёрнут в try/except, чтобы сбой одного селектора не ломал извлечение.
- Обработка detail-карточки в smart-режиме:
  1. Открывается detail URL по ссылке карточки (после паузы `random.uniform(1.5, 3.5)`).
  2. Используется **итоговый URL** после редиректов (`detail_page.url`).
  3. `find_best_child_profile` ищет существующий дочерний профиль. Если найден — загружается его `instructions_json`, LLM **не вызывается повторно**.
  4. Если не найден — вызывается `analyzer.analyze_and_create_child_profile`, сохраняется новый `ChildProfile` для родителя, `new_child_profiles += 1`.
  5. `_extract_child_text` извлекает detail-текст, который добавляется в `ParsedItem.raw_text` после `--- описание ---`.
  6. В `ParsedItem.url` в smart-режиме сохраняется итоговый URL detail-страницы.
- **Лимиты в smart-режиме:** при достижении `SMART_MAX_DETAIL_PAGES` переходы прекращаются (карточки сохраняются как есть); при достижении `job.max_child_profiles` новых дочерних профилей выбрасывается `RuntimeError` и остальные карточки сохраняются без detail-текста.

**Изменённая функция `run_universal_parser`:**

- **Шаг 0: Чтение режима и лимитов из Job.** Читает `job.parse_mode` (`fast`/`smart`) и `job.max_pages`, `job.max_child_profiles`. `fast` = только листинг, `smart` = дополнительно переходы на detail-страницы карточек. Лимит страниц из Job имеет приоритет над `stop_conditions.max_pages` из LLM-инструкции.

- **Шаг 1: Нормализация и поиск профиля.** Вызывает `normalize_target_url` и `find_best_profile`.
- **Шаг 2: Если профиля нет или он неактивен.** Если `profile is None` → вызывает `analyzer.analyze_and_create_profile`. Если `not profile.is_active` → проверяет кулдаун `retry_not_before`; если прошёл → вызывает `analyzer.regenerate_profile`.
- **Шаг 3: Загрузка инструкции.** Парсит `profile.instructions_json`, проверяет `schema_version=1`.
- **Шаг 4: Классификация ошибок (фаза 4).** На каждой странице:
  1. `_classify_block(page)` → если блок → `_wait_human`.
  2. `_check_auth_required(page, instruction)` → если `auth_required` → `_wait_human`.
  3. `_validate_page(page, instruction)` → если структурный сбой → **самолечение**: вызывает `analyzer.regenerate_profile`, один ретрай исполнения; при повторном сбое — откат на `previous_instructions_json`.
- **Шаг 5: Исполнение.** Извлекает карточки по `card_selector`, поля по `fields`, дедуплицирует по `seen_urls`. Если `detail.enabled` → проваливается в карточку.
- **Шаг 6: Пагинация.** Поддерживает стратегии `html_fragment_url`, `query_param_page`, `none`. Остальные (`next_button`, `load_more`, `infinite_scroll`) распознаются, но не реализованы (падают с ясным логом).
- **Шаг 7: Успех.** Обновляет `profile.last_success_at`, `fail_count=0`, `is_active=True`.

**Функция `run_f6s_parser`:**

- Сохранена как тонкая обёртка: просто вызывает `run_universal_parser`. Это обеспечивает обратную совместимость с `app.py`.

#### 9. `app.py` (ИЗМЕНЁННЫЙ)

**Новые импорты:**

- `import config` (вместо хардкода `SQLALCHEMY_DATABASE_URI`).
- `from models import SiteProfile` (для эндпоинтов профилей).
- `from url_utils import normalize_path_prefix` (для построения URL rescan).
- `import analyzer` (для запуска пересканирования).

**Изменения в инициализации:**

- `app.config["SQLALCHEMY_DATABASE_URI"]` берётся из `config.SQLALCHEMY_DATABASE_URI`.
- Добавлена функция `_migrate_sqlite_schema()`: Идемпотентная lightweight-миграция для существующей SQLite-базы. Добавляет колонки `profile_id`, `block_reason`, `human_requested_at`, `parse_mode`, `max_pages`, `max_child_profiles` в таблицу `jobs` через `ALTER TABLE ADD COLUMN`. Создаёт индексы.
- `ensure_f6s_seed_profile()` вызывается после `db.create_all()` для создания seed-профиля F6S.
- Стартовая очистка расширяется: `JobStatus.WAITING_HUMAN` тоже помечается `FAILED` при старте сервера.

**Новые REST API Эндпоинты (Profiles API):**

- `GET /api/profiles`:
  - **Описание**: Возвращает список всех профилей сайтов.
  - **Логика**: `SiteProfile.query.order_by(updated_at.desc().nullslast(), id.desc())`. Инструкции не возвращаются по умолчанию (`include_instructions=False`).
  - **Возвращает**: Массив объектов `{id, domain, path_prefix, version, is_active, fail_count, retry_not_before, last_success_at, last_failure_at, last_error, created_at, updated_at}`.

- `GET /api/profiles/<int:profile_id>`:
  - **Описание**: Возвращает один профиль, включая `instructions_json`.
  - **Логика**: `db.session.get(SiteProfile, profile_id)`. Если не найдено — 404.
  - **Возвращает**: Объект с `include_instructions=True`.

- `DELETE /api/profiles/<int:profile_id>`:
  - **Описание**: Удаляет профиль сайта.
  - **Логика**: Благодаря `ondelete="SET NULL"` в `models.py`, привязанные jobs остаются в базе, но теряют ссылку на профиль. Следующий запуск парсера по URL этого домена снова запустит analyzer.
  - **Возвращает**: `{deleted: 1, domain, path_prefix}`.

- `POST /api/profiles/<int:profile_id>/rescan`:
  - **Описание**: Принудительное пересканирование профиля.
  - **Логика**:
    1. Сбрасывает кулдаун (`retry_not_before = None`).
    2. Помечает профиль как неактивный (`is_active = False`, `last_error = "Запрошено принудительное пересканирование"`).
    3. Создаёт служебный job со статусом `PENDING`, `target_url = "https://{domain}{path_prefix}"`.
    4. Запускает `_run_rescan` в фоне (отдельный поток).
    5. Возвращает `job_id`, чтобы пользователь мог следить за логами.
  - **Фоновая задача `_run_rescan`**:
    - Ставит статус `RUNNING`.
    - Логирует "Запущено принудительное пересканирование профиля #N".
    - Нормализует URL.
    - Удаляет старый профиль.
    - Вызывает `analyzer.analyze_and_create_profile`.
    - При успехе — статус `COMPLETED`, лог "Создан профиль #M vK".
    - При ошибке — статус `FAILED`, лог с причиной.
  - **Возвращает**: `{ok: True, job_id, profile_id, domain, path_prefix}` с HTTP 202.

**Существующие эндпоинты без изменений:**

- `GET /api/jobs`, `GET /api/jobs/<id>/logs`, `GET /api/items`, `DELETE /api/jobs`, `DELETE /api/jobs/<id>`.

**`POST /api/parse` (ИЗМЕНЁН)**:

- **Описание**: Создаёт задачу и запускает парсер. Принимает JSON `{url, iterations, mode, maxChildProfiles}`.
- **Логика**:
  - `iterations` → `Job.max_pages` (количество страниц/итераций; проверка: целое от 1 до 100).
  - `mode` → `Job.parse_mode`: `"fast"` (только листинг) или `"smart"` (переходы на detail-страницы карточек); другие значения отклоняются.
  - `maxChildProfiles` → `Job.max_child_profiles` (пользовательский лимит новых дочерних профилей за запуск; проверка: целое от 1 до 500). Используется в умном режиме, имеет приоритет над константой `SMART_MAX_NEW_CHILD_PROFILES`.
  - URL нормализуется через `normalize_target_url`, затем запускается worker в фоновом потоке.

#### 10. `save_auth.py`

Без изменений.

#### 11. `start_chrome.command`

Без изменений.

## Frontend

Фронтенд реализован на связке React + Vite. Используется современный подход с функциональными компонентами, хуками и React Router v6. Дизайн реализован локально (через теги `<style>` в компонентах и ванильный CSS с префиксами) с использованием собственной дизайн-системы, вдохновленной терминальной эстетикой.

### Конфигурация Frontend (корень `frontend/`)

Без изменений.

### Структура директории `frontend/src/`

#### 1. `main.jsx`

Без изменений.

#### 2. `App.jsx` (ИЗМЕНЁННЫЙ)

**Новый импорт:**

- `import Profiles from "./pages/Profiles"`.

**Новый маршрут:**

- `<Route path="/profiles" element={<Profiles />} />`.

**Новая ссылка в топбаре:**

- `<NavLink to="/profiles">` с индексом `04` и текстом "Профили".

#### 3. `api.js` (ИЗМЕНЁННЫЙ)

**Новые функции API:**

- `fetchProfiles()`: `GET /profiles` — список всех профилей сайтов.
- `rescanProfile(profileId)`: `POST /profiles/{id}/rescan` — принудительное пересканирование профиля.
- `deleteProfile(profileId)`: `DELETE /profiles/{id}` — удаление профиля.

**Существующие функции без изменений (но сигнатура `startParse` расширена):**

- `fetchJobs`, `fetchJobLogs`, `fetchItems`, `deleteJob`, `deleteAllJobs`.

**Изменённая `startParse(url, options = {})`:**

- Теперь принимает второй аргумент `options`:
  - `options.iterations` → `iterations` (JSON); дефолт `1`.
  - `options.mode` → `mode` (JSON); дефолт `"fast"`.
  - `options.maxChildProfiles` → `maxChildProfiles` (JSON); дефолт `20`.
- Выполняет `POST /parse` с полным телом `{url, iterations, mode, maxChildProfiles}`.

#### Папка `pages/`

#### 4. `Dashboard.jsx` (ИЗМЕНЁННЫЙ)

**Новые состояния (State):**

- `iterations` (дефолт `1`): количество страниц/итераций.
- `mode` (дефолт `"fast"`): выбранный режим парсинга (`fast`/`smart`).
- `maxChildProfiles` (дефолт `20`): лимит новых detail-профилей для умного режима.

**Изменения в `handleLaunch`:**

- Валидирует `iterations` (целое от 1 до 100).
- Валидирует `maxChildProfiles` (целое от 1 до 500).
- Вызывает `startParse(target, {iterations: pageCount, mode, maxChildProfiles: childLimit})`.

**Новый UI в консоли запуска (форма):**

- Поле «Итерации» (числовой инпут, `min=1`, `max=100`) — количество страниц.
- Поле «Лимит профилей» (числовой инпут, `min=1`, `max=500`) — видно только в умном режиме (`mode === "smart"`).
- Переключатель «Режим»: радио «Быстрый» (`fast`) и «Умный» (`smart`). Подпись подсказки зависит от режима:
  - `smart`: «Переходит в карточки и запоминает структуры detail-страниц.»
  - `fast`: «Собирает только карточки листинга.»
- Новые CSS-классы: `.gd-options`, `.gd-option-field`, `.gd-mode-field`, `.gd-number-input`, `.gd-mode-choice`, `.gd-mode-help`.

**Изменения в `STATUS_META`:**

- Добавлен статус `waiting_human`: `{label: "Ожидание", dot: "gd-dot--waiting"}`.

**Изменения в текстах:**

- `PLACEHOLDER`: `"https://www.f6s.com/events"` → `"https://example.com/listing"`.
- Текст ошибки: `"Вставьте ссылку на страницу F6S."` → `"Вставьте ссылку на страницу."`.
- `aria-label` инпута: `"Ссылка на страницу F6S"` → `"Ссылка на страницу"`.
- Lead-текст: "Вставь ссылку на листинг грантов, акселераторов или ивентов..." → "Вставь ссылку на любую страницу с карточками — бэкенд проанализирует сайт, создаст профиль и начнёт парсинг...".

**Новые CSS-классы:**

- `.gd-dot--waiting`: Янтарная точка (`#e0c060`) с пульсацией (`gd-pulse`).
- `.gd-badge--waiting_human`: Янтарный бейдж с полупрозрачным фоном.

#### 5. `LogsPage.jsx` (ИЗМЕНЁННЫЙ)

**Изменения в `STATUS_META`:**

- Добавлен статус `waiting_human`: `{label: "Ожидание", dot: "gl-dot--waiting"}`.

**Новые CSS-классы:**

- `.gl-dot--waiting`: Янтарная точка с пульсацией (`gl-pulse`).
- `.gl-statusbar--waiting_human`: Янтарный бордер статус-бара.
- `.gl-waiting-banner`: Баннер ожидания действия человека. Стиль: янтарный фон (`rgba(224, 192, 96, 0.12)`), янтарный бордер, анимация пульсации (`gl-wait-pulse`).
- `.gl-waiting-icon`, `.gl-waiting-text`: Стили для содержимого баннера.

**Новый UI-элемент:**

- Баннер ожидания: Рендерится при `status === "waiting_human"`. Содержит иконку `⏸` и текст:
  - Для `block_reason === "auth_required"`: "Требуется авторизация в окне Chrome".
  - Иначе: "Требуется решение капчи или обход блока".

#### 6. `Profiles.jsx` (НОВАЯ СТРАНИЦА)

**Состояние (State):**

- `profiles`: Массив объектов `SiteProfile`.
- `loading`: Флаг загрузки.
- `error`: Текст ошибки.
- `rescanning`: `Set` ID профилей, которые сейчас пересканируются.

**Логика:**

- `loadProfiles()`: Вызывает `fetchProfiles()`, обновляет `profiles`.
- `handleRescan(profileId)`: Вызывает `rescanProfile(profileId)`, добавляет `profileId` в `rescanning`. При успехе делает `navigate(/logs/${result.job_id})` для слежения за прогрессом. В `finally` удаляет `profileId` из `rescanning`.
- `handleDelete(profileId, domain, pathPrefix)`: Запрашивает подтверждение (`window.confirm`), вызывает `deleteProfile(profileId)`, перезагружает список.

**UI:**

- Шапка: Kicker "profiles · управление сайтами", заголовок "Профили сайтов", lead-текст о назначении профилей.
- Тулбар: Кнопка "↻ обновить" для перезагрузки списка.
- Таблица профилей:
  - Колонки: ID, Домен, Путь, Версия, Статус, Ошибок, Обновлено, Действия.
  - Статус: Бейдж с цветом:
    - `is_active=true` → "Активен" (бирюзовый).
    - `is_active=false` + `retry_not_before > now` → "На кулдауне" (янтарный).
    - `is_active=false` + кулдаун прошёл → "Сломан" (красный).
  - Действия:
    - Кнопка "Пересканировать" (янтарная): Запускает rescan, показывает "⟳…" при загрузке.
    - Кнопка "Удалить" (серая): Удаляет профиль после подтверждения.

**CSS (префикс `gx-`):**

- `.gx-page`, `.gx-head`, `.gx-kicker`, `.gx-title`, `.gx-lead`: Стили шапки.
- `.gx-toolbar`, `.gx-refresh`: Тулбар с кнопкой обновления.
- `.gx-table-wrap`, `.gx-table`, `.gx-cell`: Таблица профилей.
- `.gx-badge`, `.gx-badge--active`, `.gx-badge--broken`, `.gx-badge--cooldown`: Бейджи статуса.
- `.gx-btn`, `.gx-btn-rescan`, `.gx-btn-delete`: Кнопки действий.

#### 7. `Results.jsx`

Без изменений.

### Утилитарные функции компонентов React (Helpers)

Без изменений.

## Дизайн-система и Визуальный Стиль

Без изменений. Все новые элементы используют существующие CSS-переменные (`--gt-amber`, `--gt-teal`, `--gt-ink`, `--gt-line`) и префиксы (`gd-`, `gl-`, `gx-`).

## Среда разработки и Контекст для ИИ-агентов (Dev Environment & Rules)

### 1. Как запускать проект (Локальная среда)

Для полноценной работы проекта нужны **три терминала** (для Chrome, бекенда и фронтенда соответственно).

#### Терминал 1: Chrome с CDP (обязательно для парсинга)

```bash
cd backend
./start_chrome.command
```

Это запустит изолированный Chrome с портом отладки `9222`. **Держите окно открытым** — парсер подключается к нему через CDP.

#### Терминал 2: Backend (Flask)

```bash
cd backend

# Создаём виртуальное окружение (если ещё нет)
python -m venv venv
source venv/bin/activate  # На Windows: venv\Scripts\activate

# Устанавливаем зависимости
pip install -r requirements.txt

# Создаём .env файл (если ещё нет)
cp .env.example .env

# Редактируем .env (добавляем LLM_API_KEY для анализа новых сайтов)
nano .env  # Или любой другой редактор

# Запускаем бекенд
python app.py
```

Бекенд запустится на `http://localhost:5000`.

**Что происходит при старте:**

1. Загружается `.env` через `python-dotenv`.
2. Создаются таблицы в SQLite (`db.create_all()`).
3. Выполняется lightweight-миграция (добавление новых колонок в `jobs`).
4. Создаётся seed-профиль F6S (если его нет).
5. Зависшие задачи (`PENDING`, `RUNNING`, `WAITING_HUMAN`) помечаются как `FAILED`.

#### Терминал 3: Frontend (Vite + React)

```bash
cd frontend

# Устанавливаем зависимости (если ещё нет)
npm install

# Запускаем dev-сервер
npm run dev
```

Фронтенд запустится на `http://localhost:5173`.

### 2. Настройка `.env`

Создайте файл `backend/.env` (или скопируйте `backend/.env.example`):

```env
# =========================================
# Grantum Parser local environment
# Этот файл НЕ коммитить в git
# =========================================

# home | vps
SERVER_LOCATION=home

# Адрес CDP-браузера
CDP_URL=http://localhost:9222

# Telegram notifier (опционально)
# Если пусто — уведомления выключены, но система работает
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Используется только при SERVER_LOCATION=vps
PUBLIC_BROWSER_URL=

# Сколько ждать действия человека при капче/авторизации (секунды)
HUMAN_WAIT_SECONDS=600

# LLM для analyzer (обязательно для анализа новых сайтов)
# Без этого парсер будет работать только с seed-профилем F6S
LLM_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini
LLM_BASE_URL=https://api.openai.com/v1

# Analyzer limits
ANALYZER_MAX_DOM_CHARS=200000
PROFILE_RESCAN_COOLDOWN_SECONDS=3600

# Опционально
# DATABASE_URI=sqlite:///db.sqlite3
```

**Важно:**

- `LLM_API_KEY` обязателен для анализа новых сайтов. Без него парсер будет работать только с seed-профилем F6S.
- `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID` опциональны. Если пустые — уведомления выключены, но система продолжает работать (ждёт человека в Chrome, просто без Telegram).
- **Никогда не коммитьте `.env` в git.** Добавьте его в `.gitignore`.

### 3. Полный сценарий первого запуска

```bash
# 1. Клонируем репозиторий (если ещё нет)
git clone <repo-url>
cd grantum-parser

# 2. Запускаем Chrome
cd backend
./start_chrome.command
# Держим окно Chrome открытым

# 3. В новом терминале настраиваем бекенд
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .
```
