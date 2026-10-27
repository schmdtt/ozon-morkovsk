import re

ACCESS_TOKEN = "__Secure-access-token"
REFRESH_TOKEN = "__Secure-refresh-token"
COOKIE_SPLIT = re.compile(r"[;,] (?=[A-Za-z0-9_\-]+=)")
TOKEN_USER = re.compile(r"\d+\.(\d+)\.")


def parse_cookie(cookie):
    pairs = {}
    for part in COOKIE_SPLIT.split(cookie or ""):
        if "=" in part:
            name, value = part.split("=", 1)
            pairs[name.strip()] = value.strip()
    return pairs


def join_cookie(pairs):
    return "; ".join(f"{name}={value}" for name, value in pairs.items())


def token_user_id(token):
    match = TOKEN_USER.match(token or "")
    return match[1] if match else None


def is_logged_in(pairs):
    user_id = token_user_id(pairs.get(ACCESS_TOKEN, ""))
    return user_id is not None and user_id != "0"


def split_proxy(proxy):
    parts = (proxy or "").strip().split(":")
    if len(parts) == 4:
        return parts
    if len(parts) == 2:
        return [*parts, None, None]
    return None


def proxy_url(proxy):
    parts = split_proxy(proxy)
    if parts is None:
        return None
    host, port, user, password = parts
    return f"http://{user}:{password}@{host}:{port}" if user else f"http://{host}:{port}"
