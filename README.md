# Grantum Parser — Universal Edition

Детальнейшая документация по проекту Grantum Parser, описывающая архитектуру, структуру директорий, каждый файл, функции и эндпоинты как для бекенда, так и для фронтенда. Данная документация предназначена для полного погружения в проект и возможности бесшовной интеграции нового кода.

**Версия:** Universal Parser Edition (Фазы 1-6 завершены)

## Что нового в Universal Edition

Проект эволюционировал из F6S-специфичного парсера в универсальный движок:
- **Автоматический анализ сайтов**: LLM анализирует любую страницу и создаёт JSON-инструкцию (профиль сайта)
- **Самолечение**: При смене вёрстки система автоматически пересканирует профиль
- **Human-in-the-loop**: Telegram-уведомления при капче/авторизации
- **Кэширование**: Повторный запуск по тому же сайту НЕ вызывает LLM
- **Управление профилями**: Страница `/profiles` для просмотра, пересканирования и удаления
- **Два режима парсинга**: Быстрый (`fast`) — только карточки листинга; Умный (`smart`) — дополнительно переходит в detail-страницы карточек и извлекает их содержимое
- **Дочерние профили (ChildProfile)**: В умном режиме селекторы detail-страниц сохраняются отдельно и привязываются к родительскому профилю листинга. При повторных заходах на однотипные detail-страницы профиль переиспользуется, LLM вызывается только для незнакомых структур.
- **Пул LLM API-ключей с ротацией**: Автоматическое переключение между несколькими API-ключами при достижении лимитов (RPM, TPM, дневной лимит).

---

## Режимы парсинга и дочерние профили (ChildProfile)

### Быстрый режим (`fast`)
- Извлекает только карточки листинга по инструкции родительского `SiteProfile`.
- По ссылкам карточек не переходит.
- Обрабатывает заданное пользователем количество страниц/итераций.

### Умный режим (`smart`)
- Извлекает карточки листинга, как в быстром режиме.
- Для каждой карточки берёт URL detail-страницы и переходит по нему.
- Пытается найти подходящий дочерний профиль (`ChildProfile`) для структуры detail-страницы:
  - если найден — загружает его JSON-инструкцию, LLM не вызывается;
  - если не найден — вызывает LLM для анализа detail-страницы, сохраняет новую инструкцию в базу как `ChildProfile`, привязанный к родительскому профилю (`parent_profile_id`), и использует её.
- Для всех страниц с одинаковым URL-шаблоном (например `/item?id=123` и `/item?id=456` → `path_prefix=/item`) создаётся один дочерний профиль, а не по одному на каждую карточку.

**Архитектура привязки:**
```
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
- "анализ страницы"
- "запуск нейро анализа страницы"

Эти сообщения появляются в следующих случаях:
- первичный анализ неизвестного сайта при создании `SiteProfile`;
- повторный анализ неактивного профиля после окончания кулдауна;
- анализ неизвестной detail-структуры при создании `ChildProfile`.

Обычная обработка карточки без вызова LLM таких сообщений не создаёт.

---

## Архитектура проекта

Проект разделен на две основные части:
- **Backend (Python / Flask / SQLAlchemy / Playwright / OpenAI API)** — отвечает за управление задачами (Jobs), анализ сайтов через LLM, запуск парсера через headless/CDP Chrome, сохранение результатов в SQLite и выдачу данных по REST API.
- **Frontend (React / Vite / React Router)** — пользовательский интерфейс (SPA), предоставляющий функционал запуска парсинга, просмотра логов в реальном времени, выгрузки спарщенных результатов в CSV и управления профилями сайтов.

---

## Backend

Бекенд написан на Python с использованием микрофреймворка Flask. Для работы с базой данных (SQLite) используется SQLAlchemy. Парсинг осуществляется с помощью Playwright, который подключается к существующему экземпляру Chrome по протоколу CDP (Chrome DevTools Protocol). Анализ сайтов выполняется через OpenAI-совместимый API (GPT-4o-mini или аналоги).

### Структура директории `backend/`

#### 1. `config.py` (ИЗМЕНЁННЫЙ)

Модуль централизованной конфигурации через переменные окружения.

**Функции:**
- `_load_env_file()`: Загружает `.env` через `python-dotenv` (приоритет: `backend/.env` → `.env` в cwd). `override=False`, чтобы системные переменные имели приоритет.
- `_get_str(key, default, empty_as_default)`: Читает строковую переменную с опциональной обработкой пустых значений.
- `_get_int(key, default)`: Читает целочисленную переменную с fallback на default при ошибке парсинга.
- `_get_str_list(key, default)`: Читает список строк через запятую/точку с запятой.

**Переменные конфигурации:**
- `SERVER_LOCATION`: `"home"` | `"vps"` (дефолт `"home"`). Влияет на текст Telegram-уведомлений.
- `CDP_URL`: Адрес CDP-браузера (дефолт `"http://localhost:9222"`).
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`: Для notifier. Если пустые — уведомления выключены.
- `PUBLIC_BROWSER_URL`: Используется только при `SERVER_LOCATION=vps` (ссылка на noVNC).
- `HUMAN_WAIT_SECONDS`: Сколько ждать действия человека при капче/авторизации (дефолт `600`).
- `LLM_API_KEYS`: Список API-ключей через запятую для пула ключей с ротацией (дефолт пустой). **Рекомендуемый способ**.
- `LLM_API_KEY`: Одиночный API-ключ (устарело, оставлено для обратной совместимости). Игнорируется, если задан `LLM_API_KEYS`.
- `LLM_MODEL`: Модель LLM (дефолт `"gpt-4o-mini"`).
- `LLM_BASE_URL`: Базовый URL API (дефолт `"https://api.openai.com/v1"`).
- `LLM_RPM_LIMIT`: Лимит запросов в минуту на один ключ (дефолт `15`).
- `LLM_TPM_LIMIT`: Лимит токенов в минуту на один ключ (дефолт `250000`).
- `LLM_DAILY_REQUEST_LIMIT`: Лимит запросов в день на один ключ (дефолт `500`).
- `LLM_KEY_WAIT_SECONDS`: Сколько секунд ждать освобождения ключа при временных лимитах (дефолт `120`).
- `ANALYZER_MAX_DOM_CHARS`: Лимит размера DOM для analyzer (дефолт `200000`).
- `PROFILE_RESCAN_COOLDOWN_SECONDS`: Кулдаун после неудачного пересканирования (дефолт `3600`).
- `SMART_MAX_DETAIL_PAGES`: Резервное значение максимума переходов на detail-страницы карточек за запуск (дефолт `100`).
- `SMART_MAX_NEW_CHILD_PROFILES`: Резервное значение максимума новых дочерних профилей (вызовов LLM) за запуск (дефолт `20`).
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
- `normalize_target_url(raw_url)`: Нормализует URL пользователя.
- `find_best_profile(normalized)`: Ищет лучший `SiteProfile` для нормализованного URL.
- `find_best_profile_for_url(raw_url)`: Удобная обёртка.

#### 3. `seed_profiles.py` (НОВЫЙ)

Модуль для создания seed-профилей известных сайтов при старте приложения.

**Константы:**
- `F6S_PROGRAMS_INSTRUCTION`: JSON-инструкция для `f6s.com/programs`.

**Функции:**
- `ensure_f6s_seed_profile()`: Идемпотентно создаёт seed-профиль F6S, если его нет в БД.

#### 4. `llm_key_pool.py` (НОВЫЙ)

Менеджер пула LLM API-ключей с ротацией и учётом лимитов.

**Назначение:**
Отвечает за автоматическое переключение между несколькими API-ключами при достижении лимитов:
- дневной лимит запросов на ключ (500/день по умолчанию);
- минутный лимит запросов (15 RPM по умолчанию);
- минутный лимит токенов (250k TPM по умолчанию);
- временные cooldown для минутных лимитов;
- блокировка ключа до следующего UTC-дня при дневном лимите;
- блокировка ключа при auth/billing ошибках.

**Архитектура:**
- Дневные счётчики хранятся в SQLite-таблице `llm_key_pool_state`.
- Минутные лимиты хранятся в памяти процесса через скользящее окно 60 секунд.
- Ключи в БД не хранятся. Хранятся только `key_hash` (SHA-256) и маска вида `sk-abc...xyz`.

**Исключения:**
- `LlmKeyPoolError`: Базовая ошибка пула ключей.
- `LlmNoAvailableKeyError`: Нет доступного ключа прямо сейчас (все в cooldown или отключены).
- `LlmDailyLimitError`: Все ключи исчерпали дневной лимит или заблокированы.

**Классы:**
- `KeyReservation`: Резервирование ключа на время запроса. Поля: `key`, `key_hash`, `estimated_tokens`, `started_at`, `finished`.

**Функции:**
- `has_keys()`: Возвращает `True`, если `LLM_API_KEYS` не пустой.
- `acquire_key(estimated_tokens, wait_timeout)`: Выдаёт доступный ключ с учётом всех лимитов. Если все ключи временно в rate limit — ждёт до `wait_timeout`. Блокировки:
  - дневной счётчик < `LLM_DAILY_REQUEST_LIMIT`;
  - нет дневной блокировки;
  - нет активного минутного cooldown;
  - RPM < `LLM_RPM_LIMIT`;
  - TPM < `LLM_TPM_LIMIT`.
- `finish_success(reservation, tokens)`: Завершает успешный запрос, обновляет счётчик токенов.
- `finish_failure(reservation)`: Завершает неуспешный запрос без пометки ключа как сломанного.
- `mark_rate_limit(reservation, kind, retry_after, message)`: Помечает ключ как временно ограниченный.
  - `kind="rpm"` или `"tpm"`: короткий cooldown 5–60 секунд.
  - `kind="daily"`: блокировка до 00:00 UTC.
  - `kind="auth"`: постоянная блокировка ключа.
- `mark_auth(reservation, message)`: Помечает ключ как невалидный (401/402/403).

**Логика ротации:**
1. Перед запросом `llm_client.chat_json` оценивает примерное потребление токенов.
2. `acquire_key()` выбирает первый доступный ключ.
3. Если все ключи упёрлись только в минутные лимиты — ждёт до `LLM_KEY_WAIT_SECONDS`.
4. Если все ключи упёрлись в дневной лимит — поднимается `LlmDailyLimitError`.
5. Если пришёл 429 от провайдера:
   - RPM/TPM → временный cooldown, ключ не считается сломанным;
   - daily/quota/insufficient → ключ блокируется до следующего UTC-дня;
   - 401/402/403 → ключ блокируется как невалидный.

**Важное ограничение:**
Минутные лимиты в памяти будут корректно работать только в рамках одного процесса бекенда. Если запустишь несколько worker-процессов, для RPM/TPM понадобится общий storage, например Redis.

#### 5. `llm_client.py` (ИЗМЕНЁННЫЙ)

Тонкий клиент для OpenAI-совместимого API (без SDK) с поддержкой пула ключей.

**Исключения:**
- `LlmError`: Базовая ошибка LLM-клиента.
- `LlmDisabledError`: LLM не настроен (нет API-ключей).
- `LlmResponseError`: LLM вернул невалидный JSON или неожиданную структуру.
- `LlmHttpError`: Сетевая/HTTP-ошибка при обращении к LLM.
- `LlmRateLimitError`: Rate limit от LLM-провайдера (базовый класс).
- `LlmRpmLimitError`: Лимит запросов в минуту (HTTP 429 с маркерами RPM).
- `LlmTpmLimitError`: Лимит токенов в минуту (HTTP 429 с маркерами TPM).
- `LlmDailyLimitError`: Дневной лимит или quota (HTTP 429 с маркерами daily/quota).
- `LlmAuthError`: Ключ отклонён: auth, billing, invalid key (HTTP 401/402/403).
- `LlmNoAvailableKeyError`: Ни один LLM-ключ сейчас недоступен.

**Функции:**
- `is_enabled()`: Возвращает `True`, если `LLM_API_KEYS` не пустой.
- `_build_url()`: Строит URL `{LLM_BASE_URL}/chat/completions`.
- `_build_headers(api_key)`: Возвращает `{"Authorization": "Bearer {api_key}", "Content-Type": "application/json"}`.
- `_strip_markdown_fence(raw)`: Убирает ```json ... ``` обёртку, если LLM её добавил.
- `_estimate_tokens(system_prompt, user_prompt, max_tokens)`: Оценивает потребление токенов для резервирования ключа.
- `_parse_retry_after(response)`: Парсит заголовок `Retry-After` из HTTP-ответа.
- `_classify_rate_limit(response)`: Классифицирует тип rate limit (rpm/tpm/daily) по тексту ошибки и маркерам.
- `_extract_usage_tokens(data, estimated_tokens)`: Извлекает фактическое потребление токенов из `response.usage`.
- `chat_json(system_prompt, user_prompt, temperature=0.2, max_tokens=None, timeout=120)`:
  - Вызов LLM с ожиданием JSON-ответа.
  - Использует пул ключей через `llm_key_pool.acquire_key()`.
  - При HTTP 429 классифицирует ошибку и помечает ключ через `mark_rate_limit()`.
  - При HTTP 401/402/403 помечает ключ как невалидный через `mark_auth()`.
  - При успехе обновляет счётчик токенов через `finish_success()`.
  - Payload: `model`, `temperature`, `messages` (system + user), `response_format: {"type": "json_object"}`.
  - Парсит `data["choices"][0]["message"]["content"]`, убирает markdown-обёртку, возвращает `dict`.

**Логика retry:**
- Максимум попыток: `max(3, количество_ключей * 2)`.
- При временных ошибках (429 RPM/TPM, 5xx) автоматически пробует следующий доступный ключ.
- При дневных лимитах (429 daily/quota) поднимается `LlmDailyLimitError` без дальнейших попыток.
- При auth ошибках (401/402/403) ключ блокируется, пробует следующий ключ.

#### 6. `analyzer.py` (НОВЫЙ)

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
- `_clean_element_html(html)`: Базовая очистка HTML.
- `_extract_candidate_blocks(page, max_blocks=5)`: Эвристика повторяющихся блоков.
- `_capture_cleaned_dom(page)`: Возвращает `(cleaned_dom, was_truncated)`.
- `_collect_page_signals(page, target_url, job_id, navigate=True)`: Загружает страницу, собирает сигналы.
- `_build_llm_prompts(normalized, signals)`: Строит system и user промпты для LLM.
- `_validate_instruction(instruction, normalized)`: Валидирует JSON-схему.
- `_smoke_test(page, instruction, job_id)`: Проверяет селекторы на живой странице.
- `analyze_and_create_profile(app, normalized, job_id)`: Главная точка входа. Сериализуется по `(domain, path_prefix)` (lock с таймаутом 180 сек).
- `regenerate_profile(app, profile, job_id, error_message)`: Перегенерирует инструкцию при структурном сбое.
- `mark_profile_failed(profile, error_message)`: Помечает профиль как неактивный.

**Detail-анализатор (умный режим / дочерние профили):**
- `_detail_system_prompt()`: Системный промпт для LLM при анализе detail-страницы.
- `_validate_detail_instruction(instruction, normalized)`: Валидирует detail-инструкцию.
- `_analyze_detail_page(page, normalized, job_id)`: Собирает сигналы, вызывает LLM.
- `analyze_and_create_child_profile(app, normalized, parent_profile_id, job_id, page)`: Создаёт `ChildProfile` для detail-страницы.

#### 7. `notifier.py` (НОВЫЙ)

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
- `_compose_message(job_ids, domain, reasons, urls)`: Собирает текст Telegram-сообщения.
- `_send_text(text)`: Отправка через `requests.post`.
- `notify_human_required(app, job_id, target_url, domain, block_reason)`: Вызывается executor'ом при переходе в `WAITING_HUMAN`.
- `mark_human_episode_finished(job_id)`: Вызывается при завершении эпизода.
- `_flush_domain(domain)`: Отложенная отправка агрегированного уведомления.

#### 8. `models.py` (ИЗМЕНЁННЫЙ)

**Добавления:**
- `JobStatus.WAITING_HUMAN`: Новый статус `"waiting_human"` (ожидание действия человека).

**SiteProfile (новая модель, таблица `site_profiles`):**
Поля: `id`, `domain` (индекс), `path_prefix`, `instructions_json` (TEXT), `previous_instructions_json` (TEXT NULL), `version` (INT), `is_active` (BOOL), `fail_count` (INT), `retry_not_before` (DATETIME NULL), `last_success_at`, `last_failure_at`, `last_error` (TEXT NULL), `created_at`, `updated_at`.

Уникальный составной индекс `uq_site_profiles_domain_path_prefix` по `(domain, path_prefix)`.

Метод `to_dict(include_instructions=False)`: Сериализует в словарь.

**Job (расширенная модель):**
Новые поля: `profile_id` (FK на `site_profiles`, `ondelete="SET NULL"`), `block_reason` (TEXT NULL), `human_requested_at` (DATETIME NULL).

Поля режимов парсинга: `parse_mode` (`"fast"` | `"smart"`, дефолт `"fast"`), `max_pages` (страницы/итерации, дефолт `1`), `max_child_profiles` (лимит новых detail-профилей за запуск, дефолт `20`), `max_detail_pages` (лимит переходов на detail-страницы, дефолт `100`).

Связь `profile = relationship("SiteProfile")`.

**ChildProfile (новая модель, таблица `child_profiles`):**
Поля: `id`, `parent_profile_id` (FK на `site_profiles`, `ondelete="CASCADE"`, индекс), `domain`, `path_prefix`, `instructions_json` (TEXT), `version` (INT), `is_active` (BOOL), `created_at`, `updated_at`.

Уникальный составной индекс `uq_child_profiles_parent_domain_path` по `(parent_profile_id, domain, path_prefix)`.

Связь `parent_profile = relationship("SiteProfile", backref="child_profiles")` с `cascade="all, delete-orphan"`.

**Функции без изменений:**
`add_log`, `set_job_status`, `save_parsed_item` — без изменений.

#### 9. `parser.py` (СИЛЬНО ИЗМЕНЁННЫЙ)

Ядро бизнес-логики — универсальный executor парсинга по JSON-инструкции из `SiteProfile`.

**Новые импорты:**
- `import json` (для парсинга `instructions_json`).
- `import analyzer` (для запуска анализатора).
- `import notifier` (для Telegram-уведомлений).
- `from url_utils import normalize_target_url, find_best_profile, normalize_path_prefix`.
- `from models import utcnow` (для `human_requested_at`).

**Новые константы:**
- `CLOUDFLARE_MARKERS`: Список маркеров Cloudflare.
- `DEFAULT_AUTH_MARKERS`: Дефолтные маркеры авторизации.

**Новые функции:**
- `_classify_block(page)`: Классифицирует тип блока.
- `_check_auth_required(page, instruction)`: Проверяет, требует ли страница авторизации.
- `_wait_human(app, page, job_id, target_url, domain, block_reason, instruction=None)`: Ожидание действия человека.
- `_validate_page(page, instruction, job_id)`: Валидирует страницу по правилам из инструкции.

**Новые функции (умный режим / дочерние профили):**
- `find_best_child_profile(parent_profile_id, domain, path)`: Ищет подходящий `ChildProfile` для detail-URL.
- `_extract_child_text(detail_page, instruction)`: Извлекает текст detail-страницы по схеме child-инструкции.

**Обработка detail-карточки в smart-режиме:**
- Открывается detail URL по ссылке карточки.
- `find_best_child_profile` ищет существующий дочерний профиль.
- Если найден — загружается его `instructions_json`, LLM не вызывается повторно.
- Если не найден — вызывается `analyzer.analyze_and_create_child_profile`.
- `_extract_child_text` извлекает detail-текст.

**Изменённая функция `run_universal_parser`:**
- Шаг 0: Чтение режима и лимитов из Job.
- Шаг 1: Нормализация и поиск профиля.
- Шаг 2: Если профиля нет или он неактивен.
- Шаг 3: Загрузка инструкции.
- Шаг 4: Классификация ошибок (фаза 4).
- Шаг 5: Исполнение.
- Шаг 6: Пагинация.
- Шаг 7: Успех.

**Функция `run_f6s_parser`:**
Сохранена как тонкая обёртка: просто вызывает `run_universal_parser`.

#### 10. `app.py` (ИЗМЕНЁННЫЙ)

**Новые импорты:**
- `import config` (вместо хардкода `SQLALCHEMY_DATABASE_URI`).
- `from models import SiteProfile` (для эндпоинтов профилей).
- `from url_utils import normalize_path_prefix` (для построения URL rescan).
- `import analyzer` (для запуска пересканирования).

**Изменения в инициализации:**
- `app.config["SQLALCHEMY_DATABASE_URI"]` берётся из `config.SQLALCHEMY_DATABASE_URI`.
- Добавлена функция `_migrate_sqlite_schema()`: Идемпотентная lightweight-миграция.
- `ensure_f6s_seed_profile()` вызывается после `db.create_all()`.
- Стартовая очистка расширяется: `JobStatus.WAITING_HUMAN` тоже помечается `FAILED` при старте сервера.

**Новые REST API Эндпоинты (Profiles API):**
- `GET /api/profiles`: Возвращает список всех профилей сайтов.
- `GET /api/profiles/<int:profile_id>`: Возвращает один профиль, включая `instructions_json`.
- `DELETE /api/profiles/<int:profile_id>`: Удаляет профиль сайта.
- `POST /api/profiles/<int:profile_id>/rescan`: Принудительное пересканирование профиля.

**Существующие эндпоинты без изменений:**
`GET /api/jobs`, `GET /api/jobs/<id>/logs`, `GET /api/items`, `DELETE /api/jobs`, `DELETE /api/jobs/<id>`.

**POST /api/parse (ИЗМЕНЁН):**
Описание: Создаёт задачу и запускает парсер. Принимает JSON `{url, iterations, mode, maxChildProfiles}`.

#### 11. `save_auth.py`
Без изменений.

#### 12. `start_chrome.command`
Без изменений.

---

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

**Изменённая `startParse(url, options = {})`:**
Теперь принимает второй аргумент `options`:
- `options.iterations` → `iterations` (JSON); дефолт `1`.
- `options.mode` → `mode` (JSON); дефолт `"fast"`.
- `options.maxChildProfiles` → `maxChildProfiles` (JSON); дефолт `20`.

#### Папка `pages/`

##### 4. `Dashboard.jsx` (ИЗМЕНЁННЫЙ)
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
- Переключатель «Режим»: радио «Быстрый» (`fast`) и «Умный» (`smart`).

**Новые CSS-классы:** `.gd-options`, `.gd-option-field`, `.gd-mode-field`, `.gd-number-input`, `.gd-mode-choice`, `.gd-mode-help`.

**Изменения в `STATUS_META`:**
- Добавлен статус `waiting_human`: `{label: "Ожидание", dot: "gd-dot--waiting"}`.

##### 5. `LogsPage.jsx` (ИЗМЕНЁННЫЙ)
**Изменения в `STATUS_META`:**
- Добавлен статус `waiting_human`: `{label: "Ожидание", dot: "gl-dot--waiting"}`.

**Новые CSS-классы:**
- `.gl-dot--waiting`: Янтарная точка с пульсацией (`gl-pulse`).
- `.gl-statusbar--waiting_human`: Янтарный бордер статус-бара.
- `.gl-waiting-banner`: Баннер ожидания действия человека.
- `.gl-waiting-icon`, `.gl-waiting-text`: Стили для содержимого баннера.

**Новый UI-элемент:**
Баннер ожидания: Рендерится при `status === "waiting_human"`. Содержит иконку `⏸` и текст:
- Для `block_reason === "auth_required"`: "Требуется авторизация в окне Chrome".
- Иначе: "Требуется решение капчи или обход блока".

##### 6. `Profiles.jsx` (НОВАЯ СТРАНИЦА)
**Состояние (State):**
- `profiles`: Массив объектов `SiteProfile`.
- `loading`: Флаг загрузки.
- `error`: Текст ошибки.
- `rescanning`: `Set` ID профилей, которые сейчас пересканируются.

**Логика:**
- `loadProfiles()`: Вызывает `fetchProfiles()`, обновляет `profiles`.
- `handleRescan(profileId)`: Вызывает `rescanProfile(profileId)`, добавляет `profileId` в `rescanning`.
- `handleDelete(profileId, domain, pathPrefix)`: Запрашивает подтверждение, вызывает `deleteProfile(profileId)`, перезагружает список.

**UI:**
- Шапка: Kicker "profiles · управление сайтами", заголовок "Профили сайтов".
- Тулбар: Кнопка "↻ обновить" для перезагрузки списка.
- Таблица профилей с колонками: ID, Домен, Путь, Версия, Статус, Ошибок, Обновлено, Действия.

**CSS (префикс `gx-`):**
- `.gx-page`, `.gx-head`, `.gx-kicker`, `.gx-title`, `.gx-lead`: Стили шапки.
- `.gx-toolbar`, `.gx-refresh`: Тулбар с кнопкой обновления.
- `.gx-table-wrap`, `.gx-table`, `.gx-cell`: Таблица профилей.
- `.gx-badge`, `.gx-badge--active`, `.gx-badge--broken`, `.gx-badge--cooldown`: Бейджи статуса.
- `.gx-btn`, `.gx-btn-rescan`, `.gx-btn-delete`: Кнопки действий.

##### 7. `Results.jsx`
Без изменений.

### Утилитарные функции компонентов React (Helpers)
Без изменений.

### Дизайн-система и Визуальный Стиль
Без изменений. Все новые элементы используют существующие CSS-переменные (`--gt-amber`, `--gt-teal`, `--gt-ink`, `--gt-line`) и префиксы (`gd-`, `gl-`, `gx-`).

---

## Среда разработки и Контекст для ИИ-агентов (Dev Environment & Rules)

### 1. Как запускать проект (Локальная среда)

Для полноценной работы проекта нужны три терминала (для Chrome, бекенда и фронтенда соответственно).

**Терминал 1: Chrome с CDP (обязательно для парсинга)**
```bash
cd backend
./start_chrome.command
```
Это запустит изолированный Chrome с портом отладки `9222`. Держите окно открытым — парсер подключается к нему через CDP.

**Терминал 2: Backend (Flask)**
```bash
cd backend

# Создаём виртуальное окружение (если ещё нет)
python -m venv venv
source venv/bin/activate  # На Windows: venv\Scripts\activate

# Устанавливаем зависимости
pip install -r requirements.txt

# Создаём .env файл (если ещё нет)
cp .env.example .env

# Редактируем .env (добавляем LLM_API_KEYS для анализа новых сайтов)
nano .env  # Или любой другой редактор

# Запускаем бекенд
python app.py
```

Бекенд запустится на `http://localhost:5000`.

**Что происходит при старте:**
- Загружается `.env` через `python-dotenv`.
- Создаются таблицы в SQLite (`db.create_all()`).
- Выполняется lightweight-миграция (добавление новых колонок в `jobs`).
- Создаётся seed-профиль F6S (если его нет).
- Зависшие задачи (`PENDING`, `RUNNING`, `WAITING_HUMAN`) помечаются как `FAILED`.

**Терминал 3: Frontend (Vite + React)**
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

# =========================================
# LLM для analyzer (обязательно для анализа новых сайтов)
# =========================================
#
# Пул API-ключей (рекомендуется):
# Перечисли ключи через запятую. Если первый ключ упрётся
# в дневной лимит, приложение автоматически возьмёт следующий.
# Минутные лимиты (RPM/TPM) держатся в памяти процесса.
# При 429 от провайдера ключ получает временный cooldown.
LLM_API_KEYS=sk-first,sk-second,sk-third

# Одиночный ключ (устарело, оставлено для обратной совместимости).
# Если LLM_API_KEYS задан, этот параметр игнорируется.
LLM_API_KEY=

LLM_MODEL=gpt-4o-mini
LLM_BASE_URL=https://api.openai.com/v1

# Лимиты для ОДНОГО LLM-ключа.
# При достижении RPM/TPM ключ уходит в короткий cooldown (5–60 сек).
# При достижении DAILY ключ блокируется до 00:00 UTC и включается следующий.
# 15 запросов в минуту
LLM_RPM_LIMIT=15
# 250 000 токенов в минуту
LLM_TPM_LIMIT=250000
# 500 запросов в день
LLM_DAILY_REQUEST_LIMIT=500

# Сколько секунд ждать освобождения ключа, если все ключи
# временно упёрлись в минутные лимиты. По умолчанию 120.
LLM_KEY_WAIT_SECONDS=120

# Analyzer limits
ANALYZER_MAX_DOM_CHARS=200000
PROFILE_RESCAN_COOLDOWN_SECONDS=3600

# Опционально
# DATABASE_URI=sqlite:///db.sqlite3
```

**Важно:**
- `LLM_API_KEYS` или `LLM_API_KEY` обязательны для анализа новых сайтов. Без них парсер будет работать только с seed-профилем F6S.
- Рекомендуется использовать `LLM_API_KEYS` с несколькими ключами для ротации при достижении лимитов.
- Минутные лимиты (RPM/TPM) хранятся в памяти процесса. Если запустишь несколько worker-процессов, понадобится общий storage (например Redis).
- `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID` опциональны. Если пустые — уведомления выключены, но система продолжает работать.
- Никогда не коммитьте `.env` в git. Добавьте его в `.gitignore`.

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
nano .env  # Добавляем LLM_API_KEYS

# 4. Запускаем бекенд
python app.py

# 5. В третьем терминале настраиваем фронтенд
cd frontend
npm install
npm run dev
```

Открывай `http://localhost:5173` в браузере — и вперёд парсить гранты!

---

## Пул LLM API-ключей (детали реализации)

### Как работает ротация ключей

1. **Перед запросом**: `llm_client.chat_json` оценивает примерное потребление токенов через `_estimate_tokens()`.

2. **Выбор ключа**: `llm_key_pool.acquire_key()` выбирает первый доступный ключ, у которого:
   - дневной счётчик < `LLM_DAILY_REQUEST_LIMIT` (500 по умолчанию);
   - нет дневной блокировки;
   - нет активного минутного cooldown;
   - RPM < `LLM_RPM_LIMIT` (15 по умолчанию);
   - TPM позволяет запрос (< `LLM_TPM_LIMIT`, 250k по умолчанию).

3. **Если все ключи упёрлись в минутные лимиты**: клиент ждёт до `LLM_KEY_WAIT_SECONDS` (120 по умолчанию), периодически проверяя доступность ключей.

4. **Если все ключи упёрлись в дневной лимит**: поднимается `LlmDailyLimitError` без дальнейших попыток.

5. **При HTTP 429 от провайдера**:
   - **RPM/TPM** (временный лимит): ключ получает короткий cooldown 5–60 секунд, не считается сломанным.
   - **daily/quota/insufficient** (дневной лимит): ключ блокируется до 00:00 UTC, включается следующий ключ.
   - **401/402/403** (auth/billing): ключ блокируется как невалидный навсегда, включается следующий ключ.

### Хранение состояния

**Дневные счётчики** (в БД, таблица `llm_key_pool_state`):
- `key_hash`: SHA-256 хеш ключа (первые 32 символа).
- `key_mask`: Маскированный вид ключа (`sk-abc...xyz`).
- `daily_date`: Текущая UTC-дата.
- `daily_requests`: Количество запросов за сегодня.
- `disabled_until`: Timestamp блокировки (0 если не заблокирован).
- `last_error`: Текст последней ошибки.
- `updated_at`: Timestamp последнего обновления.

**Минутные лимиты** (в памяти процесса):
- Скользящее окно 60 секунд для RPM (список timestamps запросов).
- Скользящее окно 60 секунд для TPM (список пар `(timestamp, tokens)`).
- `cooldown_until`: Timestamp временной блокировки.
- `pending_tokens`: Зарезервированные токены для активных запросов.

### Логирование и отладка

При срабатывании лимитов в логах появляются сообщения:
- `"LLM вернул HTTP 429 (лимит запросов в минуту)"` — RPM limit.
- `"LLM вернул HTTP 429 (лимит токенов в минуту)"` — TPM limit.
- `"LLM вернул HTTP 429 (дневной лимит/quota)"` — daily limit.
- `"LLM ключ отклонён: HTTP 401"` — auth error.

Для отладки можно добавить логирование в `llm_key_pool.acquire_key()` для отслеживания выбора ключей.

