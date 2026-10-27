from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from morkovsk.config import FP_CHROMIUM, FP_HEADLESS
from morkovsk.ozon.session import (
    ACCESS_TOKEN,
    REFRESH_TOKEN,
    is_logged_in,
    join_cookie,
    parse_cookie,
    split_proxy,
    token_user_id,
)

FINGERPRINT_ARGS = [
    "--fingerprint-platform=windows",
    "--fingerprint-brand=Chrome",
    "--lang=ru-RU",
    "--accept-lang=ru-RU,ru",
    "--timezone=Europe/Moscow",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-non-proxied-udp",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]
DEFAULT_FINGERPRINT_SEED = 424242
REFRESH_PAGES = ("https://www.ozon.ru/my", "https://www.ozon.ru/gameapp/main/")
SKIPPED_COOKIES = {"abt_data"}


def fingerprint_seed(value):
    return int(value) % 900000 + 1000


def launch(playwright, proxy, seed, headless=False, extra_args=()):
    parts = split_proxy(proxy) if proxy else None
    browser_proxy = None
    if parts:
        host, port, user, password = parts
        browser_proxy = {"server": f"http://{host}:{port}"}
        if user:
            browser_proxy |= {"username": user, "password": password}
    return playwright.chromium.launch(
        executable_path=FP_CHROMIUM,
        headless=headless,
        proxy=browser_proxy,
        args=[f"--fingerprint={seed}", *FINGERPRINT_ARGS, *extra_args],
    )


def _browse_for_cookies(pairs, proxy, log):
    user_id = token_user_id(pairs.get(ACCESS_TOKEN, ""))
    seed = fingerprint_seed(user_id) if user_id else DEFAULT_FINGERPRINT_SEED
    with sync_playwright() as playwright:
        browser = launch(playwright, proxy, seed, headless=FP_HEADLESS)
        try:
            context = browser.new_context(locale="ru-RU")
            for name, value in pairs.items():
                if name in SKIPPED_COOKIES:
                    continue
                cookie = {"name": name, "value": value, "domain": ".ozon.ru", "path": "/", "secure": True}
                try:
                    context.add_cookies([cookie | {"sameSite": "Lax"}])
                except PlaywrightError as error:
                    log(f"cookie {name} пропущена: {str(error)[:50]}")
            page = context.new_page()
            for url in REFRESH_PAGES:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(4000)
                except PlaywrightError as error:
                    log(f"{url}: {str(error)[:50]}")
            return {cookie["name"]: cookie["value"] for cookie in context.cookies()}
        finally:
            browser.close()


def refresh_cookie(cookie, proxy, log):
    pairs = parse_cookie(cookie)
    if REFRESH_TOKEN not in pairs:
        log("в cookie нет refresh-token — восстановить сессию нельзя")
        return None
    try:
        fresh_pairs = _browse_for_cookies(pairs, proxy, log)
    except PlaywrightError as error:
        log(f"браузер: {str(error)[:80]}")
        return None
    if not is_logged_in(fresh_pairs):
        log("Ozon выдал анонимный токен — refresh-token мёртв, нужна новая cookie")
        return None
    log("сессия восстановлена через fingerprint-chromium")
    return join_cookie(fresh_pairs)
