import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / "f6s_profile"

START_URL = "https://www.f6s.com/login"
CHECK_URL = "https://www.f6s.com/events"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _stealth(context):
    """Стираем следы автоматизации ещё на этапе логина."""
    context.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        window.chrome = window.chrome || { runtime: {} };
        """
    )


def _open_context(playwright):
    """
    Открываем ВИДИМЫЙ системный Chrome с персистентным профилем.
    Если системного Chrome нет — fallback на встроенный Chromium.
    """
    common = dict(
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        user_agent=USER_AGENT,
        viewport={"width": 1440, "height": 900},
        locale="en-US",
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ],
    )

    try:
        ctx = playwright.chromium.launch_persistent_context(
            channel="chrome", **common
        )
        print(">> Использую системный Google Chrome.")
        return ctx
    except Exception as e:
        print(f">> Системный Chrome недоступен ({e}). Беру встроенный Chromium.")
        return playwright.chromium.launch_persistent_context(**common)


def main():
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = _open_context(p)
        _stealth(context)

        # Новая страница — чтобы init-скрипт точно применился.
        page = context.new_page()
        page.goto(START_URL)

        print("=" * 64)
        print("БРАУЗЕР ОТКРЫТ. Сделай по шагам В ЭТОМ ОКНЕ:")
        print(" 1) Залогинься на F6S по EMAIL + паролю (НЕ через Google).")
        print(" 2) В адресной строке этого же окна открой:")
        print(f"    {CHECK_URL}")
        print(" 3) Если увидишь капчу/проверку 'are you a bot' — ПРОЙДИ ЕЁ РУКАМИ")
        print("    (поставь галку / реши задачу) и дождись, пока появится")
        print("    РЕАЛЬНЫЙ список ивентов (карточки), а не заглушка.")
        print(" 4) ТОЛЬКО когда видишь карточки — вернись в терминал и нажми Enter.")
        print("=" * 64)

        input("Нажми Enter, чтобы сохранить профиль...")

        context.close()

    print(f"Профиль сохранён: {PROFILE_DIR}")
    print("Старый f6s_auth.json больше не нужен — парсер его не читает.")


if __name__ == "__main__":
    main()