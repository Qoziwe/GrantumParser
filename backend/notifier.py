"""
Notifier — отправка Telegram-уведомлений о событиях, требующих человека.

Реализовано без Telegram SDK: прямой HTTP-вызов Bot API.

События фазы 5:
- captcha;
- cloudflare;
- auth_required.

Правила:
- если TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID пустые, notifier отключён;
- не более одного сообщения на задачу за эпизод WAITING_HUMAN;
- глобальный кулдаун между отправками — 60 секунд;
- параллельные задачи одного домена агрегируются в одно сообщение;
- тексты ветвятся по SERVER_LOCATION: home / vps.
"""

import threading
import time

import requests

import config
from models import add_log, LogLevel


GLOBAL_COOLDOWN_SECONDS = 60.0

# Небольшое окно агрегации: если несколько задач одного домена
# почти одновременно попросят человека, отправим одно сообщение.
AGGREGATION_WINDOW_SECONDS = 2.0

_lock = threading.Lock()
_last_sent_at = 0.0

# job_id -> {"reason": block_reason, "domain": domain}
_job_episodes = {}

# domain -> pending notification state
_domain_pending = {}


def is_enabled() -> bool:
    return bool(
        config.TELEGRAM_BOT_TOKEN.strip()
        and config.TELEGRAM_CHAT_IDS
    )


def _compose_message(job_ids, domain, reasons, urls):
    """
    Собирает текст Telegram-сообщения.

    Шаблоны из приложения B:
    - home, капча: подойди к компьютеру, открыто окно Chrome;
    - home, авторизация: войди в окне Chrome;
    - vps: добавить ссылку на PUBLIC_BROWSER_URL.
    """
    if len(job_ids) == 1:
        tasks = f"Задача #{job_ids[0]}"
    else:
        tasks = "Задачи " + ", ".join(f"#{x}" for x in job_ids)

    reasons = set(reasons)

    if reasons == {"auth_required"}:
        head = f"[Grantum] {tasks}: {domain} требует авторизации."
        home_action = "Войди в окне Chrome. После входа парсер продолжит сам."
    elif reasons <= {"captcha", "cloudflare"}:
        head = f"[Grantum] {tasks}: капча/блок на {domain}."
        home_action = (
            "Подойди к компьютеру — открыто окно Chrome. "
            "После решения парсер продолжит сам."
        )
    else:
        head = f"[Grantum] {tasks}: требуется действие человека на {domain}."
        home_action = (
            "Подойди к компьютеру — открыто окно Chrome. "
            "После решения парсер продолжит сам."
        )

    if config.SERVER_LOCATION == "vps" and config.PUBLIC_BROWSER_URL.strip():
        action = f"Открыть браузер сервера: {config.PUBLIC_BROWSER_URL.strip()}"
    else:
        action = home_action

    urls = list(dict.fromkeys(urls))

    if len(urls) > 5:
        urls = urls[:5] + [f"... и ещё {len(urls) - 5}"]

    if len(urls) == 1:
        url_block = f"URL: {urls[0]}"
    else:
        url_block = "URL:\n" + "\n".join(urls)

    return f"{head}\n{action}\n{url_block}"


def _send_text(text: str):
    """
    Отправка текста через Telegram Bot API.

    Токен не логируется.
    """
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN.strip()}/sendMessage"

    # Отправляем во все указанные чаты (TELEGRAM_CHAT_ID — через запятую).
    # Если хоть одна доставка прошла — считаем отправку успешной.
    errors = []
    delivered = False
    for chat_id in config.TELEGRAM_CHAT_IDS:
        payload = {
            "chat_id": chat_id,
            "text": text,
        }
        try:
            response = requests.post(url, json=payload, timeout=15)
            if response.ok:
                delivered = True
            else:
                errors.append(
                    f"chat {chat_id}: HTTP {response.status_code}: "
                    f"{response.text[:200]}"
                )
        except Exception as exc:
            errors.append(f"chat {chat_id}: {exc}")

    if not delivered:
        raise RuntimeError("Telegram: " + "; ".join(errors)[:280])


def notify_human_required(app, job_id, target_url, domain, block_reason):
    """
    Вызывается executor'ом, когда задача переходит в WAITING_HUMAN.

    Возвращает True, если уведомление запланировано/отправлено,
    False — если notifier выключен или эпизод уже уведомлён.
    """
    if not is_enabled():
        try:
            add_log(
                job_id,
                LogLevel.WARNING,
                "Telegram-уведомление пропущено: "
                "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID не настроены."
            )
        except Exception:
            pass
        return False

    with _lock:
        existing_episode = _job_episodes.get(job_id)

        if existing_episode and existing_episode.get("reason") == block_reason:
            # Эта задача уже уведомлена про текущий эпизод.
            return False

        _job_episodes[job_id] = {
            "reason": block_reason,
            "domain": domain,
        }

        state = _domain_pending.get(domain)

        if state is None:
            state = {
                "app": app,
                "job_ids": set(),
                "urls": [],
                "reasons": set(),
                "timer": None,
            }
            _domain_pending[domain] = state

        state["job_ids"].add(job_id)

        if target_url not in state["urls"]:
            state["urls"].append(target_url)

        state["reasons"].add(block_reason)

        if state["timer"] is None:
            timer = threading.Timer(
                AGGREGATION_WINDOW_SECONDS,
                _flush_domain,
                args=(domain,)
            )
            timer.daemon = True
            state["timer"] = timer
            timer.start()

    return True


def mark_human_episode_finished(job_id):
    """
    Вызывается, когда эпизод WAITING_HUMAN завершился:
    - человек решил проблему;
    - истёк таймаут;
    - задача упала/завершилась.

    Это позволяет при следующем новом блоке отправить новое уведомление.
    """
    with _lock:
        episode = _job_episodes.pop(job_id, None)

        if not episode:
            return

        domain = episode.get("domain")
        state = _domain_pending.get(domain)

        if not state:
            return

        state["job_ids"].discard(job_id)

        if not state["job_ids"]:
            timer = state.get("timer")
            if timer is not None:
                try:
                    timer.cancel()
                except Exception:
                    pass

            _domain_pending.pop(domain, None)


def _flush_domain(domain):
    """
    Отложенная отправка агрегированного уведомления по домену.
    """
    global _last_sent_at

    with _lock:
        state = _domain_pending.pop(domain, None)

    if not state:
        return

    job_ids = sorted(state["job_ids"])
    urls = list(state["urls"])
    reasons = set(state["reasons"])
    app = state["app"]

    if not job_ids:
        return

    with app.app_context():
        with _lock:
            wait_seconds = GLOBAL_COOLDOWN_SECONDS - (time.time() - _last_sent_at)

        if wait_seconds > 0:
            # Глобальный кулдаун ещё не прошёл — переносим отправку.
            with _lock:
                new_state = _domain_pending.get(domain)

                if new_state is None:
                    new_state = {
                        "app": app,
                        "job_ids": set(),
                        "urls": [],
                        "reasons": set(),
                        "timer": None,
                    }
                    _domain_pending[domain] = new_state

                new_state["job_ids"].update(job_ids)

                for url in urls:
                    if url not in new_state["urls"]:
                        new_state["urls"].append(url)

                new_state["reasons"].update(reasons)

                if new_state["timer"] is None:
                    timer = threading.Timer(
                        wait_seconds + 0.2,
                        _flush_domain,
                        args=(domain,)
                    )
                    timer.daemon = True
                    new_state["timer"] = timer
                    timer.start()

            for job_id in job_ids:
                try:
                    add_log(
                        job_id,
                        LogLevel.INFO,
                        f"Telegram-уведомление отложено на {wait_seconds:.0f} сек "
                        f"(глобальный кулдаун)."
                    )
                except Exception:
                    pass

            return

        text = _compose_message(job_ids, domain, reasons, urls)

        try:
            _send_text(text)

            with _lock:
                _last_sent_at = time.time()

            for job_id in job_ids:
                try:
                    add_log(
                        job_id,
                        LogLevel.INFO,
                        "Telegram-уведомление отправлено."
                    )
                except Exception:
                    pass

        except Exception as exc:
            for job_id in job_ids:
                try:
                    add_log(
                        job_id,
                        LogLevel.WARNING,
                        f"Telegram-уведомление не отправлено: {exc}"
                    )
                except Exception:
                    pass