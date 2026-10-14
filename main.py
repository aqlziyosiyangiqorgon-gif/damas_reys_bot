import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from config import settings
from database.db import Database
from middlewares.roles import RoleMiddleware, TelegramApiFailureMiddleware, InvalidCallbackMiddleware
from handlers.common import common_router
from handlers.admin import admin_router
from handlers.driver import driver_router
from handlers.staff import staff_router
from handlers.parent import parent_router
from services.scheduler_service import SchedulerService
from middlewares.sqlite_storage import SQLiteFSMStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s"
)
logger = logging.getLogger(__name__)

async def main():
    db = Database(settings.get_db_path())
    await db.init_db()
    logger.info(f"Ma'lumotlar bazasi initsializatsiya qilindi: {settings.get_db_path()}")

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    fsm_storage = SQLiteFSMStorage(db)
    dp = Dispatcher(storage=fsm_storage)

    # Dependency Injection
    dp["db"] = db

    # Middleware: Rolni aniqlash (barcha xabar va callbacklarda)
    dp.message.middleware(RoleMiddleware(db))
    dp.callback_query.middleware(RoleMiddleware(db))
    dp.message.middleware(TelegramApiFailureMiddleware())
    dp.callback_query.middleware(TelegramApiFailureMiddleware())
    dp.callback_query.middleware(InvalidCallbackMiddleware())

    # Routerlarni qat'iy ketma-ketlikda ulash
    dp.include_router(common_router)
    dp.include_router(admin_router)
    dp.include_router(driver_router)
    dp.include_router(staff_router)
    dp.include_router(parent_router)

    # Scheduler xizmatini ishga tushirish (APScheduler)
    scheduler = SchedulerService(db=db, bot=bot)
    dp["scheduler"] = scheduler
    scheduler.start()

    try:
        logger.info("Bot to'liq ishlab chiqarish (production) rejimida ishga tushmoqda...")
        await dp.start_polling(bot)
    finally:
        logger.info("Bot to'xtatilmoqda (Graceful shutdown)...")
        scheduler.stop()
        await bot.session.close()
        await fsm_storage.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot to'xtatildi.")
