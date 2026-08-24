from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()


def utcnow():
    """
    Возвращает текущее UTC-время без timezone-информации.
    SQLite проще работает с naive datetime.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class JobStatus:
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING_HUMAN = "waiting_human"

    ALL = [PENDING, RUNNING, COMPLETED, FAILED, WAITING_HUMAN]


class LogLevel:
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"

    ALL = [INFO, WARNING, ERROR]


class SiteProfile(db.Model):
    """
    Профиль сайта.

    Хранит JSON-инструкцию, по которой executor будет парсить сайт.
    Профиль привязывается к домену и шаблону пути.
    """
    __tablename__ = "site_profiles"

    id = db.Column(db.Integer, primary_key=True)

    domain = db.Column(
        db.String(255),
        nullable=False,
        index=True
    )

    path_prefix = db.Column(
        db.String(1024),
        nullable=False,
        default="/"
    )

    instructions_json = db.Column(
        db.Text,
        nullable=False
    )

    previous_instructions_json = db.Column(
        db.Text,
        nullable=True
    )

    version = db.Column(
        db.Integer,
        nullable=False,
        default=1
    )

    is_active = db.Column(
        db.Boolean,
        nullable=False,
        default=True
    )

    fail_count = db.Column(
        db.Integer,
        nullable=False,
        default=0
    )

    retry_not_before = db.Column(
        db.DateTime,
        nullable=True
    )

    last_success_at = db.Column(
        db.DateTime,
        nullable=True
    )

    last_failure_at = db.Column(
        db.DateTime,
        nullable=True
    )

    last_error = db.Column(
        db.Text,
        nullable=True
    )

    created_at = db.Column(
        db.DateTime,
        default=utcnow,
        nullable=False
    )

    updated_at = db.Column(
        db.DateTime,
        default=utcnow,
        onupdate=utcnow,
        nullable=False
    )

    __table_args__ = (
        db.UniqueConstraint(
            "domain",
            "path_prefix",
            name="uq_site_profiles_domain_path_prefix"
        ),
        db.Index(
            "ix_site_profiles_domain_path_prefix",
            "domain",
            "path_prefix"
        ),
    )

    def __repr__(self):
        return f"<SiteProfile {self.id} domain={self.domain} path={self.path_prefix} active={self.is_active}>"

    def to_dict(self, include_instructions: bool = False):
        data = {
            "id": self.id,
            "domain": self.domain,
            "path_prefix": self.path_prefix,
            "version": self.version,
            "is_active": self.is_active,
            "fail_count": self.fail_count,
            "retry_not_before": self.retry_not_before.isoformat() if self.retry_not_before else None,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "last_failure_at": self.last_failure_at.isoformat() if self.last_failure_at else None,
            "last_error": self.last_error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

        if include_instructions:
            data["instructions_json"] = self.instructions_json
            data["previous_instructions_json"] = self.previous_instructions_json

        return data


class ChildProfile(db.Model):
    """Профиль detail-страницы, созданный для конкретного листинга."""
    __tablename__ = "child_profiles"

    id = db.Column(db.Integer, primary_key=True)
    parent_profile_id = db.Column(
        db.Integer,
        db.ForeignKey("site_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    domain = db.Column(db.String(255), nullable=False, index=True)
    path_prefix = db.Column(db.String(1024), nullable=False, default="/")
    instructions_json = db.Column(db.Text, nullable=False)
    version = db.Column(db.Integer, nullable=False, default=1)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    parent_profile = db.relationship(
        "SiteProfile",
        backref=db.backref("child_profiles", lazy="select", cascade="all, delete-orphan"),
    )

    __table_args__ = (
        db.UniqueConstraint(
            "parent_profile_id", "domain", "path_prefix",
            name="uq_child_profiles_parent_domain_path",
        ),
    )


class Job(db.Model):
    """
    Задача парсинга.
    Создается каждый раз, когда пользователь запускает парсер из админки.
    """
    __tablename__ = "jobs"

    id = db.Column(db.Integer, primary_key=True)

    target_url = db.Column(
        db.String(2048),
        nullable=False
    )

    status = db.Column(
        db.String(20),
        nullable=False,
        default=JobStatus.PENDING,
        index=True
    )

    total_found = db.Column(
        db.Integer,
        nullable=False,
        default=0
    )

    created_at = db.Column(
        db.DateTime,
        default=utcnow,
        nullable=False,
        index=True
    )

    profile_id = db.Column(
        db.Integer,
        db.ForeignKey("site_profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    block_reason = db.Column(
        db.Text,
        nullable=True
    )

    human_requested_at = db.Column(
        db.DateTime,
        nullable=True
    )

    parse_mode = db.Column(
        db.String(20),
        nullable=False,
        default="fast"
    )

    max_pages = db.Column(
        db.Integer,
        nullable=False,
        default=1
    )

    max_child_profiles = db.Column(
        db.Integer,
        nullable=False,
        default=20
    )

    max_detail_pages = db.Column(
        db.Integer,
        nullable=False,
        default=100
    )

    profile = db.relationship(
        "SiteProfile",
        backref=db.backref("jobs", lazy="select"),
        foreign_keys=[profile_id]
    )

    logs = db.relationship(
        "Log",
        backref="job",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="Log.created_at"
    )

    items = db.relationship(
        "ParsedItem",
        backref="job",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="ParsedItem.created_at"
    )

    def __repr__(self):
        return f"<Job {self.id} status={self.status} url={self.target_url}>"

    def to_dict(self):
        return {
            "id": self.id,
            "target_url": self.target_url,
            "status": self.status,
            "total_found": self.total_found,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "profile_id": self.profile_id,
            "block_reason": self.block_reason,
            "human_requested_at": self.human_requested_at.isoformat() if self.human_requested_at else None,
            "parse_mode": self.parse_mode,
            "max_pages": self.max_pages,
            "max_child_profiles": self.max_child_profiles,
            "max_detail_pages": self.max_detail_pages,
        }


class Log(db.Model):
    """
    Логи парсера.
    Используются для отображения прогресса во фронтенде в реальном времени.
    """
    __tablename__ = "logs"

    id = db.Column(db.Integer, primary_key=True)

    job_id = db.Column(
        db.Integer,
        db.ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    level = db.Column(
        db.String(20),
        nullable=False,
        default=LogLevel.INFO
    )

    message = db.Column(
        db.Text,
        nullable=False
    )

    created_at = db.Column(
        db.DateTime,
        default=utcnow,
        nullable=False,
        index=True
    )

    def __repr__(self):
        return f"<Log {self.id} job_id={self.job_id} level={self.level}>"

    def to_dict(self):
        return {
            "id": self.id,
            "job_id": self.job_id,
            "level": self.level,
            "message": self.message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class StructuringStatus:
    SKIPPED = "skipped"
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"

    ALL = [SKIPPED, PENDING, SUCCESS, FAILED]


class ParsedItem(db.Model):
    """
    Спаршенные элементы: гранты, акселераторы, ивенты и т.д.
    """
    __tablename__ = "parsed_items"

    id = db.Column(db.Integer, primary_key=True)

    job_id = db.Column(
        db.Integer,
        db.ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    title = db.Column(
        db.String(1024),
        nullable=False
    )

    url = db.Column(
        db.String(2048),
        nullable=False
    )

    raw_text = db.Column(
        db.Text,
        nullable=True
    )

    source_url = db.Column(
        db.String(2048),
        nullable=True
    )

    structured_data = db.Column(
        db.Text,
        nullable=True
    )

    structuring_status = db.Column(
        db.String(20),
        nullable=False,
        default=StructuringStatus.SKIPPED,
        index=True
    )

    structuring_error = db.Column(
        db.Text,
        nullable=True
    )

    created_at = db.Column(
        db.DateTime,
        default=utcnow,
        nullable=False,
        index=True
    )

    __table_args__ = (
        db.Index("ix_parsed_items_job_created", "job_id", "created_at"),
    )

    def __repr__(self):
        return f"<ParsedItem {self.id} job_id={self.job_id} title={self.title}>"

    def to_dict(self):
        import json as _json

        structured = None
        if self.structured_data:
            try:
                structured = _json.loads(self.structured_data)
            except (ValueError, TypeError):
                structured = None

        return {
            "id": self.id,
            "job_id": self.job_id,
            "title": self.title,
            "url": self.url,
            "raw_text": self.raw_text,
            "source_url": self.source_url,
            "structured_data": structured,
            "structuring_status": self.structuring_status,
            "structuring_error": self.structuring_error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def add_log(job_id, level, message):
    """
    Быстрая запись лога в базу.

    Пример:
        add_log(job.id, LogLevel.INFO, "Переход на страницу...")
    """
    level = (level or LogLevel.INFO).upper()

    if level not in LogLevel.ALL:
        level = LogLevel.INFO

    log = Log(
        job_id=job_id,
        level=level,
        message=str(message)
    )

    db.session.add(log)
    db.session.commit()

    return log


def set_job_status(job_id, status, total_found=None):
    """
    Обновляет статус задачи.

    Пример:
        set_job_status(job.id, JobStatus.RUNNING)
        set_job_status(job.id, JobStatus.COMPLETED, total_found=15)
    """
    job = db.session.get(Job, job_id)

    if not job:
        return None

    if status in JobStatus.ALL:
        job.status = status

    if total_found is not None:
        job.total_found = total_found

    db.session.commit()

    return job


def save_parsed_item(job_id, title, url, raw_text=None):
    """
    Сохраняет спаршенный элемент.

    Пример:
        save_parsed_item(
            job_id=job.id,
            title="Startup Grant 2026",
            url="https://www.f6s.com/some-page",
            raw_text="Описание гранта..."
        )
    """
    item = ParsedItem(
        job_id=job_id,
        title=title,
        url=url,
        raw_text=raw_text
    )

    db.session.add(item)
    db.session.commit()

    return item