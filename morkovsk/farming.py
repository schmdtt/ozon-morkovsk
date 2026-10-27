import asyncio
import datetime
import logging
import random

from aiogram.exceptions import TelegramAPIError
from curl_cffi import requests as cr
from curl_cffi.requests import RequestsError

from morkovsk import storage
from morkovsk.bot import ui
from morkovsk.bot.client import bot
from morkovsk.ozon.browser import refresh_cookie
from morkovsk.ozon.game import FarmResult, GameClient, Status
from morkovsk.ozon.session import proxy_url

PROXY_OK_STATUSES = (200, 301, 302, 307, 308)
LOTTERY_THRESHOLDS = [24, 6, 1]
LOTTERY_WINDOW_HOURS = 1.5
LOTTERY_CHECK_SECONDS = 20 * 60
FIRST_FARM_DELAY_SECONDS = 90
FARM_PAUSE_RANGE = (30 * 60, 45 * 60)

logger = logging.getLogger(__name__)
_farming_accounts = set()
_background_tasks = set()


def client_for(account, cookie=None):
    return GameClient(
        cookie or account["cookie"], proxy=account.get("proxy"), do_cart=bool(account["do_cart"]), on_log=logger.info
    )


def validate(cookie, proxy=None):
    try:
        client = GameClient(cookie, proxy=proxy, on_log=logger.info)
        client.warmup()
        balance = client.balance()
    except RequestsError as error:
        logger.warning("validate: %s", error)
        return None
    return balance


def check_proxy(proxy):
    url = proxy_url(proxy)
    if url is None:
        return False
    try:
        response = cr.get(
            "https://www.ozon.ru/",
            impersonate="safari17_0",
            proxies={"http": url, "https": url},
            timeout=15,
            allow_redirects=False,
        )
    except RequestsError as error:
        logger.warning("check_proxy: %s", error)
        return False
    return response.status_code in PROXY_OK_STATUSES


def _save_progress(account, client, balance):
    tickets = client.main_page().tickets
    if tickets is not None:
        storage.update_account(account["id"], tickets=tickets)
    new_cookie = client.refreshed_cookie()
    if new_cookie:
        storage.update_account(account["id"], cookie=new_cookie)
    storage.mark_run(account["id"], balance)
    storage.log_balance(account["id"], balance)


def _refresh_balance(account):
    client = client_for(account)
    client.warmup()
    balance = client.balance()
    if balance is None:
        storage.mark_run(account["id"], account["last_balance"], session_dead=True)
        return
    _save_progress(account, client, balance)


def refresh_balances(owner):
    for account in storage.list_accounts(owner):
        if not account["enabled"]:
            continue
        try:
            _refresh_balance(account)
        except RequestsError as error:
            logger.warning("refresh balance acc %s: %s", account["id"], error)


async def notify(chat_id, text, **options):
    try:
        await bot.send_message(chat_id, text, **options)
    except TelegramAPIError as error:
        logger.warning("send to %s failed: %s", chat_id, error)


def _farm_in_thread(account, cookie, stopped, on_event):
    client = client_for(account, cookie)
    result = client.farm(stopped, on_event)
    if result.status in (Status.OK, Status.STOPPED):
        _save_progress(account, client, result.balance)
    return result


async def farm_account(account):
    if account["id"] in _farming_accounts:
        return FarmResult(status=Status.BUSY)
    _farming_accounts.add(account["id"])
    try:
        owner = account["owner"]
        loop = asyncio.get_running_loop()

        def stopped():
            return not storage.has_access(owner)

        def on_event(kind, name, reward, balance):
            text = ui.farm_event(account["label"], kind, name, reward, balance)
            asyncio.run_coroutine_threadsafe(notify(owner, text, parse_mode="HTML"), loop)

        result = await asyncio.to_thread(_farm_in_thread, account, account["cookie"], stopped, on_event)
        if result.status == Status.DEAD and not stopped():
            logger.info("acc %s: сессия мертва, восстанавливаю через браузер", account["id"])
            fresh = await asyncio.to_thread(refresh_cookie, account["cookie"], account.get("proxy"), logger.info)
            if fresh:
                storage.update_account(account["id"], cookie=fresh)
                result = await asyncio.to_thread(_farm_in_thread, account, fresh, stopped, on_event)
        if result.status == Status.DEAD:
            storage.mark_run(account["id"], account["last_balance"], session_dead=True)
        return result
    finally:
        _farming_accounts.discard(account["id"])


def _forget_task(task):
    _background_tasks.discard(task)
    if not task.cancelled() and task.exception():
        logger.error("farm task failed", exc_info=task.exception())


def start_farm(account):
    task = asyncio.create_task(farm_account(account))
    _background_tasks.add(task)
    task.add_done_callback(_forget_task)


def _read_deadline(account):
    client = client_for(account)
    client.warmup()
    return client.main_page().lottery_deadline


def _due_threshold(hours_left, fired):
    for threshold in LOTTERY_THRESHOLDS:
        if threshold not in fired and (threshold - LOTTERY_WINDOW_HOURS) < hours_left <= threshold:
            return threshold
    return None


async def _remind_owner(owner, account):
    deadline = await asyncio.to_thread(_read_deadline, account)
    if deadline is None:
        return
    hours_left = (deadline - datetime.datetime.now(datetime.UTC)).total_seconds() / 3600
    if hours_left <= 0:
        return
    deadline_key = deadline.isoformat()
    stored_deadline, fired = storage.get_lottery_reminder(owner)
    if stored_deadline != deadline_key:
        fired = set()
    threshold = _due_threshold(hours_left, fired)
    if threshold:
        fired |= {hours for hours in LOTTERY_THRESHOLDS if hours >= threshold}
        await notify(owner, ui.lottery_reminder(threshold), parse_mode="HTML")
    storage.set_lottery_reminder(owner, deadline_key, fired)


async def lottery_scheduler():
    while True:
        await asyncio.sleep(LOTTERY_CHECK_SECONDS)
        first_account_by_owner = {}
        for account in storage.all_enabled_accounts():
            first_account_by_owner.setdefault(account["owner"], account)
        for owner, account in first_account_by_owner.items():
            try:
                await _remind_owner(owner, account)
            except RequestsError as error:
                logger.warning("lottery reminder for %s: %s", owner, error)


async def scheduler():
    await asyncio.sleep(FIRST_FARM_DELAY_SECONDS)
    while True:
        for account in storage.all_enabled_accounts():
            if not storage.has_access(account["owner"]):
                continue
            try:
                result = await farm_account(account)
            except RequestsError as error:
                logger.warning("farm acc %s: %s", account["id"], error)
                continue
            if result.status == Status.DEAD:
                await notify(account["owner"], ui.session_dead(account["label"]))
        await asyncio.sleep(random.uniform(*FARM_PAUSE_RANGE))
