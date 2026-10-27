import asyncio
import html
import re

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from morkovsk import storage as db
from morkovsk.bot import ui
from morkovsk.bot.onboarding import AddAcc, add_by_cookie, collect_cookie, replace_cookie, run_login, submit_login_code
from morkovsk.farming import check_proxy, farm_account, refresh_balances, validate

router = Router()
_farming_owners = set()
PROXY_REMOVE_WORDS = ("нет", "no", "-", "убрать")


class NewCookie(StatesGroup):
    cookie = State()


class GrantU(StatesGroup):
    uid = State()


class SetProxy(StatesGroup):
    val = State()


def _id_after(prefix: str, data: str) -> int:
    return int(data[len(prefix) :])


async def guard(event) -> bool:
    if db.has_access(event.from_user.id):
        return True
    if isinstance(event, CallbackQuery):
        await event.answer(ui.NO_ACCESS, show_alert=True)
    else:
        await event.answer(ui.NO_ACCESS)
    return False


async def admin_guard(event) -> bool:
    return await guard(event) and db.is_admin(event.from_user.id)


@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    if not await guard(message):
        return
    await message.answer(ui.TITLE, reply_markup=ui.main_menu(message.from_user.id), parse_mode="HTML")


@router.callback_query(F.data == "home")
async def home(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    if not await guard(callback):
        return
    await callback.message.edit_text(ui.TITLE, reply_markup=ui.main_menu(callback.from_user.id), parse_mode="HTML")


@router.callback_query(F.data == "faq")
async def faq(callback: CallbackQuery):
    if not await guard(callback):
        return
    await callback.message.edit_text(ui.FAQ_TEXT, parse_mode="HTML", reply_markup=ui.back_to("home"))


async def _render_profile(target_message, owner):
    stats = await asyncio.to_thread(db.profile_stats, owner)
    await target_message.edit_text(ui.profile_text(stats), reply_markup=ui.profile_menu(), parse_mode="HTML")


@router.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery):
    if not await guard(callback):
        return
    await _render_profile(callback.message, callback.from_user.id)


@router.callback_query(F.data == "prof_refresh")
async def prof_refresh(callback: CallbackQuery):
    if not await guard(callback):
        return
    await callback.answer("Обновляю живьём…")
    await callback.message.edit_text("⏳ Читаю балансы через прокси…")
    await asyncio.to_thread(refresh_balances, callback.from_user.id)
    await _render_profile(callback.message, callback.from_user.id)


@router.callback_query(F.data == "accs")
async def accs(callback: CallbackQuery):
    if not await guard(callback):
        return
    accounts = db.list_accounts(callback.from_user.id)
    await callback.message.edit_text("Твои Ozon-аккаунты:", reply_markup=ui.accounts_menu(accounts))


@router.callback_query(F.data == "howto")
async def howto(callback: CallbackQuery):
    if not await guard(callback):
        return
    await callback.message.edit_text(ui.COOKIE_SNIPPET, parse_mode="HTML", reply_markup=ui.back_to("accs"))


@router.callback_query(F.data.startswith("acc:"))
async def acc_view(callback: CallbackQuery):
    if not await guard(callback):
        return
    account = db.get_account(_id_after("acc:", callback.data), callback.from_user.id)
    if not account:
        return await callback.answer("нет такого", show_alert=True)
    await callback.message.edit_text(ui.account_card(account), reply_markup=ui.account_menu(account), parse_mode="HTML")


async def _toggle_account_flag(callback: CallbackQuery, prefix: str, column: str):
    if not await guard(callback):
        return
    account = db.get_account(_id_after(prefix, callback.data), callback.from_user.id)
    db.update_account(account["id"], **{column: 0 if account[column] else 1})
    account = db.get_account(account["id"], callback.from_user.id)
    await callback.message.edit_reply_markup(reply_markup=ui.account_menu(account))


@router.callback_query(F.data.startswith("tc:"))
async def toggle_cart(callback: CallbackQuery):
    await _toggle_account_flag(callback, "tc:", "do_cart")


@router.callback_query(F.data.startswith("px:"))
async def proxy_start(callback: CallbackQuery, state: FSMContext):
    if not await guard(callback):
        return
    await state.set_state(SetProxy.val)
    await state.update_data(acc_id=_id_after("px:", callback.data))
    await callback.message.edit_text(
        "Пришли прокси для этого аккаунта в формате:\n<code>ip:port:логин:пароль</code>\n"
        "(или <code>ip:port</code> без авторизации)\n\n"
        "⚠️ Прокси <b>обязателен</b> (1 прокси = 1 аккаунт) — убрать нельзя, можно только заменить на другой.",
        parse_mode="HTML",
    )


@router.message(SetProxy.val)
async def proxy_set(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    account = db.get_account(data["acc_id"], message.from_user.id)
    if not account:
        return await message.answer("аккаунт не найден")
    proxy = message.text.strip()
    if proxy.lower() in PROXY_REMOVE_WORDS:
        return await message.answer(
            "Прокси обязателен для каждого аккаунта — убрать нельзя. Пришли другой прокси, чтобы заменить."
        )
    if db.proxy_used(proxy, exclude_id=account["id"]):
        return await message.answer("❌ Этот прокси уже привязан к другому аккаунту (1 прокси = 1 акк). Пришли другой.")
    progress = await message.answer("Проверяю прокси + cookie через него…")
    balance = await asyncio.to_thread(validate, account["cookie"], proxy)
    if balance is None:
        return await progress.edit_text(
            "❌ Через этот прокси не читается (мёртвый прокси или cookie протух). "
            "Проверь формат <code>ip:port:логин:пароль</code> и что cookie актуальный.",
            parse_mode="HTML",
        )
    db.update_account(account["id"], proxy=proxy)
    db.mark_run(account["id"], balance)
    await progress.edit_text(
        f"✅ Прокси задан ({proxy.split(':')[0]}). Баланс через него: {balance} 🥕", reply_markup=ui.to_accounts()
    )


@router.callback_query(F.data.startswith("del:"))
async def del_acc(callback: CallbackQuery):
    if not await guard(callback):
        return
    db.delete_account(_id_after("del:", callback.data), callback.from_user.id)
    await callback.answer("удалён")
    await accs(callback)


@router.callback_query(F.data == "add")
async def add_start(callback: CallbackQuery, state: FSMContext):
    if not await guard(callback):
        return
    await state.set_state(AddAcc.label)
    await callback.message.edit_text("Название аккаунта (как подписать, напр. «Основной»):")


@router.message(AddAcc.label)
async def add_label(message: Message, state: FSMContext):
    await state.update_data(label=message.text.strip()[:40])
    await state.set_state(AddAcc.proxy)
    await message.answer(
        "Теперь <b>прокси</b> для этого аккаунта (обязательно, 1 прокси = 1 аккаунт):\n"
        "<code>ip:port:логин:пароль</code> (или <code>ip:port</code>).",
        parse_mode="HTML",
    )


@router.message(AddAcc.proxy)
async def add_proxy(message: Message, state: FSMContext):
    proxy = message.text.strip()
    if db.proxy_used(proxy):
        return await message.answer(
            "❌ Этот прокси уже привязан к другому аккаунту. Нужен свой прокси на каждый акк. Пришли другой."
        )
    progress = await message.answer("Проверяю прокси…")
    if not await asyncio.to_thread(check_proxy, proxy):
        return await progress.edit_text(
            "❌ Прокси не отвечает / не пускает Ozon. Проверь формат <code>ip:port:логин:пароль</code> и пришли снова.",
            parse_mode="HTML",
        )
    await state.update_data(proxy=proxy)
    await progress.edit_text(
        "✅ Прокси ок. <b>Как войти в аккаунт?</b>", parse_mode="HTML", reply_markup=ui.login_methods()
    )


@router.callback_query(F.data == "lm_cookie")
async def lm_cookie(callback: CallbackQuery, state: FSMContext):
    if not await guard(callback):
        return
    await state.set_state(AddAcc.cookie)
    await callback.message.edit_text("Вставь cookie веб-сессии ozon.ru:\n\n" + ui.COOKIE_SNIPPET, parse_mode="HTML")


@router.callback_query(F.data == "lm_phone")
async def lm_phone(callback: CallbackQuery, state: FSMContext):
    if not await guard(callback):
        return
    await state.set_state(AddAcc.phone)
    await callback.message.edit_text(
        "Пришли <b>номер телефона</b> аккаунта в формате <code>+79XXXXXXXXX</code>:", parse_mode="HTML"
    )


@router.callback_query(F.data == "lm_qr")
async def lm_qr(callback: CallbackQuery, state: FSMContext):
    if not await guard(callback):
        return
    await callback.message.edit_text("⏳ Готовлю QR-код для входа (10–20 сек)…")
    await run_login(callback.from_user.id, "qr", None, state)


@router.message(AddAcc.phone)
async def add_phone(message: Message, state: FSMContext):
    digits = re.sub(r"\D", "", message.text or "")
    if len(digits) < 10:
        return await message.answer("Нужен номер в формате <code>+79XXXXXXXXX</code>. Пришли снова.", parse_mode="HTML")
    await message.answer("⏳ Открываю вход Ozon и запрашиваю код (звонок/SMS), это ~20–40 сек…")
    await run_login(message.from_user.id, "phone", "+7" + digits[-10:], state)


@router.message(AddAcc.code)
async def add_code(message: Message, state: FSMContext):
    data = await state.get_data()
    session_dir = data.get("login_dir")
    code = re.sub(r"\D", "", message.text or "")
    if not session_dir or not code:
        return await message.answer("Пришли код цифрами.")
    submit_login_code(session_dir, code)
    await message.answer("⏳ Ввожу код…")


@router.message(AddAcc.cookie)
async def add_cookie(message: Message, state: FSMContext):
    collect_cookie(message, state, add_by_cookie)


@router.callback_query(F.data.startswith("nc:"))
async def newcookie_start(callback: CallbackQuery, state: FSMContext):
    if not await guard(callback):
        return
    account = db.get_account(_id_after("nc:", callback.data), callback.from_user.id)
    if not account:
        return await callback.answer("нет такого", show_alert=True)
    await state.set_state(NewCookie.cookie)
    await state.update_data(acc_id=account["id"], proxy=account["proxy"])
    await callback.message.edit_text("Пришли новый cookie ozon.ru:\n\n" + ui.COOKIE_SNIPPET, parse_mode="HTML")


@router.message(NewCookie.cookie)
async def newcookie_save(message: Message, state: FSMContext):
    collect_cookie(message, state, replace_cookie)


@router.callback_query(F.data.startswith("f:"))
async def farm_one(callback: CallbackQuery):
    if not await guard(callback):
        return
    account = db.get_account(_id_after("f:", callback.data), callback.from_user.id)
    if not account:
        return await callback.answer("нет", show_alert=True)
    await callback.answer("Фармлю…")
    await callback.message.edit_text(f"⏳ Фармлю «{html.escape(account['label'])}»…")
    result = await farm_account(account)
    await callback.message.edit_text(
        ui.farm_report(account["label"], result), reply_markup=ui.to_accounts(), parse_mode="HTML"
    )


@router.callback_query(F.data == "farm_all")
async def farm_all(callback: CallbackQuery):
    if not await guard(callback):
        return
    owner = callback.from_user.id
    if owner in _farming_owners:
        return await callback.answer("Уже фармлю, подожди", show_alert=True)
    accounts = [account for account in db.list_accounts(owner) if account["enabled"]]
    if not accounts:
        return await callback.answer("Нет активных аккаунтов", show_alert=True)
    await callback.answer("Фармлю все…")
    await callback.message.edit_text(f"⏳ Фармлю {len(accounts)} аккаунт(ов)…")
    _farming_owners.add(owner)
    try:
        reports = []
        for account in accounts:
            if not db.has_access(owner):
                break
            reports.append(ui.farm_report(account["label"], await farm_account(account)))
    finally:
        _farming_owners.discard(owner)
    await callback.message.edit_text("\n\n".join(reports) or "—", reply_markup=ui.back_to("home"), parse_mode="HTML")


@router.callback_query(F.data == "users")
async def users(callback: CallbackQuery):
    if not await guard(callback):
        return
    if not db.is_admin(callback.from_user.id):
        return await callback.answer("только админ", show_alert=True)
    await callback.message.edit_text(
        "Пользователи (👑админ ✅доступ 🚫нет):", reply_markup=ui.users_menu(db.list_users())
    )


@router.callback_query(F.data.startswith("u:"))
async def user_view(callback: CallbackQuery):
    if not await admin_guard(callback):
        return
    tg_id = _id_after("u:", callback.data)
    account_count = len(db.list_accounts(tg_id))
    await callback.message.edit_text(
        f"Пользователь <code>{tg_id}</code>\nаккаунтов: {account_count}\n"
        f"доступ: {'есть' if db.has_access(tg_id) else 'нет'}",
        reply_markup=ui.user_menu(tg_id),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("rv:"))
async def do_revoke(callback: CallbackQuery):
    if not await admin_guard(callback):
        return
    db.revoke(_id_after("rv:", callback.data))
    await callback.answer("Доступ забран, фарм остановлен", show_alert=True)
    await users(callback)


@router.callback_query(F.data.startswith("gr:"))
async def do_grant(callback: CallbackQuery):
    if not await admin_guard(callback):
        return
    db.grant(_id_after("gr:", callback.data))
    await callback.answer("Доступ выдан")
    await users(callback)


@router.callback_query(F.data == "grant")
async def grant_start(callback: CallbackQuery, state: FSMContext):
    if not await admin_guard(callback):
        return
    await state.set_state(GrantU.uid)
    await callback.message.edit_text("Пришли Telegram ID пользователя, которому выдать доступ:")


@router.message(GrantU.uid)
async def grant_uid(message: Message, state: FSMContext):
    await state.clear()
    if not db.is_admin(message.from_user.id):
        return
    try:
        tg_id = int(message.text.strip())
    except ValueError:
        return await message.answer("Нужен числовой ID")
    db.grant(tg_id)
    await message.answer(f"✅ Доступ выдан {tg_id}", reply_markup=ui.single_button("👤 Пользователи", "users"))
