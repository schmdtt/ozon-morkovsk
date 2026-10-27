import sqlite3
import threading
import time

from morkovsk.config import DATA_DIR, DB_PATH, OWNER_ID

MSK_OFFSET_SECONDS = 3 * 3600
DAY_SECONDS = 86400

_lock = threading.Lock()


def _connect():
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def _now():
    moment = time.gmtime(time.time() + MSK_OFFSET_SECONDS)
    return f"{moment.tm_mday:02d}.{moment.tm_mon:02d}.{moment.tm_year % 100:02d} {moment.tm_hour}:{moment.tm_min:02d}"


def init():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _lock, _connect() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS users(
            tg_id INTEGER PRIMARY KEY, has_access INTEGER DEFAULT 0,
            is_admin INTEGER DEFAULT 0, created_at TEXT)"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS accounts(
            id INTEGER PRIMARY KEY AUTOINCREMENT, owner INTEGER, label TEXT,
            cookie TEXT, do_cart INTEGER DEFAULT 0, session_dead INTEGER DEFAULT 0,
            enabled INTEGER DEFAULT 1, last_balance INTEGER, last_run TEXT, proxy TEXT,
            tickets INTEGER)"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS balance_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT, acc_id INTEGER, ts INTEGER, balance INTEGER)"""
        )
        connection.execute("CREATE INDEX IF NOT EXISTS ix_blog ON balance_log(acc_id, ts)")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS lottery_reminder(
            owner INTEGER PRIMARY KEY, deadline TEXT, fired TEXT)"""
        )
        connection.execute(
            "INSERT OR IGNORE INTO users(tg_id,has_access,is_admin,created_at) VALUES(?,1,1,?)", (OWNER_ID, _now())
        )


def _user_flag(tg_id, column):
    if tg_id == OWNER_ID:
        return True
    with _lock, _connect() as connection:
        row = connection.execute(f"SELECT {column} FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        return bool(row and row[column])


def has_access(tg_id):
    return _user_flag(tg_id, "has_access")


def is_admin(tg_id):
    return _user_flag(tg_id, "is_admin")


def grant(tg_id):
    with _lock, _connect() as connection:
        connection.execute(
            "INSERT INTO users(tg_id,has_access,is_admin,created_at) VALUES(?,1,0,?) "
            "ON CONFLICT(tg_id) DO UPDATE SET has_access=1",
            (tg_id, _now()),
        )


def revoke(tg_id):
    if tg_id == OWNER_ID:
        return
    with _lock, _connect() as connection:
        connection.execute("UPDATE users SET has_access=0 WHERE tg_id=?", (tg_id,))
        connection.execute("UPDATE accounts SET enabled=0 WHERE owner=?", (tg_id,))


def list_users():
    with _lock, _connect() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM users ORDER BY is_admin DESC, created_at")]


def add_account(owner, label, cookie, proxy):
    with _lock, _connect() as connection:
        cursor = connection.execute(
            "INSERT INTO accounts(owner,label,cookie,proxy,do_cart) VALUES(?,?,?,?,1)", (owner, label, cookie, proxy)
        )
        return cursor.lastrowid


def proxy_used(proxy, exclude_id=None):
    with _lock, _connect() as connection:
        if exclude_id:
            row = connection.execute("SELECT 1 FROM accounts WHERE proxy=? AND id<>?", (proxy, exclude_id)).fetchone()
        else:
            row = connection.execute("SELECT 1 FROM accounts WHERE proxy=?", (proxy,)).fetchone()
        return bool(row)


def list_accounts(owner):
    with _lock, _connect() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM accounts WHERE owner=? ORDER BY id", (owner,))]


def all_enabled_accounts():
    with _lock, _connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT a.* FROM accounts a JOIN users u ON u.tg_id=a.owner "
                "WHERE a.enabled=1 AND (u.has_access=1 OR a.owner=?)",
                (OWNER_ID,),
            )
        ]


def get_account(acc_id, owner=None):
    with _lock, _connect() as connection:
        if owner is None:
            row = connection.execute("SELECT * FROM accounts WHERE id=?", (acc_id,)).fetchone()
        else:
            row = connection.execute("SELECT * FROM accounts WHERE id=? AND owner=?", (acc_id, owner)).fetchone()
        return dict(row) if row else None


def update_account(acc_id, **fields):
    if not fields:
        return
    assignments = ", ".join(f"{name}=?" for name in fields)
    with _lock, _connect() as connection:
        connection.execute(f"UPDATE accounts SET {assignments} WHERE id=?", (*fields.values(), acc_id))


def delete_account(acc_id, owner):
    with _lock, _connect() as connection:
        connection.execute("DELETE FROM accounts WHERE id=? AND owner=?", (acc_id, owner))


def mark_run(acc_id, balance, session_dead=False):
    update_account(acc_id, last_balance=balance, last_run=_now(), session_dead=int(session_dead))


def get_lottery_reminder(owner):
    with _lock, _connect() as connection:
        row = connection.execute("SELECT deadline, fired FROM lottery_reminder WHERE owner=?", (owner,)).fetchone()
        if not row:
            return None, set()
        fired = {int(hours) for hours in (row["fired"] or "").split(",") if hours.strip()}
        return row["deadline"], fired


def set_lottery_reminder(owner, deadline, fired):
    with _lock, _connect() as connection:
        connection.execute(
            "INSERT INTO lottery_reminder(owner,deadline,fired) VALUES(?,?,?) "
            "ON CONFLICT(owner) DO UPDATE SET deadline=excluded.deadline, fired=excluded.fired",
            (owner, deadline, ",".join(str(hours) for hours in sorted(fired))),
        )


def log_balance(acc_id, balance):
    if balance is None:
        return
    with _lock, _connect() as connection:
        last = connection.execute(
            "SELECT balance FROM balance_log WHERE acc_id=? ORDER BY ts DESC LIMIT 1", (acc_id,)
        ).fetchone()
        if last and last["balance"] == int(balance):
            return
        connection.execute(
            "INSERT INTO balance_log(acc_id,ts,balance) VALUES(?,?,?)", (acc_id, int(time.time()), int(balance))
        )


def _earned_since(connection, acc_id, since):
    base = connection.execute(
        "SELECT balance FROM balance_log WHERE acc_id=? AND ts<? ORDER BY ts DESC LIMIT 1", (acc_id, since)
    ).fetchone()
    rows = connection.execute(
        "SELECT balance FROM balance_log WHERE acc_id=? AND ts>=? ORDER BY ts ASC", (acc_id, since)
    ).fetchall()
    sequence = ([base["balance"]] if base else []) + [row["balance"] for row in rows]
    return sum(max(0, sequence[index] - sequence[index - 1]) for index in range(1, len(sequence)))


def profile_stats(owner):
    now = int(time.time())
    msk_midnight = now - ((now + MSK_OFFSET_SECONDS) % DAY_SECONDS)
    periods = [
        ("сегодня", msk_midnight),
        ("неделя", now - 7 * DAY_SECONDS),
        ("месяц", now - 30 * DAY_SECONDS),
        ("всё время", 0),
    ]
    with _lock, _connect() as connection:
        accounts = [
            dict(row) for row in connection.execute("SELECT * FROM accounts WHERE owner=? ORDER BY id", (owner,))
        ]
        earned = {
            period: sum(_earned_since(connection, account["id"], since) for account in accounts)
            for period, since in periods
        }
    active = sum(1 for account in accounts if account["enabled"])
    return {
        "accs": accounts,
        "total": sum((account["last_balance"] or 0) for account in accounts),
        "total_tickets": sum((account["tickets"] or 0) for account in accounts),
        "active": active,
        "paused": len(accounts) - active,
        "expired": sum(account["session_dead"] for account in accounts),
        "earned": earned,
    }
