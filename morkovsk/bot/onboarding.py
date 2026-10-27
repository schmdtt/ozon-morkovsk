import asyncio
import json
import logging
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path

from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import FSInputFile

from morkovsk import storage
from morkovsk.bot import ui
from morkovsk.bot.client import bot
from morkovsk.config import LOGINS_DIR
from morkovsk.farming import notify, start_farm, validate
from morkovsk.ozon.session import ACCESS_TOKEN

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOGIN_TIMEOUT_SECONDS = 330
LOGIN_POLL_SECONDS = 2
COOKIE_DEBOUNCE_SECONDS = 2.0

logger = logging.getLogger(__name__)


class AddAcc(StatesGroup):
    label = State()
    proxy = State()
    cookie = State()
    phone = State()
    code = State()


_cookie_parts = defaultdict(dict)
_cookie_save_tasks = {}


def collect_cookie(message, state, on_cookie):
    user_id = message.from_user.id
    _cookie_parts[user_id][message.message_id] = message.text or ""
    pending = _cookie_save_tasks.get(user_id)
    if pending and not pending.done():
        pending.cancel()

    async def save_after_pause():
        await asyncio.sleep(COOKIE_DEBOUNCE_SECONDS)
        parts = _cookie_parts[user_id]
        cookie = "".join(parts[message_id] for message_id in sorted(parts)).strip()
        _cookie_save_tasks.pop(user_id, None)
        if ACCESS_TOKEN not in cookie:
            await message.answer(ui.COOKIE_INCOMPLETE, parse_mode="HTML")
            return
        _cookie_parts.pop(user_id, None)
        progress = await message.answer("Проверяю cookie…")
        balance = await asyncio.to_thread(validate, cookie, (await state.get_data()).get("proxy"))
        if balance is None:
            await progress.edit_text(ui.COOKIE_REJECTED, parse_mode="HTML")
            return
        await on_cookie(message, state, progress, cookie, balance)

    _cookie_save_tasks[user_id] = asyncio.create_task(save_after_pause())


async def add_account(owner, label, cookie, proxy, balance):
    acc_id = storage.add_account(owner, label, cookie, proxy)
    storage.mark_run(acc_id, balance)
    storage.log_balance(acc_id, balance)
    start_farm(storage.get_account(acc_id))


async def add_by_cookie(message, state, progress, cookie, balance):
    data = await state.get_data()
    await state.clear()
    label = data.get("label") or "Аккаунт"
    if storage.proxy_used(data["proxy"]):
        await progress.edit_text("✅ Аккаунт с этим прокси уже добавлен.", reply_markup=ui.to_accounts())
        return
    await add_account(message.from_user.id, label, cookie, data["proxy"], balance)
    await progress.edit_text(ui.account_added(label, balance), reply_markup=ui.to_accounts(), parse_mode="HTML")


async def replace_cookie(message, state, progress, cookie, balance):
    data = await state.get_data()
    await state.clear()
    account = storage.get_account(data["acc_id"], message.from_user.id)
    if not account:
        await progress.edit_text("Аккаунт не найден.")
        return
    storage.update_account(account["id"], cookie=cookie)
    storage.mark_run(account["id"], balance)
    storage.log_balance(account["id"], balance)
    start_farm(storage.get_account(account["id"]))
    await progress.edit_text(f"✅ Cookie обновлён. Баланс: {balance} 🥕", reply_markup=ui.to_accounts())


def _read_status(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


async def _send_screen(user_id, screen, caption):
    if not screen.exists():
        await notify(user_id, caption, parse_mode="HTML")
        return
    try:
        await bot.send_photo(user_id, FSInputFile(screen), caption=caption, parse_mode="HTML")
    except TelegramAPIError as error:
        logger.warning("send screen to %s failed: %s", user_id, error)


async def _watch_worker(user_id, session_dir, state):
    screen = session_dir / "screen.png"
    cookie_file = session_dir / "cookie.txt"
    announced = set()
    deadline = time.monotonic() + LOGIN_TIMEOUT_SECONDS
    while time.monotonic() < deadline and not cookie_file.exists():
        await asyncio.sleep(LOGIN_POLL_SECONDS)
        status = _read_status(session_dir / "status.json")
        step = status.get("step")
        if step in announced:
            continue
        if step == "QR_READY" and screen.exists():
            await _send_screen(user_id, screen, ui.LOGIN_QR)
        elif step == "WAIT_RESEND":
            await notify(user_id, ui.login_wait_resend(status.get("resend", "")), parse_mode="HTML")
        elif step == "CODE_NEEDED":
            await state.set_state(AddAcc.code)
            await state.update_data(login_dir=str(session_dir))
            await _send_screen(user_id, screen, ui.login_code_needed(status.get("sms_requested", False)))
        elif step == "CAPTCHA":
            await _send_screen(user_id, screen, ui.LOGIN_CAPTCHA)
        elif step == "NO_QR":
            await notify(user_id, ui.LOGIN_NO_QR)
            break
        elif step in ("FAILED", "TIMEOUT"):
            break
        if step:
            announced.add(step)
    return cookie_file.read_text(encoding="utf-8").strip() if cookie_file.exists() else None


async def run_login(user_id, method, phone, state):
    data = await state.get_data()
    label, proxy = data.get("label") or "Аккаунт", data["proxy"]
    session_dir = LOGINS_DIR / f"{user_id}_{int(time.time())}"
    session_dir.mkdir(parents=True, exist_ok=True)
    args = [sys.executable, "-m", "morkovsk.ozon.login", str(session_dir), method, proxy, *([phone] if phone else [])]
    process = await asyncio.create_subprocess_exec(*args, cwd=PROJECT_ROOT)
    try:
        cookie = await _watch_worker(user_id, session_dir, state)
    finally:
        if process.returncode is None:
            process.kill()
        await state.clear()
        shutil.rmtree(session_dir, ignore_errors=True)
    if not cookie:
        await notify(user_id, ui.LOGIN_FAILED)
        return
    balance = await asyncio.to_thread(validate, cookie, proxy)
    if balance is None:
        await notify(user_id, "Вход прошёл, но игра не открывается. Попробуй ещё раз или добавь через cookie.")
        return
    await add_account(user_id, label, cookie, proxy, balance)
    await notify(user_id, ui.account_added(label, balance), parse_mode="HTML")


def submit_login_code(session_dir, code):
    Path(session_dir, "code.txt").write_text(code, encoding="utf-8")
