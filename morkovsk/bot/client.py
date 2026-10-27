from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

from morkovsk.config import BOT_TOKEN, TELEGRAM_API_URL

session = AiohttpSession(api=TelegramAPIServer.from_base(TELEGRAM_API_URL)) if TELEGRAM_API_URL else None
bot = Bot(BOT_TOKEN, session=session)
