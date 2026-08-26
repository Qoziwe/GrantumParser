import json
import threading

from flask import Flask, request, jsonify
from flask_cors import CORS
from sqlalchemy import event, inspect, text
from sqlalchemy.engine import Engine

import config
import auth as auth_mod
from models import (
    db,
    Job,
    Log,
    ParsedItem,
    JobStatus,
    SiteProfile,
    ChildProfile,
)
from parser import run_f6s_parser
from url_utils import (
    UrlValidationError,
    normalize_target_url,
    normalize_path_prefix,
)
from seed_profiles import ensure_f6s_seed_profile
import analyzer
import notifier

app = Flask(
    __name__,
    # Статика фронтенда (если собрана во frontend/dist) отдаётся с теми же
    # security-заголовками; при dev-режиме фронт работает через Vite.
    static_folder=None,
)

app.config["SQLALCHEMY_DATABASE_URI"] = config.SQLALCHEMY_DATABASE_URI
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = config.SQLALCHEMY_TRACK_MODIFICATIONS
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MiB на тело запроса

# Куки сессии: HttpOnly; SameSite зависит от режима: при кросс-сайт фронтенде
# (GitHub Pages -> API-домен) нужен None, иначе куки не отправляются.
# Secure — по конфигу/окружению (SameSite=None требует Secure=True).
_SESSION_SECURE = config.SESSION_COOKIE_SECURE
if _SESSION_SECURE == "auto":
    _SESSION_SECURE = str(config.SERVER_LOCATION == "vps").lower()

_SAMESITE = "None" if _SESSION_SECURE == "true" else "Lax"

# Фронтенд живёт на другом порту => другой origin. Для передачи сессионных
# кук нужны точные origins (не "*") + supports_credentials.
CORS(
    app,
    origins=config.CORS_ORIGINS,
    supports_credentials=True,
    allow_headers=["Content-Type", "X-CSRF-Token"],
    max_age=600,
)

db.init_app(app)

# Ссылка на приложение для notifier (контекст Flask при отправке уведомлений).
config._flask_app = app


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """
    Настройка SQLite под нагрузку парсера:
    - WAL: читатели (API) не блокируют писателей (потоки парсера) и наоборот,
      а каждая запись перестаёт требовать полный fsync (~десятки мс -> <1 мс);
    - synchronous=NORMAL безопасен при WAL и заметно ускоряет коммиты;
    - busy_timeout вместо мгновенного "database is locked" при конкуренции.
    Потоки в одном процессе видят WAL через общую память, отдельные
    процессы — через -wal/-shm файлы рядом с базой.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def _migrate_sqlite_schema():
    """
    Идемпотентная lightweight-миграция для существующей SQLite-базы.
    """
    if db.engine.dialect.name != "sqlite":
        return

    inspector = inspect(db.engine)

    if not inspector.has_table("jobs"):
        return

    existing_columns = {
        column["name"]
        for column in inspector.get_columns("jobs")
    }

    with db.engine.begin() as conn:
        if "profile_id" not in existing_columns:
            conn.execute(text(
                "ALTER TABLE jobs ADD COLUMN profile_id INTEGER"
            ))

        if "block_reason" not in existing_columns:
            conn.execute(text(
                "ALTER TABLE jobs ADD COLUMN block_reason TEXT"
            ))

        if "human_requested_at" not in existing_columns:
            conn.execute(text(
                "ALTER TABLE jobs ADD COLUMN human_requested_at DATETIME"
            ))

        if "parse_mode" not in existing_columns:
            conn.execute(text(
                "ALTER TABLE jobs ADD COLUMN parse_mode VARCHAR(20) NOT NULL DEFAULT 'fast'"
            ))

        if "max_pages" not in existing_columns:
            conn.execute(text(
                "ALTER TABLE jobs ADD COLUMN max_pages INTEGER NOT NULL DEFAULT 1"
            ))

        if "max_child_profiles" not in existing_columns:
            conn.execute(text(
                "ALTER TABLE jobs ADD COLUMN max_child_profiles INTEGER NOT NULL DEFAULT 20"
            ))

        if "max_detail_pages" not in existing_columns:
            conn.execute(text(
                "ALTER TABLE jobs ADD COLUMN max_detail_pages INTEGER NOT NULL DEFAULT 100"
            ))

        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_jobs_profile_id ON jobs (profile_id)"
        ))

        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs (status)"
        ))

        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_jobs_created_at ON jobs (created_at)"
        ))

    # Миграция parsed_items
    if inspector.has_table("parsed_items"):
        items_columns = {
            column["name"]
            for column in inspector.get_columns("parsed_items")
        }

        with db.engine.begin() as conn:
            if "source_url" not in items_columns:
                conn.execute(text(
                    "ALTER TABLE parsed_items ADD COLUMN source_url VARCHAR(2048)"
                ))

            if "structured_data" not in items_columns:
                conn.execute(text(
                    "ALTER TABLE parsed_items ADD COLUMN structured_data TEXT"
                ))

            if "structuring_status" not in items_columns:
                conn.execute(text(
                    "ALTER TABLE parsed_items ADD COLUMN "
                    "structuring_status VARCHAR(20) NOT NULL DEFAULT 'skipped'"
                ))

            if "structuring_error" not in items_columns:
                conn.execute(text(
                    "ALTER TABLE parsed_items ADD COLUMN structuring_error TEXT"
                ))

            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_parsed_items_structuring_status "
                "ON parsed_items (structuring_status)"
            ))


with app.app_context():
    db.create_all()
    _migrate_sqlite_schema()

    # Инициализация таблиц авторизации в той же SQLite-базе
    _sqlite_path = None
    try:
        raw_path = db.engine.url.database
        if raw_path:
            import os as _os
            _sqlite_path = (
                raw_path if _os.path.isabs(raw_path)
                else _os.path.abspath(_os.path.join(app.instance_path or ".", raw_path))
            )
    except Exception:
        _sqlite_path = None
    auth_mod.init_auth_db(_sqlite_path)

    # Первичный пароль: из конфига или сгенерированный (печатается в консоль).
    with auth_mod._connect() as _auth_conn:
        auth_mod._ensure_tables(_auth_conn)
        if not auth_mod.is_password_set(_auth_conn):
            if config.AUTH_PASSWORD:
                initial_password = config.AUTH_PASSWORD
                print("[auth] Установлен пароль из AUTH_PASSWORD (.env).")
            else:
                import secrets as _secrets
                initial_password = _secrets.token_urlsafe(12)
                print("=" * 60)
                print("[auth] Пароль доступа не задан в .env.")
                print(f"[auth] СГЕНЕРИРОВАН ПАРОЛЬ: {initial_password}")
                print("[auth] Сохрани его и/или задай AUTH_PASSWORD в .env,")
                print("[auth] чтобы он не менялся при каждом старте.")
                print("=" * 60)
            auth_mod.set_password(_auth_conn, initial_password)

    # Создаём seed-профиль F6S, если его нет
    ensure_f6s_seed_profile()

    # Помечаем зависшие задачи как упавшие
    Job.query.filter(
        Job.status.in_([
            JobStatus.PENDING,
            JobStatus.RUNNING,
            JobStatus.WAITING_HUMAN,
        ])
    ).update(
        {Job.status: JobStatus.FAILED},
        synchronize_session=False
    )

    db.session.commit()


# ============================================================
# Аутентификация: before_request / after_request
# ============================================================

# Маршруты, доступные без аутентификации.
_PUBLIC_PATHS = {"/api/auth/login", "/api/auth/status", "/api/auth/setup"}

# Безопасные методы, не требующие CSRF-токена.
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _client_ip():
    return auth_mod._client_ip(
        request.remote_addr or "?",
        request.headers.get("X-Forwarded-For"),
    )


def _set_auth_cookies(resp, session_token=None, csrf_token=None):
    """Выставляет cookie сессии и CSRF. Secure управляется конфигом."""
    secure = str(_SESSION_SECURE) == "true"
    if session_token:
        resp.set_cookie(
            auth_mod.SESSION_COOKIE_NAME,
            session_token,
            max_age=auth_mod.SESSION_TTL_SECONDS,
            httponly=True,
            samesite=_SAMESITE,
            secure=secure,
            path="/",
        )
    else:
        resp.delete_cookie(auth_mod.SESSION_COOKIE_NAME, path="/")
    if csrf_token:
        # CSRF-куку читает JS фронтенда -> НЕ HttpOnly.
        resp.set_cookie(
            auth_mod.CSRF_COOKIE_NAME,
            csrf_token,
            max_age=auth_mod.SESSION_TTL_SECONDS,
            httponly=False,
            samesite=_SAMESITE,
            secure=secure,
            path="/",
        )


@app.before_request
def _auth_gate():
    path = request.path

    # Общий rate limit на API — до аутентификации.
    if path.startswith("/api/"):
        ip = _client_ip()
        try:
            auth_mod.check_api_rate(ip)
        except auth_mod.RateLimitedError as e:
            resp = jsonify({"error": str(e)})
            resp.status_code = 429
            resp.headers["Retry-After"] = str(e.retry_after)
            return resp

    # OPTIONS запросы (CORS preflight) должны проходить без проверки сессии и токенов.
    if request.method == "OPTIONS":
        return None

    if path in _PUBLIC_PATHS:
        return None

    token = request.cookies.get(auth_mod.SESSION_COOKIE_NAME)
    if not token:
        return jsonify({"error": "unauthorized"}), 401

    with auth_mod._connect() as conn:
        sess = auth_mod.get_session(conn, token)

    if sess is None:
        resp = jsonify({"error": "session expired"})
        resp.status_code = 401
        _set_auth_cookies(resp, session_token=None)
        return resp

    # CSRF для мутирующих запросов: cookie X-CSRF-Token должны совпасть.
    # Исключение — logout: принудительный разлогин через CSRF безвреден,
    # а неработающий выход при потере токена — реальная проблема.
    if request.method not in _SAFE_METHODS and path != "/api/auth/logout":
        cookie_csrf = request.cookies.get(auth_mod.CSRF_COOKIE_NAME)
        header_csrf = request.headers.get("X-CSRF-Token")
        if not auth_mod.verify_csrf(cookie_csrf, header_csrf):
            return jsonify({"error": "csrf validation failed"}), 403

    return None


@app.after_request
def _security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=()",
    )
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'",
    )
    return resp


# ============================================================
# Auth API
# ============================================================

@app.route("/api/auth/status", methods=["GET"])
def auth_status():
    """Публичный эндпоинт: установлен ли пароль (для показа формы логина)."""
    with auth_mod._connect() as conn:
        return jsonify({"password_set": auth_mod.is_password_set(conn)})


@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json(silent=True) or {}
    password = str(data.get("password") or "")

    # Ограничиваем длину входа — защита от resource-exhaustion на scrypt.
    if len(password) > 1024:
        return jsonify({"error": "Неверный пароль."}), 400

    ip = _client_ip()

    try:
        auth_mod.check_login_rate(ip)
        auth_mod.check_bruteforce_lock(ip)
    except auth_mod.RateLimitedError as e:
        resp = jsonify({"error": str(e)})
        resp.status_code = 429
        resp.headers["Retry-After"] = str(e.retry_after)
        return resp

    with auth_mod._connect() as conn:
        row = conn.execute(
            "SELECT password_hash FROM auth_settings WHERE id = 1"
        ).fetchone()
        stored = row[0] if row else None
        ok = bool(stored) and auth_mod.verify_password(password, stored)

        if not ok:
            auth_mod.record_failed_login(ip)
            return jsonify({"error": "Неверный пароль."}), 401

        auth_mod.reset_failed_logins(ip)
        auth_mod.purge_expired_sessions(conn)
        token, csrf = auth_mod.create_session(conn)

    # CSRF-токен дублируем в теле ответа: фронтенд на другом домене
    # (GitHub Pages) не может читать куки API-домена через document.cookie,
    # поэтому единственный способ доставить токен в JS — тело ответа.
    resp = jsonify({"ok": True, "csrf_token": csrf})
    _set_auth_cookies(resp, session_token=token, csrf_token=csrf)
    return resp


@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    token = request.cookies.get(auth_mod.SESSION_COOKIE_NAME)
    with auth_mod._connect() as conn:
        auth_mod.destroy_session(conn, token)
    resp = jsonify({"ok": True})
    _set_auth_cookies(resp, session_token=None)
    return resp


@app.route("/api/auth/password", methods=["POST"])
def auth_change_password():
    """Смена пароля (требует активную сессию + CSRF)."""
    data = request.get_json(silent=True) or {}
    new_password = str(data.get("new_password") or "")
    old_password = str(data.get("old_password") or "")

    ip = _client_ip()
    try:
        auth_mod.check_bruteforce_lock(ip)
    except auth_mod.RateLimitedError as e:
        resp = jsonify({"error": str(e)})
        resp.status_code = 429
        resp.headers["Retry-After"] = str(e.retry_after)
        return resp

    with auth_mod._connect() as conn:
        row = conn.execute(
            "SELECT password_hash FROM auth_settings WHERE id = 1"
        ).fetchone()
        stored = row[0] if row else None

        if not (stored and auth_mod.verify_password(old_password, stored)):
            auth_mod.record_failed_login(ip)
            return jsonify({"error": "Текущий пароль неверен."}), 401

        try:
            auth_mod.set_password(conn, new_password)
        except auth_mod.AuthError as e:
            return jsonify({"error": str(e)}), 400

    resp = jsonify({"ok": True})
    # Все сессии инвалидированы set_password — чистим куки.
    _set_auth_cookies(resp, session_token=None)
    return resp


@app.route("/api/parse", methods=["POST"])
def parse():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    parse_mode = (data.get("mode") or "fast").strip().lower()
    max_pages = data.get("iterations", 1)

    if not url:
        return jsonify({"error": "url is required"}), 400

    if parse_mode not in {"fast", "smart"}:
        return jsonify({"error": "mode must be fast or smart"}), 400

    try:
        max_pages = int(max_pages)
    except (TypeError, ValueError):
        return jsonify({"error": "iterations must be an integer"}), 400

    if not 1 <= max_pages <= 100:
        return jsonify({"error": "iterations must be between 1 and 100"}), 400

    max_child_profiles = data.get("maxChildProfiles", 20)
    try:
        max_child_profiles = int(max_child_profiles)
    except (TypeError, ValueError):
        return jsonify({"error": "maxChildProfiles must be an integer"}), 400

    if max_child_profiles < 1:
        return jsonify({"error": "maxChildProfiles must be at least 1"}), 400

    max_detail_pages = data.get("maxDetailPages", config.SMART_MAX_DETAIL_PAGES)
    try:
        max_detail_pages = int(max_detail_pages)
    except (TypeError, ValueError):
        return jsonify({"error": "maxDetailPages must be an integer"}), 400

    if max_detail_pages < 1:
        return jsonify({"error": "maxDetailPages must be at least 1"}), 400

    try:
        normalized = normalize_target_url(url)
    except UrlValidationError as exc:
        return jsonify({"error": str(exc)}), 400

    job = Job(
        target_url=normalized.original,
        status=JobStatus.PENDING,
        total_found=0,
        parse_mode=parse_mode,
        max_pages=max_pages,
        max_child_profiles=max_child_profiles,
        max_detail_pages=max_detail_pages,
    )

    db.session.add(job)
    db.session.commit()

    thread = threading.Thread(
        target=run_f6s_parser,
        args=(app, job.id, normalized.original),
        daemon=True
    )
    thread.start()

    return jsonify(job.to_dict()), 201


@app.route("/api/jobs", methods=["GET"])
def get_jobs():
    # Лимит истории: чипам и статусам фронтенда хватает свежих задач,
    # а полная выборка со временем деградирует каждый опрос.
    jobs = Job.query.order_by(Job.created_at.desc()).limit(200).all()
    return jsonify([job.to_dict() for job in jobs])


@app.route("/api/jobs/<int:job_id>/logs", methods=["GET"])
def get_job_logs(job_id):
    job = db.session.get(Job, job_id)

    if not job:
        return jsonify({"error": "Job not found"}), 404

    # after_id: инкрементальная догрузка для поллинга — фронтенд присылает
    # id последнего полученного лога и получает только новые записи.
    # Без него (первый запрос) отдаём весь лог.
    try:
        after_id = int(request.args.get("after_id", 0))
    except (TypeError, ValueError):
        after_id = 0

    query = Log.query.filter_by(job_id=job_id)

    if after_id > 0:
        query = query.filter(Log.id > after_id)
        logs = query.order_by(Log.id.asc()).all()
    else:
        logs = query.order_by(Log.created_at.asc(), Log.id.asc()).all()

    return jsonify([log.to_dict() for log in logs])


@app.route("/api/items", methods=["GET"])
def get_items():
    job_id = request.args.get("job_id", type=int)

    query = ParsedItem.query

    if job_id:
        query = query.filter_by(job_id=job_id)

    items = query.order_by(ParsedItem.created_at.desc()).all()

    return jsonify([item.to_dict() for item in items])


@app.route("/api/jobs", methods=["DELETE"])
def delete_all_jobs():
    try:
        n_items = db.session.query(ParsedItem).delete(synchronize_session=False)
        n_logs = db.session.query(Log).delete(synchronize_session=False)
        n_jobs = db.session.query(Job).delete(synchronize_session=False)

        db.session.commit()

        return jsonify({
            "deleted_jobs": n_jobs,
            "deleted_items": n_items,
            "deleted_logs": n_logs,
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/jobs/<int:job_id>", methods=["DELETE"])
def delete_job(job_id):
    job = db.session.query(Job).filter_by(id=job_id).first()

    if not job:
        return jsonify({"error": "not found"}), 404

    try:
        n_items = (
            db.session.query(ParsedItem)
            .filter_by(job_id=job_id)
            .delete(synchronize_session=False)
        )

        n_logs = (
            db.session.query(Log)
            .filter_by(job_id=job_id)
            .delete(synchronize_session=False)
        )

        db.session.delete(job)
        db.session.commit()

        return jsonify({
            "deleted": 1,
            "deleted_items": n_items,
            "deleted_logs": n_logs
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

# ============================================================
# Profiles API
# ============================================================

@app.route("/api/profiles", methods=["GET"])
def list_profiles():
    """
    Возвращает список всех профилей сайтов.

    Сортировка: свежие обновления сверху.
    Инструкции не возвращаются по умолчанию (слишком много данных).
    """
    profiles = (
        SiteProfile.query
        .order_by(SiteProfile.updated_at.desc().nullslast(), SiteProfile.id.desc())
        .all()
    )
    return jsonify([p.to_dict(include_instructions=False) for p in profiles])


@app.route("/api/profiles/<int:profile_id>", methods=["GET"])
def get_profile(profile_id):
    """
    Возвращает один профиль, включая instructions_json.
    """
    profile = db.session.get(SiteProfile, profile_id)

    if not profile:
        return jsonify({"error": "Profile not found"}), 404

    return jsonify(profile.to_dict(include_instructions=True))


@app.route("/api/profiles/<int:profile_id>", methods=["DELETE"])
def delete_profile(profile_id):
    """
    Удаляет профиль сайта.

    Благодаря ondelete="SET NULL" в models.py, привязанные jobs
    остаются в базе, но теряют ссылку на профиль.
    Следующий запуск парсера по URL этого домена снова запустит analyzer.
    """
    profile = db.session.get(SiteProfile, profile_id)

    if not profile:
        return jsonify({"error": "Profile not found"}), 404

    try:
        domain = profile.domain
        path_prefix = profile.path_prefix

        db.session.delete(profile)
        db.session.commit()

        return jsonify({
            "deleted": 1,
            "domain": domain,
            "path_prefix": path_prefix,
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/profiles/<int:profile_id>/rescan", methods=["POST"])
def rescan_profile(profile_id):
    """
    Принудительное пересканирование профиля.

    Логика:
    1. Сбрасывает кулдаун (retry_not_before = None).
    2. Помечает профиль как неактивный.
    3. Создаёт служебный job со статусом PENDING.
    4. Запускает analyzer в фоне.
    5. Возвращает job_id, чтобы пользователь мог следить за логами.

    После успешного rescan профиль получит новую версию инструкции.
    """
    profile = db.session.get(SiteProfile, profile_id)

    if not profile:
        return jsonify({"error": "Profile not found"}), 404

    # Сбрасываем кулдаун и помечаем как неактивный
    profile.retry_not_before = None
    profile.is_active = False
    profile.last_error = "Запрошено принудительное пересканирование"

    # Создаём служебный job для отслеживания прогресса
    # target_url формируется из домена и path_prefix для analyzer
    scan_target_url = f"https://{profile.domain}{profile.path_prefix}"

    scan_job = Job(
        target_url=scan_target_url,
        status=JobStatus.PENDING,
        total_found=0,
        profile_id=profile.id,
    )

    db.session.add(scan_job)
    db.session.commit()

    scan_job_id = scan_job.id

    def _run_rescan(app_ctx, scan_job_id, profile_id):
        """
        Фоновая задача: in-place пересканирование профиля.
        
        Не удаляет строку, а обновляет инструкцию in-place,
        сохраняя previous_instructions_json и инкрементируя version.
        """
        with app_ctx.app_context():
            from models import add_log, LogLevel, db as _db

            def _set_status(status):
                _set_job_status_in_thread(scan_job_id, status)

            _set_status(JobStatus.RUNNING)
            add_log(
                scan_job_id,
                LogLevel.INFO,
                f"Запущено принудительное пересканирование профиля #{profile_id}.",
            )

            profile = _db.session.get(SiteProfile, profile_id)
            if not profile:
                add_log(
                    scan_job_id,
                    LogLevel.ERROR,
                    "Профиль не найден в БД (возможно, был удалён).",
                )
                _set_status(JobStatus.FAILED)
                notifier.notify_runtime_error(
                    scan_job_id,
                    "пересканирование профиля",
                    "Профиль не найден в БД (возможно, был удалён).",
                )
                return

            # Нормализуем URL для analyzer
            try:
                normalized = normalize_target_url(
                    f"https://{profile.domain}{profile.path_prefix}"
                )
            except UrlValidationError as exc:
                add_log(
                    scan_job_id,
                    LogLevel.ERROR,
                    f"Не удалось нормализовать URL профиля: {exc}",
                )
                _set_status(JobStatus.FAILED)
                notifier.notify_runtime_error(
                    scan_job_id,
                    "пересканирование профиля",
                    f"Не удалось нормализовать URL профиля: {exc}",
                )
                return

            # Сохраняем старую инструкцию
            old_instructions = profile.instructions_json
            old_version = profile.version

            # Помечаем как неактивный
            profile.is_active = False
            profile.last_error = "Запрошено принудительное пересканирование"
            _db.session.commit()

            add_log(
                scan_job_id,
                LogLevel.INFO,
                f"Старая версия v{old_version}. Запускаю анализатор страницы.",
            )

            # Вызываем ядро анализа
            try:
                instruction = analyzer._analyze_page_and_build_instruction(
                    app_ctx, normalized, scan_job_id
                )

                # In-place обновление существующей строки
                profile.previous_instructions_json = old_instructions
                profile.instructions_json = json.dumps(instruction, ensure_ascii=False)
                profile.version = old_version + 1
                profile.is_active = True
                profile.fail_count = 0
                profile.last_error = None
                profile.retry_not_before = None
                _db.session.commit()

                add_log(
                    scan_job_id,
                    LogLevel.INFO,
                    f"Пересканирование успешно. "
                    f"Профиль #{profile.id} обновлён до v{profile.version}.",
                )
                _set_status(JobStatus.COMPLETED)

            except analyzer.AnalyzerError as exc:
                add_log(
                    scan_job_id,
                    LogLevel.ERROR,
                    f"Пересканирование не удалось: {exc}",
                )
                analyzer.mark_profile_failed(profile, str(exc))
                _set_status(JobStatus.FAILED)
                notifier.notify_runtime_error(
                    scan_job_id,
                    "пересканирование профиля",
                    str(exc),
                )

            except Exception as exc:
                _db.session.rollback()
                add_log(
                    scan_job_id,
                    LogLevel.ERROR,
                    f"Критическая ошибка при пересканировании: {exc}",
                )
                try:
                    analyzer.mark_profile_failed(profile, str(exc))
                except Exception:
                    pass
                _set_status(JobStatus.FAILED)
                notifier.notify_runtime_error(
                    scan_job_id,
                    "пересканирование профиля",
                    f"Критическая ошибка: {exc}",
                )

    def _set_job_status_in_thread(job_id, status):
        """Безопасное обновление статуса job из потока."""
        try:
            job = db.session.get(Job, job_id)
            if job:
                job.status = status
                db.session.commit()
        except Exception:
            db.session.rollback()

    thread = threading.Thread(
        target=_run_rescan,
        args=(app, scan_job_id, profile.id),
        daemon=True,
    )
    thread.start()

    return jsonify({
        "ok": True,
        "job_id": scan_job_id,
        "profile_id": profile.id,
        "domain": profile.domain,
        "path_prefix": profile.path_prefix,
    }), 202

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True,
        use_reloader=False
    )