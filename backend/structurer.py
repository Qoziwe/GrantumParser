"""
Structurer — модуль AI-структурирования карточек.

Поток:
1. Парсер сохраняет ParsedItem с structuring_status="pending".
2. Фоновый воркер (StructuringWorker) подбирает батчи pending-карточек.
3. Батч отправляется в LLM, который возвращает структурированный JSON
   для каждой карточки.
4. При невалидном JSON — автоматический ретрай с описанием ошибки.
5. Результат записывается в structured_data, статус обновляется.

Парсер НЕ ждёт завершения структурирования — работает параллельно.
"""

import json
import threading
import time
import traceback
from typing import List, Optional, Tuple

import config
import llm_client
from models import db, ParsedItem, StructuringStatus, LogLevel


def _add_log(job_id, level, message):
    """Логирование через БД (как в parser.py)."""
    try:
        from models import Log
        db.session.add(Log(job_id=job_id, level=level, message=str(message)))
        db.session.commit()
    except Exception:
        db.session.rollback()


# Все поля, которые LLM должен заполнить для каждой карточки.
STRUCTURED_FIELDS_DESCRIPTION = """\
Для КАЖДОЙ карточки заполни следующие поля (если информация отсутствует — ставь null).
ВНИМАНИЕ: Для полей с указанным списком вариантов, ты ОБЯЗАН выбрать только значения из этого списка.

- title (string): чистое название мероприятия/гранта/программы
- description (string): краткое описание, 2-3 предложения
- full_description (string): полное описание, все детали
- category (string): строго одна из: "grant", "accelerator", "hackathon", "competition", "fellowship", "event", "course", "incubator", "scholarship", "other"
- tags (array[string]): теги, максимум 10
- organizer (string): организатор/компания/фонд
- location (string): место проведения или "online"
- country (string): строго одно значение из списка. Для международных программ: "Global", "Europe", "CIS", "MENA", "LATAM", "APAC". Страны: "Russia", "USA", "UK", "Germany", "France", "China", "India", "UAE", "Kazakhstan", "Belarus", "Uzbekistan", "Israel", "Singapore", "Canada", "Australia", "Other"
- deadline (string): дедлайн подачи (дата или текст типа "Rolling" / "Ongoing")
- start_date (string): дата начала
- end_date (string): дата окончания
- funding_amount (string): размер финансирования/приза (текстом, как написано)
- currency (string): строго из списка: "USD", "EUR", "RUB", "GBP", "KZT", "BYN", "CNY", "AED", "SAR", "Other"
- eligibility (string): кто может участвовать
- requirements (array[string]): список требований к участникам
- benefits (array[string]): что получает участник (помимо денег)
- application_url (string): ссылка на подачу заявки (если есть в тексте)
- stage (string): строго стадия стартапа: "idea", "mvp", "early", "growth", "any" или null
- industry (array[string]): строго из списка: "AI/ML", "Web3/Crypto", "FinTech", "EdTech", "MedTech/HealthTech", "AgroTech", "BioTech", "E-commerce/Retail", "SaaS/Enterprise", "Hardware/IoT", "GameDev", "SpaceTech", "DeepTech", "GreenTech/Cleantech", "Logistics/Mobility", "Cybersecurity", "AR/VR", "Social Impact", "Media/Entertainment", "PropTech/Real Estate", "HRTech", "LegalTech", "FoodTech", "Creative Industries", "Any/Agnostic", "Other"
- language (string): строго из списка: "RU", "EN", "ES", "FR", "DE", "ZH", "AR", "Other"
- is_free (boolean): бесплатно ли участие
- confidence_score (float 0.0-1.0): твоя уверенность в качестве извлечённых данных"""


SYSTEM_PROMPT = (
    "Ты — AI-ассистент для структурирования информации о грантах, "
    "акселераторах, хакатонах и стартап-программах.\n\n"
    "Тебе дан батч из нескольких карточек. Каждая карточка содержит "
    "сырой текст, собранный парсером со страницы.\n\n"
    "Твоя задача — извлечь из текста структурированную информацию "
    "и вернуть JSON.\n\n"
    "ТРЕБОВАНИЯ:\n"
    "1. Ответ СТРОГО в формате JSON без markdown-обёрток.\n"
    "2. Корневой объект должен содержать ключ \"items\" — массив объектов.\n"
    "3. Каждый объект в \"items\" соответствует одной карточке из входных данных.\n"
    "4. Порядок объектов в \"items\" СТРОГО совпадает с порядком входных карточек.\n"
    "5. Количество объектов в \"items\" РАВНО количеству входных карточек.\n"
    "6. Не придумывай информацию, которой нет в тексте — ставь null.\n"
    "7. Если текст на другом языке — переводить НЕ нужно, оставляй как есть.\n\n"
    f"{STRUCTURED_FIELDS_DESCRIPTION}\n\n"
    "Формат ответа:\n"
    "{\n"
    '  "items": [\n'
    "    { ... поля карточки 1 ... },\n"
    "    { ... поля карточки 2 ... },\n"
    "    ...\n"
    "  ]\n"
    "}"
)


def _build_user_prompt(batch: List[dict]) -> str:
    """
    Формирует пользовательский промпт из батча карточек.

    batch — список словарей с ключами: item_id, title, raw_text, url.
    """
    parts = []
    for i, card in enumerate(batch, start=1):
        parts.append(
            f"=== КАРТОЧКА {i} ===\n"
            f"ID: {card['item_id']}\n"
            f"Заголовок: {card['title']}\n"
            f"URL: {card['url']}\n"
            f"Текст:\n{card['raw_text']}\n"
        )
    return "\n".join(parts)


def _validate_llm_response(data: dict, expected_count: int) -> Tuple[bool, str]:
    """
    Валидирует структуру ответа LLM.

    Возвращает (is_valid, error_message).
    """
    if not isinstance(data, dict):
        return False, "Ответ не является JSON-объектом."

    items = data.get("items")
    if not isinstance(items, list):
        return False, 'Ответ не содержит ключ "items" или это не массив.'

    if len(items) != expected_count:
        return False, (
            f"Ожидалось {expected_count} элементов в items, "
            f"получено {len(items)}."
        )

    required_keys = {"title", "category", "confidence_score"}
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            return False, f"Элемент items[{i}] не является объектом."
        missing = required_keys - set(item.keys())
        if missing:
            return False, (
                f"Элемент items[{i}] не содержит обязательных полей: "
                f"{', '.join(sorted(missing))}."
            )

    return True, ""


def structurize_batch(
    batch: List[dict],
    job_id: int,
    max_retries: int = 2,
) -> List[Optional[dict]]:
    """
    Обрабатывает батч карточек через LLM.

    batch — список словарей: {item_id, title, raw_text, url}.
    Возвращает список dict (по одному на карточку) или None при полном провале.

    При невалидном JSON делает ретрай с описанием ошибки.
    """
    if not batch:
        return []

    user_prompt = _build_user_prompt(batch)
    expected_count = len(batch)

    last_error = ""

    for attempt in range(1, max_retries + 1):
        try:
            prompt = user_prompt
            if attempt > 1 and last_error:
                prompt += (
                    f"\n\n⚠️ ПРЕДЫДУЩАЯ ПОПЫТКА ЗАВЕРШИЛАСЬ ОШИБКОЙ:\n"
                    f"{last_error}\n"
                    f"Исправь проблему и верни валидный JSON с ровно "
                    f"{expected_count} элементами в items."
                )

            _add_log(
                job_id, LogLevel.INFO,
                f"[Structurer] Отправка батча ({len(batch)} карточек) "
                f"в LLM, попытка {attempt}/{max_retries}..."
            )

            data = llm_client.chat_json(
                SYSTEM_PROMPT,
                prompt,
                temperature=0.1,
                max_tokens=8000,
                timeout=180,
            )

            is_valid, error_msg = _validate_llm_response(data, expected_count)

            if is_valid:
                _add_log(
                    job_id, LogLevel.INFO,
                    f"[Structurer] Батч успешно структурирован "
                    f"({len(batch)} карточек)."
                )
                return data["items"]

            last_error = error_msg
            _add_log(
                job_id, LogLevel.WARNING,
                f"[Structurer] Невалидная структура ответа LLM "
                f"(попытка {attempt}/{max_retries}): {error_msg}"
            )

        except llm_client.LlmResponseError as e:
            last_error = str(e)
            _add_log(
                job_id, LogLevel.WARNING,
                f"[Structurer] Ошибка ответа LLM "
                f"(попытка {attempt}/{max_retries}): {e}"
            )
        except llm_client.LlmError as e:
            last_error = str(e)
            _add_log(
                job_id, LogLevel.ERROR,
                f"[Structurer] Ошибка LLM "
                f"(попытка {attempt}/{max_retries}): {e}"
            )
            # Для rate limit / auth ошибок нет смысла ретраить
            if isinstance(e, (
                llm_client.LlmDailyLimitError,
                llm_client.LlmAuthError,
                llm_client.LlmNoAvailableKeyError,
                llm_client.LlmDisabledError,
            )):
                break
        except Exception as e:
            last_error = str(e)
            _add_log(
                job_id, LogLevel.ERROR,
                f"[Structurer] Неожиданная ошибка "
                f"(попытка {attempt}/{max_retries}): {e}\n"
                f"{traceback.format_exc()}"
            )

    # Все попытки исчерпаны
    _add_log(
        job_id, LogLevel.ERROR,
        f"[Structurer] Все {max_retries} попыток исчерпаны для батча "
        f"из {len(batch)} карточек. Последняя ошибка: {last_error}"
    )
    return None


class StructuringWorker:
    """
    Фоновый воркер для структурирования карточек.

    Запускается в отдельном потоке. Парсер кладёт item_id в очередь,
    воркер подбирает батчи и обрабатывает через LLM.
    Парсер не ждёт — продолжает работать параллельно.
    """

    def __init__(self, app, job_id, batch_size=None, max_retries=None):
        self.app = app
        self.job_id = job_id
        self.batch_size = batch_size or config.SMART_STRUCTURING_BATCH_SIZE
        self.max_retries = max_retries or config.SMART_STRUCTURING_MAX_RETRIES
        self._queue: List[int] = []  # item_ids
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._flush_event = threading.Event()  # Сигнал: пора обработать что есть
        self._thread: Optional[threading.Thread] = None
        self._items_processed = 0
        self._items_failed = 0
        self._finished = threading.Event()

    def start(self):
        """Запускает воркер-поток."""
        if self._thread and self._thread.is_alive():
            return
        self._finished.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name=f"structurer-job-{self.job_id}",
        )
        self._thread.start()

        with self.app.app_context():
            _add_log(
                self.job_id, LogLevel.INFO,
                f"[Structurer] Воркер запущен (batch_size={self.batch_size}, "
                f"max_retries={self.max_retries})."
            )

    def enqueue(self, item_id: int):
        """Добавляет item_id в очередь на структурирование."""
        with self._lock:
            self._queue.append(item_id)
            queue_len = len(self._queue)

        # Если набрался полный батч — разбудить воркер
        if queue_len >= self.batch_size:
            self._flush_event.set()

    def stop_and_wait(self, timeout: float = 300.0):
        """
        Сигнализирует воркеру завершиться и ждёт.
        Воркер обработает оставшиеся карточки в очереди.
        """
        self._stop_event.set()
        self._flush_event.set()  # Разбудить если спит

        self._finished.wait(timeout=timeout)

        with self.app.app_context():
            _add_log(
                self.job_id, LogLevel.INFO,
                f"[Structurer] Воркер завершён. "
                f"Обработано: {self._items_processed}, "
                f"ошибок: {self._items_failed}."
            )

    @property
    def stats(self) -> dict:
        return {
            "processed": self._items_processed,
            "failed": self._items_failed,
            "queue_size": len(self._queue),
        }

    def _run_loop(self):
        """Основной цикл воркера."""
        try:
            while True:
                # Ждём пока наберётся полный батч или придёт сигнал stop
                self._flush_event.wait()
                self._flush_event.clear()

                with self._lock:
                    if self._stop_event.is_set():
                        # Если останавливаемся — забираем всё что осталось
                        pending_ids = list(self._queue)
                        self._queue.clear()
                    else:
                        # Забираем только полные батчи, остаток оставляем в очереди
                        complete_batches = len(self._queue) // self.batch_size
                        take_count = complete_batches * self.batch_size
                        pending_ids = self._queue[:take_count]
                        self._queue = self._queue[take_count:]
                        
                        # Если вдруг в очереди всё ещё есть полный батч (маловероятно)
                        if len(self._queue) >= self.batch_size:
                            self._flush_event.set()

                if pending_ids:
                    self._process_ids(pending_ids)

                # Проверяем, нужно ли завершаться
                if self._stop_event.is_set():
                    break
        except Exception as e:
            with self.app.app_context():
                _add_log(
                    self.job_id, LogLevel.ERROR,
                    f"[Structurer] Критическая ошибка воркера: {e}\n"
                    f"{traceback.format_exc()}"
                )
        finally:
            self._finished.set()

    def _process_ids(self, item_ids: List[int]):
        """Обрабатывает список item_ids, разбивая на батчи."""
        for i in range(0, len(item_ids), self.batch_size):
            batch_ids = item_ids[i:i + self.batch_size]
            self._process_batch(batch_ids)

    def _process_batch(self, item_ids: List[int]):
        """Обрабатывает один батч item_ids."""
        with self.app.app_context():
            try:
                # Загружаем карточки из БД
                items = ParsedItem.query.filter(
                    ParsedItem.id.in_(item_ids)
                ).all()

                if not items:
                    _add_log(
                        self.job_id, LogLevel.WARNING,
                        f"[Structurer] Батч пуст: items не найдены "
                        f"в БД (ids={item_ids})."
                    )
                    return

                # Формируем батч для LLM
                batch = []
                for item in items:
                    batch.append({
                        "item_id": item.id,
                        "title": item.title or "",
                        "raw_text": (item.raw_text or "")[:15000],  # Ограничение
                        "url": item.url or "",
                    })

                # Вызываем LLM
                results = structurize_batch(
                    batch,
                    self.job_id,
                    max_retries=self.max_retries,
                )

                if results is None:
                    # Полный провал — помечаем все карточки
                    for item in items:
                        item.structuring_status = StructuringStatus.FAILED
                        item.structuring_error = (
                            "LLM не смог структурировать данные "
                            "после всех попыток."
                        )
                    db.session.commit()
                    self._items_failed += len(items)

                    _add_log(
                        self.job_id, LogLevel.ERROR,
                        f"[Structurer] Батч из {len(items)} карточек "
                        f"полностью провалился."
                    )
                    return

                # Сопоставляем результаты с карточками
                for item, result in zip(items, results):
                    try:
                        item.structured_data = json.dumps(
                            result, ensure_ascii=False
                        )
                        item.structuring_status = StructuringStatus.SUCCESS
                        item.structuring_error = None
                        item.raw_text = None  # Очищаем сырой текст для экономии места в БД
                        self._items_processed += 1
                    except Exception as e:
                        item.structuring_status = StructuringStatus.FAILED
                        item.structuring_error = f"Ошибка сериализации: {e}"
                        self._items_failed += 1

                        _add_log(
                            self.job_id, LogLevel.ERROR,
                            f"[Structurer] Ошибка сериализации "
                            f"карточки #{item.id}: {e}"
                        )

                db.session.commit()

                _add_log(
                    self.job_id, LogLevel.INFO,
                    f"[Structurer] Батч обработан: "
                    f"{len(items)} карточек, "
                    f"всего обработано: {self._items_processed}, "
                    f"ошибок: {self._items_failed}."
                )

            except Exception as e:
                db.session.rollback()
                _add_log(
                    self.job_id, LogLevel.ERROR,
                    f"[Structurer] Ошибка обработки батча: {e}\n"
                    f"{traceback.format_exc()}"
                )
                self._items_failed += len(item_ids)
