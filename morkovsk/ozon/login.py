import json
import os
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from morkovsk.ozon.browser import fingerprint_seed, launch
from morkovsk.ozon.session import is_logged_in, join_cookie

LOGIN_URL = "https://id.ozon.ru/"
WAIT_FOR_LOGIN_SECONDS = 300
WAIT_FOR_RESEND_SECONDS = 110
QR_RESHOT_SECONDS = 25
OTHER_WAY_LINKS = ("Войти другим способом", "другим способом")
CAPTCHA_MARKERS = ("fab_chlg", "не робот", "captcha", "подтвердите, что вы")
QR_MARKERS = ("наведите камеру", "qr-код", "по qr", "подтвердите вход в приложении")
PUSH_CONFIRM_MARKERS = ("наведите камеру", "подтвердите вход в приложении", "по qr")
CODE_SCREEN_MARKERS = ("введите код", "код из")
CONFIRM_BUTTONS = ("Подтвердить", "Войти", "Продолжить", "Далее")


class LoginSession:
    def __init__(self, session_dir, page, context):
        self.session_dir = session_dir
        self.page = page
        self.context = context
        self.code_file = os.path.join(session_dir, "code.txt")
        self.cookie_file = os.path.join(session_dir, "cookie.txt")

    def status(self, step, **fields):
        write_status(self.session_dir, step, **fields)

    def shot(self, name="screen.png"):
        try:
            self.page.screenshot(path=os.path.join(self.session_dir, name))
        except PlaywrightError as error:
            print(f"screenshot failed: {error}", file=sys.stderr)

    def logged_in_cookie(self):
        cookies = {cookie["name"]: cookie["value"] for cookie in self.context.cookies()}
        return join_cookie(cookies) if is_logged_in(cookies) else None

    def save_if_logged_in(self):
        cookie = self.logged_in_cookie()
        if not cookie:
            return False
        Path(self.cookie_file).write_text(cookie, encoding="utf-8")
        self.shot()
        self.status("LOGGED_IN")
        return True

    def content(self):
        return self.page.content().lower()

    def is_captcha(self):
        content = self.content()
        return any(marker in content for marker in CAPTCHA_MARKERS)

    def click_text(self, *variants):
        for text in variants:
            element = self.page.query_selector(f"text={text}")
            if element and element.is_visible():
                try:
                    element.click()
                    return True
                except PlaywrightError:
                    continue
        return False


def write_status(session_dir, step, **fields):
    fields["step"] = step
    Path(session_dir, "status.json").write_text(json.dumps(fields, ensure_ascii=False), encoding="utf-8")


def _click_submit(page):
    for selector in ("button:has-text('Войти')", "button[type=submit]"):
        for element in page.query_selector_all(selector):
            try:
                text = element.inner_text() or ""
                if element.is_visible() and "VK" not in text and "почте" not in text:
                    element.click()
                    return True
            except PlaywrightError:
                continue
    return False


def _find_resend(page):
    for element in page.query_selector_all("button, [role=button], a"):
        try:
            text = element.inner_text() or ""
            if "Получить новый код" in text and element.is_visible():
                return element, text.replace("\n", " ").strip()
        except PlaywrightError:
            continue
    return None, ""


def _reach_code_screen(login):
    for _ in range(4):
        content = login.content()
        if any(marker in content for marker in CODE_SCREEN_MARKERS):
            break
        if any(marker in content for marker in PUSH_CONFIRM_MARKERS):
            login.click_text(*OTHER_WAY_LINKS)
            login.page.wait_for_timeout(3500)
            continue
        break


def _request_sms(login):
    resend_done = False
    wait_end = time.time() + WAIT_FOR_RESEND_SECONDS
    while time.time() < wait_end:
        element, text = _find_resend(login.page)
        if element is None:
            break
        if "Через" not in text and element.get_attribute("disabled") is None:
            try:
                element.click()
                resend_done = True
            except PlaywrightError as error:
                print(f"resend click failed: {error}", file=sys.stderr)
            login.page.wait_for_timeout(3500)
            break
        login.shot()
        login.status("WAIT_RESEND", resend=text)
        login.page.wait_for_timeout(3000)
    return resend_done


def _type_code(page, code):
    target = None
    for element in page.query_selector_all("input"):
        try:
            numeric = element.get_attribute("type") in ("tel", "text", "number") or (
                element.get_attribute("inputmode") == "numeric"
            )
            if element.is_visible() and numeric:
                target = element
                break
        except PlaywrightError:
            continue
    if target:
        target.click()
    page.keyboard.type(code, delay=130)
    page.wait_for_timeout(2000)
    for name in CONFIRM_BUTTONS:
        button = page.query_selector(f"button:has-text('{name}')")
        if button and button.is_visible():
            try:
                button.click()
                break
            except PlaywrightError:
                continue


def _login_by_phone(login, phone):
    page = login.page
    if not page.query_selector("input[type=tel]"):
        login.click_text(*OTHER_WAY_LINKS)
        page.wait_for_timeout(3000)
    phone_input = page.query_selector("input[type=tel]")
    if not phone_input:
        login.shot()
        login.status("FAILED", why="нет поля телефона")
        return
    phone_input.click()
    phone_input.fill("")
    page.keyboard.type(re.sub(r"\D", "", phone or "")[-10:], delay=70)
    page.wait_for_timeout(800)
    if not _click_submit(page):
        page.keyboard.press("Enter")
    page.wait_for_timeout(6000)
    _reach_code_screen(login)
    if login.is_captcha():
        login.shot()
        login.status("CAPTCHA")
    resend_done = _request_sms(login)
    login.shot()
    login.status("CODE_NEEDED", sms_requested=resend_done)
    deadline = time.time() + WAIT_FOR_LOGIN_SECONDS
    code_typed = False
    while time.time() < deadline:
        if login.save_if_logged_in():
            return
        if os.path.exists(login.code_file) and not code_typed:
            code = Path(login.code_file).read_text(encoding="utf-8").strip()
            if code:
                _type_code(page, code)
                code_typed = True
                login.status("CODE_SUBMITTED")
        time.sleep(2)
    login.shot()
    login.status("TIMEOUT")


def _login_by_qr(login):
    page = login.page
    if page.query_selector("input[type=tel]"):
        login.click_text(*OTHER_WAY_LINKS, "QR", "по QR")
        page.wait_for_timeout(3000)
    content = login.content()
    if not any(marker in content for marker in QR_MARKERS):
        login.shot()
        login.status("NO_QR")
        return
    login.shot()
    login.status("QR_READY")
    deadline = time.time() + WAIT_FOR_LOGIN_SECONDS
    last_shot = 0
    while time.time() < deadline:
        if login.save_if_logged_in():
            return
        if time.time() - last_shot > QR_RESHOT_SECONDS:
            login.shot()
            last_shot = time.time()
        time.sleep(2)
    login.status("TIMEOUT")


def main(session_dir, method, proxy, phone=None):
    os.makedirs(session_dir, exist_ok=True)
    write_status(session_dir, "STARTING")
    with sync_playwright() as playwright:
        seed = fingerprint_seed(abs(hash(f"{phone}{proxy}")))
        browser = launch(playwright, proxy, seed, extra_args=["--window-size=500,840"])
        try:
            context = browser.new_context(locale="ru-RU", viewport={"width": 480, "height": 820})
            page = context.new_page()
            login = LoginSession(session_dir, page, context)
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            if method == "phone":
                _login_by_phone(login, phone)
            else:
                _login_by_qr(login)
        except Exception as error:
            write_status(session_dir, "FAILED", why=str(error)[:150])
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)
