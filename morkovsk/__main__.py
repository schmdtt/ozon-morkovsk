import asyncio
import logging

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from morkovsk import storage
from morkovsk.bot.client import bot
from morkovsk.bot.handlers import router
from morkovsk.farming import lottery_scheduler, scheduler


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    storage.init()
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)
    await asyncio.gather(dispatcher.start_polling(bot, handle_signals=False), scheduler(), lottery_scheduler())


asyncio.run(main())
