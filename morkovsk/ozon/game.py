import base64
import datetime
import json
import random
import re
import time
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import quote

from curl_cffi import requests as cr
from curl_cffi.requests import RequestsError

from morkovsk.ozon.session import ACCESS_TOKEN, join_cookie, parse_cookie, proxy_url, token_user_id

USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
)
OZON = "https://www.ozon.ru"
COMPOSER_ACTION = f"{OZON}/api/composer-api.bx/_action/"
PUSH_APPLICATION = {"platform": "IOS", "name": "BX", "build": "PROD"}
DELAY_RANGE = (2.0, 7.0)
LONG_DELAY_RANGE = (8.0, 14.0)
LONG_DELAY_CHANCE = 0.10

MOBILE_MARKERS = {
    "MOBILE_APP_TYPE": "ozonapp_ios",
    "x-o3-app-name": "ozonapp_ios",
    "x-o3-app-version": "19.23.0(1013)",
    "x-o3-language": "ru",
    "x-o3-localization-version": "2",
    "x-o3-os-version": "27.0",
    "x-o3-sys-languages": "ru|en",
}

BALANCE_WIDGET = (
    "operationsHistory-5108270-default-1",
    {
        "url": "/gameapp/history",
        "ci": {
            "config": "30-4",
            "vertical": "regulardraw",
            "name": "operationsHistory",
            "version": 1,
            "layoutID": 263951,
            "id": 5108270,
        },
    },
)
TASKS_WIDGET = (
    "tasksList-5108193-default-1",
    {
        "url": "/gameapp/tasks?perehod=tabbar",
        "ci": {
            "config": "27-2",
            "vertical": "regulardraw",
            "name": "tasksList",
            "version": 1,
            "layoutID": 263942,
            "id": 5108193,
        },
    },
)

TASK_KINDS = [
    (("купите", "на сумм"), "paid"),
    (("корзин",), "cart"),
    (("посетите", "раздел"), "visit"),
    (("отзыв",), "review"),
    (("каждый день", "заходите"), "daily"),
]


class Status(StrEnum):
    OK = "ok"
    DEAD = "dead"
    TRANSPORT = "transport"
    STOPPED = "stopped"
    BUSY = "busy"


@dataclass
class Task:
    id: str
    name: str
    kind: str
    reward: str
    done: bool


@dataclass
class MainPage:
    tickets: int | None
    lottery_deadline: datetime.datetime | None


@dataclass
class FarmResult:
    status: Status
    start_balance: int | None = None
    balance: int | None = None
    completed: list[str] = field(default_factory=list)


def classify(name):
    lowered = (name or "").lower()
    for markers, kind in TASK_KINDS:
        if any(marker in lowered for marker in markers):
            return kind
    return "other"


def _find_tasks(node):
    found = []
    if isinstance(node, dict):
        if isinstance(node.get("tasks"), list):
            found += node["tasks"]
        for value in node.values():
            found += _find_tasks(value)
    elif isinstance(node, list):
        for item in node:
            found += _find_tasks(item)
    return found


class GameClient:
    def __init__(self, cookie, proxy=None, do_cart=False, on_log=None):
        self.cookie = cookie
        self.do_cart = do_cart
        self.log = on_log or (lambda _message: None)
        options = {"impersonate": "safari17_0", "timeout": 30}
        if proxy:
            url = proxy_url(proxy) or proxy
            options["proxies"] = {"http": url, "https": url}
        self.session = cr.Session(**options)
        for name, value in {**parse_cookie(cookie), **MOBILE_MARKERS}.items():
            self.session.cookies.set(name, value, domain=".ozon.ru")
        self._original_access_token = self._cookie_pairs().get(ACCESS_TOKEN)

    @staticmethod
    def pause():
        delay = random.uniform(*DELAY_RANGE)
        if random.random() < LONG_DELAY_CHANCE:
            delay += random.uniform(*LONG_DELAY_RANGE)
        time.sleep(delay)

    def _cookie_pairs(self):
        pairs = parse_cookie(self.cookie)
        for jar_cookie in self.session.cookies.jar:
            if jar_cookie.value is not None:
                pairs[jar_cookie.name] = jar_cookie.value
        return pairs

    def _anonymous(self):
        return token_user_id(self._cookie_pairs().get(ACCESS_TOKEN, "")) == "0"

    def refreshed_cookie(self):
        new_token = self._cookie_pairs().get(ACCESS_TOKEN)
        if not new_token or not self._original_access_token or new_token == self._original_access_token:
            return None
        if self._anonymous():
            self.log("переизданный токен анонимный (userid=0) — не сохраняю")
            return None
        return join_cookie(self._cookie_pairs())

    def _page(self, path):
        return self.session.get(OZON + path, headers={"user-agent": USER_AGENT, "accept-language": "ru"}, timeout=30)

    def _post(self, url, body):
        headers = {
            "content-type": "application/json",
            "accept": "application/json",
            "x-o3-app-name": "mweb_client",
            "origin": OZON,
            "referer": f"{OZON}/gameapp/tasks",
            "user-agent": USER_AGENT,
        }
        return self.session.post(url, data=body, headers=headers, timeout=30)

    def warmup(self):
        try:
            self._page("/gameapp/history")
        except RequestsError as error:
            self.log(f"warmup: {error}")

    def _widget(self, widget):
        widget_id, config = widget
        async_data = base64.b64encode(json.dumps(config, ensure_ascii=False).encode()).decode()
        body = json.dumps(
            {"asyncData": async_data, "extraBody": {}, "url": config["url"], "componentName": config["url"]},
            ensure_ascii=False,
        )
        try:
            response = self._post(f"{OZON}/api/composer-api.bx/widget/json/v2?widgetStateId={widget_id}", body)
        except RequestsError as error:
            return None, None, f"{type(error).__name__}: {str(error)[:70]}"
        try:
            state = response.json().get("state")
            state = json.loads(state) if isinstance(state, str) else state
        except (ValueError, AttributeError):
            return response.status_code, None, "ответ не JSON"
        return response.status_code, state, None

    def action(self, name, body):
        response = self._post(COMPOSER_ACTION + name, json.dumps(body, ensure_ascii=False))
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, response.text

    def _push_visit(self, page):
        return self.action("inAppPushAction", {"application": PUSH_APPLICATION, "page": page})

    @staticmethod
    def _balance_of(state):
        if not state:
            return None
        match = re.search(r'"Текущий баланс"[\s\S]{0,160}?"value":\s*"([^"]+)"', json.dumps(state, ensure_ascii=False))
        if not match:
            return None
        digits = re.sub(r"\D", "", match.group(1))
        return int(digits) if digits else None

    def balance(self):
        return self._balance_of(self._widget(BALANCE_WIDGET)[1])

    def auth_state(self, tries=3):
        for attempt in range(tries):
            http_status, state, error = self._widget(BALANCE_WIDGET)
            if self._balance_of(state) is not None:
                return Status.OK
            if self._anonymous():
                return Status.DEAD
            self.log(f"проба сессии {attempt + 1}/{tries}: HTTP {http_status} {error or 'пустой ответ'}")
            if attempt < tries - 1:
                time.sleep(random.uniform(3, 8) * (attempt + 1))
        return Status.TRANSPORT

    def tasks(self):
        state = self._widget(TASKS_WIDGET)[1]
        tasks = {}
        for raw in _find_tasks(state or {}):
            if not isinstance(raw, dict) or "id" not in raw or str(raw["id"]) in tasks:
                continue
            name = raw.get("name", "")
            badge = ((raw.get("statusBadge") or {}).get("badge") or {}).get("text", "")
            tasks[str(raw["id"])] = Task(
                id=str(raw["id"]),
                name=name,
                kind=classify(name),
                reward=str((raw.get("reward") or {}).get("value", "?")),
                done=badge == "Выполнено",
            )
        return list(tasks.values())

    def complete_visit(self, task_id):
        http_status, data = self.action("regulardraw/collectVisitReward", {"task_id": task_id})
        if http_status != 200 or not isinstance(data, dict):
            return False
        link = (data.get("action") or {}).get("link") or ""
        if not link:
            match = re.search(r'"link":\s*"([^"]+)"', json.dumps(data))
            link = match.group(1) if match else ""
        if not link:
            return False
        page = re.sub(r"^ozon://", "/", link).split("?")[0]
        if not page.endswith("/"):
            page += "/"
        self.pause()
        push_status, _ = self._push_visit(page)
        return push_status == 200

    def _entrypoint_page(self, path):
        return self._post(f"{OZON}/api/entrypoint-api.bx/page/json/v2?url={quote(path, safe='')}", "{}").text

    def complete_cart(self, task_id):
        modal = self._entrypoint_page(f"/modal/taskmrk/?task_id={task_id}&page_changed=true")
        highlight = re.search(r"/highlight/[a-z0-9\-]+", modal)
        if not highlight:
            return False
        page = highlight.group(0) + "/"
        page_json = self._entrypoint_page(page + "?miniapp=main")
        sku = re.search(r'skuId["\\:\s]+(\d{6,})', page_json) or re.search(r"/product/(\d{6,})", page_json)
        if not sku:
            return False
        self.pause()
        item = {"params": {"spsDiscountNotify": "true", "qtyDiscountNotify": "true"}, "quantity": 1, "id": int(sku[1])}
        response = self._post(COMPOSER_ACTION + "v2/addToCart", json.dumps([item]))
        added = response.status_code == 200 and '"success":true' in response.text
        self.pause()
        self._push_visit(page)
        return added

    def free_lukoshko_key(self):
        try:
            html = self._page("/gameapp/lukoshko/?perehod=morkovsk_tabbar").text or ""
        except RequestsError as error:
            self.log(f"лукошко: {error}")
            return None
        for match in re.finditer(r"data-state='([^']*)'", html):
            blob = match.group(1)
            if "mainButton" not in blob or "openLootbox" not in blob:
                continue
            try:
                button = json.loads(blob).get("mainButton")
            except json.JSONDecodeError:
                continue
            if not isinstance(button, dict):
                continue
            button_action = (button.get("common") or {}).get("action") or {}
            if "openLootbox" not in (button_action.get("link") or ""):
                continue
            if button.get("disabled") or button.get("badge"):
                return None
            return (button_action.get("params") or {}).get("idempotencyKey")
        return None

    def spin_lukoshko(self, key):
        http_status, data = self.action("v2/regulardraw/openLootbox", {"idempotencyKey": key})
        if http_status != 200 or not isinstance(data, dict):
            return None
        return ((data.get("data") or {}).get("title") or {}).get("title") or "приз"

    def main_page(self):
        try:
            html = self._page("/gameapp/main/?perehod=tabbar").text or ""
        except RequestsError as error:
            self.log(f"главная игры: {error}")
            return MainPage(tickets=None, lottery_deadline=None)
        tickets = re.search(r'"ticketsAmount"\s*:\s*\{\s*"value"\s*:\s*"?(\d+)"?', html)
        deadline = re.search(r'ticketsPurchaseExp"?\s*:\s*\{\s*"date"\s*:\s*"([^"]+)"', html)
        return MainPage(
            tickets=int(tickets[1]) if tickets else None,
            lottery_deadline=datetime.datetime.strptime(deadline[1], "%Y-%m-%dT%H:%M:%S%z") if deadline else None,
        )

    def farm(self, stopped, on_event):
        self.warmup()
        self.pause()
        state = self.auth_state()
        if state != Status.OK:
            return FarmResult(status=state)
        self._push_visit("/gameapp/main/")
        self.pause()
        result = FarmResult(status=Status.OK, start_balance=self.balance())
        tasks = [task for task in self.tasks() if not task.done]
        if not self._run_tasks(tasks, stopped, on_event, result):
            result.status = Status.STOPPED
            result.balance = self.balance()
            return result
        self._collect_daily(tasks, stopped, on_event, result)
        self._spin_free_lukoshko(stopped, on_event, result)
        result.balance = self.balance()
        return result

    def _run_tasks(self, tasks, stopped, on_event, result):
        todo = [task for task in tasks if task.kind == "visit" or (self.do_cart and task.kind == "cart")]
        random.shuffle(todo)
        for task in todo:
            if stopped():
                return False
            complete = self.complete_visit if task.kind == "visit" else self.complete_cart
            if complete(task.id):
                result.completed.append(f"{task.name[:40]} +{task.reward}")
                on_event(task.kind, task.name, task.reward, self.balance())
            self.pause()
        return True

    def _collect_daily(self, tasks, stopped, on_event, result):
        for task in [task for task in tasks if task.kind == "daily"]:
            if stopped():
                return
            before = self.balance()
            http_status, _ = self.action("regulardraw/collectVisitReward", {"task_id": task.id})
            self.pause()
            after = self.balance()
            if http_status == 200 and before is not None and after is not None and after > before:
                result.completed.append(f"ежедневный бонус +{after - before}")
                on_event("daily", "Ежедневный бонус", after - before, after)

    def _spin_free_lukoshko(self, stopped, on_event, result):
        if stopped():
            return
        key = self.free_lukoshko_key()
        if not key:
            return
        self.pause()
        prize = self.spin_lukoshko(key)
        if prize:
            result.completed.append(f"лукошко: {prize}")
            on_event("lukoshko", prize, None, self.balance())
