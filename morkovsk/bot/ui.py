import html

from aiogram.types import InlineKeyboardButton as Button
from aiogram.types import InlineKeyboardMarkup as Keyboard

from morkovsk import storage
from morkovsk.ozon.game import Status

TITLE = "🥕 <b>Ozon Морковка — панель фарма</b>"
NO_ACCESS = "⛔ Нет доступа. Обратись к владельцу бота."

COOKIE_SNIPPET = (
    "📋 <b>Как получить cookie</b> (1 раз, ~30 сек, на компьютере):\n\n"
    "1️⃣ Открой <b>www.ozon.ru</b> (ты должен быть залогинен).\n"
    "2️⃣ Нажми <b>F12</b> → вкладка <b>Network</b> (Сеть).\n"
    "3️⃣ Нажми <b>F5</b> (обновить страницу).\n"
    "4️⃣ В списке кликни самую верхнюю строку <b>www.ozon.ru</b> (тип <i>document</i>).\n"
    "5️⃣ Справа: <b>Headers</b> → блок <b>Request Headers</b> → строка <code>cookie:</code>.\n"
    "6️⃣ Правый клик по значению → <b>Copy value</b>.\n"
    "7️⃣ Вставь сюда одним сообщением. 👇\n\n"
    "⚠️ НЕ бери <code>document.cookie</code> из Console — там нет токена.\n"
    "✅ Правильная строка длинная и содержит <code>__Secure-access-token</code>."
)

FAQ_TEXT = (
    "❓ <b>Как работает бот</b>\n\n"
    "🥕 <b>Ozon «Морковка»</b> — бот сам фармит морковки:\n"
    "• Выполняет задания-визиты (заходит в нужные разделы) → начисляются морковки.\n"
    "• Крутит <b>бесплатное лукошко</b> раз в день (платное за морковки не трогает).\n"
    "• Отмечает ежедневный визит (стрик).\n"
    "• Работает <b>24/7</b> сам, каждые ~30–45 мин; ты получаешь уведомления.\n"
    "• Сам продлевает сессию (авто-обновление cookie).\n\n"
    "🌐 <b>Почему прокси обязателен?</b>\n"
    "Каждый аккаунт должен ходить со <b>своего IP</b> — это защита от бана. "
    "Ozon видит, что аккаунты не связаны между собой. Правило: <b>1 прокси = 1 аккаунт</b>. "
    "Если несколько аккаунтов идут с одного IP — они «палятся» как одна ферма и попадают под блок. "
    "Поэтому прокси нельзя убрать, только заменить.\n\n"
    "🔐 <b>Как добавить аккаунт?</b>\n"
    "Три способа: QR-код из приложения Ozon, номер телефона (SMS/звонок) или cookie из браузера. "
    "Если Ozon покажет капчу при входе — используй cookie. Дальше бот сам продлевает сессию."
)


def single_button(text, callback_data):
    return Keyboard(inline_keyboard=[[Button(text=text, callback_data=callback_data)]])


def back_to(callback_data="home"):
    return single_button("⬅️ Назад", callback_data)


def to_accounts():
    return single_button("⬅️ К аккаунтам", "accs")


def main_menu(tg_id):
    rows = [
        [Button(text="🥕 Мои аккаунты", callback_data="accs")],
        [Button(text="👤 Профиль", callback_data="profile")],
        [Button(text="▶️ Фарм сейчас", callback_data="farm_all")],
        [Button(text="❓ Как работает", callback_data="faq")],
    ]
    if storage.is_admin(tg_id):
        rows.append([Button(text="👤 Пользователи", callback_data="users")])
    return Keyboard(inline_keyboard=rows)


def or_dash(value):
    return value if value is not None else "—"


def account_button_text(account):
    flag = "🟢" if account["enabled"] else "⚪"
    return f"{flag} {account['label']} · {or_dash(account['last_balance'])}🥕 · {or_dash(account['tickets'])}🎟"


def accounts_menu(accounts):
    rows = [[Button(text=account_button_text(account), callback_data=f"acc:{account['id']}")] for account in accounts]
    rows.append([Button(text="➕ Добавить аккаунт", callback_data="add")])
    rows.append([Button(text="📋 Как получить cookie", callback_data="howto")])
    rows.append([Button(text="⬅️ Назад", callback_data="home")])
    return Keyboard(inline_keyboard=rows)


def account_card(account):
    return (
        f"👤 <b>{html.escape(account['label'])}</b>\n"
        f"Баланс: {or_dash(account['last_balance'])} 🥕\n"
        f"Билеты: {or_dash(account['tickets'])} 🎟\n"
        f"Обновлён: {account['last_run'] or '—'}"
    )


def account_menu(account):
    proxy_host = (account.get("proxy") or "").split(":")[0] if account.get("proxy") else "нет"
    account_id = account["id"]
    rows = [
        [Button(text="▶️ Фарм", callback_data=f"f:{account_id}")],
        [
            Button(
                text=("🛒 Корзина: ВКЛ" if account["do_cart"] else "🛒 Корзина: выкл"), callback_data=f"tc:{account_id}"
            )
        ],
        [Button(text=f"🌐 Прокси: {proxy_host}", callback_data=f"px:{account_id}")],
        [
            Button(text="🔄 Обновить cookie", callback_data=f"nc:{account_id}"),
            Button(text="🗑 Удалить", callback_data=f"del:{account_id}"),
        ],
        [Button(text="⬅️ Назад", callback_data="accs")],
    ]
    return Keyboard(inline_keyboard=rows)


def login_methods():
    return Keyboard(
        inline_keyboard=[
            [Button(text="📱 По QR-коду (проще всего)", callback_data="lm_qr")],
            [Button(text="☎️ По номеру (SMS/звонок)", callback_data="lm_phone")],
            [Button(text="🍪 Вставить cookie", callback_data="lm_cookie")],
        ]
    )


def _signed(value):
    return f"+{value}" if value >= 0 else str(value)


def profile_text(stats):
    accounts = stats["accs"]
    earned = stats["earned"]
    lines = [
        "👤 <b>Профиль</b>",
        "",
        f"🥕 Всего морковок: <b>{stats['total']}</b>",
        f"🎟 Билетов всего: <b>{stats['total_tickets']}</b>",
        f"📊 Аккаунтов: {len(accounts)}  (🟢 {stats['active']} · ⚪ {stats['paused']} пауза)",
        "🍪 Куки: все живые ✅" if not stats["expired"] else f"🍪 Куки: ⚠️ протухших {stats['expired']}",
        "",
        "📈 <b>Заработано</b> 🥕:",
        f"Сегодня: {_signed(earned['сегодня'])}",
        f"За неделю: {_signed(earned['неделя'])}",
        f"За месяц: {_signed(earned['месяц'])}",
        f"За всё время: {_signed(earned['всё время'])}",
    ]
    if accounts:
        lines += ["", "──  По аккаунтам  ──"]
        for account in accounts:
            flag = "🟢" if account["enabled"] else "⚪"
            lines.append(
                f"{flag} {html.escape(account['label'])} · {or_dash(account['last_balance'])}🥕 · "
                f"{or_dash(account['tickets'])}🎟 · {account['last_run'] or '—'}"
            )
    else:
        lines += ["", "Пока нет аккаунтов — добавь в «Мои аккаунты»."]
    return "\n".join(lines)


def profile_menu():
    return Keyboard(
        inline_keyboard=[
            [Button(text="🔄 Обновить (живьём)", callback_data="prof_refresh")],
            [Button(text="⬅️ Назад", callback_data="home")],
        ]
    )


def users_menu(users):
    rows = []
    for user in users:
        tag = "👑" if user["is_admin"] else ("✅" if user["has_access"] else "🚫")
        rows.append([Button(text=f"{tag} {user['tg_id']}", callback_data=f"u:{user['tg_id']}")])
    rows.append([Button(text="➕ Выдать доступ", callback_data="grant")])
    rows.append([Button(text="⬅️ Назад", callback_data="home")])
    return Keyboard(inline_keyboard=rows)


def user_menu(tg_id):
    rows = []
    if tg_id != storage.OWNER_ID:
        rows.append([Button(text="🚫 Забрать доступ (остановит фарм)", callback_data=f"rv:{tg_id}")])
        rows.append([Button(text="✅ Выдать доступ", callback_data=f"gr:{tg_id}")])
    rows.append([Button(text="⬅️ Назад", callback_data="users")])
    return Keyboard(inline_keyboard=rows)


COOKIE_INCOMPLETE = (
    "Получил часть. Если строка пришла несколькими сообщениями — пришли остальные куски. "
    "Пока в собранном нет <code>__Secure-access-token</code>."
)
COOKIE_REJECTED = (
    "❌ Не проходит. Убедись, что это <b>полная</b> строка <code>cookie</code> из вкладки "
    "<b>Network</b> (не <code>document.cookie</code>), и что прокси живой. Пришли cookie заново."
)
LOGIN_QR = "📱 Отсканируй этот QR в приложении Ozon (Профиль → «Войти по QR»). Жду входа…"
LOGIN_CAPTCHA = "⚠️ Ozon показал капчу — удалённо решить не могу. Если не пройдёт, добавь через cookie."
LOGIN_NO_QR = "📱 Ozon сейчас не показывает QR для входа. Добавь аккаунт ☎️ по номеру или через 🍪 cookie."
LOGIN_FAILED = "❌ Вход не завершён (таймаут, ошибка или капча). Попробуй ещё раз или добавь через 🍪 cookie."


def account_added(label, balance):
    return f"✅ Аккаунт «{html.escape(label)}» добавлен. Баланс: {balance} 🥕\nЗапускаю фарм."


def login_wait_resend(countdown):
    return (
        "⏳ Ozon сначала шлёт пуш. Жду и запрашиваю код по <b>SMS/звонку</b> "
        f"(<i>{html.escape(countdown)}</i>). Пуш можно игнорировать."
    )


def login_code_needed(sms_requested):
    if sms_requested:
        return (
            "✅ Код отправлен по <b>SMS/звонку</b>. Пришли код "
            "(или последние цифры номера, с которого звонят) одним сообщением."
        )
    return "Ozon запросил код. Он в <b>пуше приложения Ozon</b> — пришли его одним сообщением."


def farm_event(label, kind, name, reward, balance):
    head = f"👤 {html.escape(label)}\n"
    if kind == "lukoshko":
        return head + f"🎁 Прокручено лукошко — выпало «{html.escape(name)}»\nБаланс: {balance} 🥕"
    if kind == "daily":
        return head + f"🎁 Ежедневный бонус забран! +{reward} 🥕\nБаланс: {balance} 🥕"
    return head + f"✅ Выполнено задание: «{html.escape(name)}» (+{reward} 🥕)\nБаланс: {balance} 🥕"


def farm_report(label, result):
    head = f"👤 <b>{html.escape(label)}</b>\n"
    if result.status == Status.DEAD:
        return head + "❌ Сессия не авторизована — обнови cookie."
    if result.status == Status.TRANSPORT:
        return head + "⚠️ Сеть или прокси недоступны — прогон пропущен, cookie в порядке."
    if result.status == Status.BUSY:
        return head + "⏳ Уже фармится."
    lines = [f"Баланс: {result.balance} 🥕"]
    if result.balance is not None and result.start_balance is not None and result.balance != result.start_balance:
        lines[0] += f"  ({_signed(result.balance - result.start_balance)} за прогон)"
    if result.status == Status.STOPPED:
        lines.append("⏹ Остановлено: доступ забрали.")
    if result.completed:
        lines.append(f"✅ Заданий выполнено: {len(result.completed)}")
        lines += [f"   • {html.escape(item)}" for item in result.completed]
    else:
        lines.append("Новых заданий не было.")
    return head + "\n".join(lines)


def session_dead(label):
    return f"⚠️ «{label}»: cookie протух, обнови (🔄) — фарм на паузе."


def lottery_reminder(hours):
    return (
        "🎟 <b>Напоминание о лотерее</b>\n"
        f"Продажа билетов заканчивается примерно через <b>{hours} ч</b>.\n"
        "Успей купить билет: Ozon → Морковск → «Билеты»."
    )
