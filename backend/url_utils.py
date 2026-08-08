from dataclasses import dataclass
from urllib.parse import urlsplit

from models import SiteProfile


class UrlValidationError(ValueError):
    """
    Ошибка валидации URL.
    Используется, например, в POST /api/parse для возврата 400.
    """
    pass


@dataclass(frozen=True)
class NormalizedUrl:
    """
    Нормализованное представление URL для матчинга профиля.

    Важно:
    - original сохраняется как входная точка для executor и Job.target_url;
    - query/fragment НЕ участвуют в матчинге профиля;
    - domain нормализуется без www.
    """
    original: str
    scheme: str
    domain: str
    path: str
    query: str
    fragment: str


def normalize_path_prefix(prefix: str) -> str:
    """
    Приводит path_prefix профиля к единому виду.

    Примеры:
    "" -> "/"
    "/" -> "/"
    "/programs/" -> "/programs"
    "programs" -> "/programs"
    """
    p = (prefix or "").strip()

    if not p or p == "/":
        return "/"

    if not p.startswith("/"):
        p = "/" + p

    while len(p) > 1 and p.endswith("/"):
        p = p[:-1]

    return p


def normalize_target_url(raw_url: str) -> NormalizedUrl:
    """
    Нормализует URL пользователя для последующего матчинга с SiteProfile.

    Правила из спецификации:
    - только http/https;
    - host в нижнем регистре;
    - убрать www.;
    - path оставить как есть, если пуст — "/";
    - query и fragment игнорируются для матчинга, но сохраняются в объекте;
    - original остаётся исходной строкой после strip().
    """
    url = (raw_url or "").strip()

    if not url:
        raise UrlValidationError("URL is empty")

    parts = urlsplit(url)

    scheme = (parts.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise UrlValidationError("URL must start with http:// or https://")

    host = (parts.hostname or "").lower()
    if not host:
        raise UrlValidationError("URL has no host")

    if host.startswith("www."):
        host = host[4:]

    path = parts.path or "/"
    if not path.startswith("/"):
        path = "/" + path

    return NormalizedUrl(
        original=url,
        scheme=scheme,
        domain=host,
        path=path,
        query=parts.query,
        fragment=parts.fragment,
    )


def _path_matches(path: str, prefix: str) -> bool:
    """
    Проверяет, подходит ли путь под префикс профиля.

    Правила:
    - "/" подходит всегда;
    - точное совпадение подходит;
    - путь может начинаться с prefix + "/".
    """
    prefix = normalize_path_prefix(prefix)
    path = path or "/"

    if prefix == "/":
        return True

    if path == prefix:
        return True

    return path.startswith(prefix + "/")


def find_best_profile(normalized: NormalizedUrl):
    """
    Ищет лучший SiteProfile для нормализованного URL.

    Логика:
    - домен должен совпадать;
    - path_prefix должен подходить под путь;
    - из подходящих выбирается самый длинный path_prefix.

    Важно:
    - неактивные профили здесь НЕ фильтруются,
      потому что дальше executor/analyzer должен сам решать,
      наступил ли кулдаун и можно ли пересканировать профиль.
    """
    candidates = SiteProfile.query.filter_by(domain=normalized.domain).all()

    best_profile = None
    best_prefix_len = -1

    for profile in candidates:
        prefix = normalize_path_prefix(profile.path_prefix)

        if not _path_matches(normalized.path, prefix):
            continue

        prefix_len = len(prefix)

        if prefix_len > best_prefix_len:
            best_profile = profile
            best_prefix_len = prefix_len

    return best_profile


def find_best_profile_for_url(raw_url: str):
    """
    Удобная обёртка:
    - нормализует URL;
    - сразу ищет профиль.

    Возвращает:
        (NormalizedUrl, SiteProfile | None)
    """
    normalized = normalize_target_url(raw_url)
    profile = find_best_profile(normalized)
    return normalized, profile