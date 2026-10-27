import json
import sqlite3
import sys

from curl_cffi.requests import RequestsError

from morkovsk.config import DB_PATH
from morkovsk.ozon.game import GameClient, Status
from morkovsk.ozon.session import join_cookie, parse_cookie

FAB_HTML = "<html><head><title>Ozon</title></head><body>Доступ ограничен: fab_chlg</body></html>"


class FakeResponse:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text

    def json(self):
        return json.loads(self.text)


def fake_post(status_code=None, text="", error=None):
    def post(*_args, **_kwargs):
        if error:
            raise error
        return FakeResponse(status_code, text)

    return post


def first_enabled_account():
    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT cookie, proxy FROM accounts WHERE enabled=1 LIMIT 1").fetchone()
    if row is None:
        sys.exit("в базе нет включённых аккаунтов")
    return row["cookie"], row["proxy"]


def client_with(cookie, proxy, post=None):
    client = GameClient(cookie, proxy=proxy)
    client.warmup()
    if post:
        client.session.post = post
    return client


def main():
    cookie, proxy = first_enabled_account()
    without_tokens = join_cookie(
        {name: value for name, value in parse_cookie(cookie).items() if "token" not in name.lower()}
    )
    cases = [
        ("живая сессия", client_with(cookie, proxy), Status.OK),
        ("403 FAB на живой сессии", client_with(cookie, proxy, fake_post(403, FAB_HTML)), Status.TRANSPORT),
        (
            "обрыв прокси",
            client_with(cookie, proxy, fake_post(error=RequestsError("Failed to connect to proxy"))),
            Status.TRANSPORT,
        ),
        ("не-JSON от прокси", client_with(cookie, proxy, fake_post(200, "<html>proxy error</html>")), Status.TRANSPORT),
        ("сессия без токенов", client_with(without_tokens, proxy), Status.DEAD),
    ]
    passed = 0
    for title, client, expected in cases:
        try:
            got = client.auth_state()
        except RequestsError:
            got = "exception"
        ok = got == expected
        passed += ok
        print(f"[{'PASS' if ok else 'FAIL'}] {title}: {got} (ожидали {expected})")
    print(f"{passed}/{len(cases)} passed")
    sys.exit(0 if passed == len(cases) else 1)


if __name__ == "__main__":
    main()
