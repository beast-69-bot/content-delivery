"""
Telegram Selling Bot — main.py
Entry point. Registers all routers, middlewares, and starts polling.
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config.settings import settings
from database.connection import init_db, close_db
from middlewares.auth import AuthMiddleware
from middlewares.fsm_timeout import FSMTimeoutMiddleware
from middlewares.throttle import ThrottleMiddleware
from scheduler.expiry import start_scheduler, stop_scheduler

# ── Handlers ──────────────────────────────────────────────────────────────────
from handlers.user.start import router as start_router
from handlers.user.captcha import router as captcha_router
from handlers.user.products import router as products_router
from handlers.user.payment import router as payment_router
from handlers.user.orders import router as orders_router

from handlers.admin.panel import router as admin_panel_router
from handlers.admin.products import router as admin_products_router
from handlers.admin.payments import router as admin_payments_router
from handlers.admin.orders import router as admin_orders_router
from handlers.admin.admins import router as admin_admins_router
from handlers.admin.settings import router as admin_settings_router
from handlers.admin.broadcast import router as admin_broadcast_router
from handlers.admin.content import router as admin_content_router
from handlers.admin.premium import router as admin_premium_router

import os
from logging.handlers import RotatingFileHandler

os.makedirs("logs", exist_ok=True)

logger = logging.getLogger()
logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

fh = RotatingFileHandler("logs/bot.log", maxBytes=5*1024*1024, backupCount=3)
fh.setFormatter(formatter)
logger.addHandler(fh)

ch = logging.StreamHandler()
ch.setFormatter(formatter)
logger.addHandler(ch)

logger = logging.getLogger(__name__)


async def on_startup(bot: Bot) -> None:
    await init_db()
    await start_scheduler(bot)
    logger.info("✅ Bot started successfully")


async def on_shutdown(bot: Bot) -> None:
    logger.info("🛑 Bot shutting down...")
    try:
        from middlewares.fsm_timeout import active_timers
        if active_timers:
            logger.info(f"Waiting 10 seconds for {len(active_timers)} active FSM sessions to complete...")
            await asyncio.sleep(10)
            logger.info(f"Dropping {len(active_timers)} active FSM sessions.")
            for task in active_timers.values():
                task.cancel()
    except Exception as e:
        logger.error(f"Error handling FSM timers during shutdown: {e}")

    await stop_scheduler()
    await close_db()
    
    try:
        await bot.send_message(settings.OWNER_ID, "🛑 Bot is shutting down.")
    except Exception as e:
        logger.error(f"Failed to send shutdown message to owner: {e}")
        
    logger.info("🛑 Bot shut down")


async def main() -> None:
    session = AiohttpSession(timeout=60)
    bot = Bot(
        token=settings.BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # ── Startup / Shutdown ────────────────────────────────────────────────────
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # ── Middlewares ───────────────────────────────────────────────────────────
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())
    dp.message.middleware(FSMTimeoutMiddleware(timeout_seconds=300))
    dp.callback_query.middleware(FSMTimeoutMiddleware(timeout_seconds=300))
    dp.message.middleware(ThrottleMiddleware(rate_limit=1.0))

    # ── Routers (order matters — admin before user for /start overlap) ────────
    dp.include_router(admin_panel_router)
    dp.include_router(admin_products_router)
    dp.include_router(admin_content_router)
    dp.include_router(admin_payments_router)
    dp.include_router(admin_orders_router)
    dp.include_router(admin_admins_router)
    dp.include_router(admin_settings_router)
    dp.include_router(admin_broadcast_router)
    dp.include_router(admin_premium_router)

    dp.include_router(start_router)
    dp.include_router(captcha_router)
    dp.include_router(products_router)
    dp.include_router(payment_router)
    dp.include_router(orders_router)

    logger.info("🚀 Starting bot polling...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
