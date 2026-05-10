"""
scheduler/expiry.py
APScheduler job — checks for expired orders every minute and notifies users.
"""

import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot

from services.db_service import expire_old_orders
from services.order_feed_service import sync_order_feed

logger = logging.getLogger(__name__)

_scheduler = AsyncIOScheduler()


async def _check_expired_orders(bot: Bot) -> None:
    expired = await expire_old_orders()
    for order in expired:
        try:
            await bot.send_message(
                chat_id=order.user_id,
                text=(
                    f"⏰ <b>Order Expired</b>\n\n"
                    f"Order <b>#{order.order_id}</b> has expired.\n"
                    f"Payment was not received within the time limit.\n\n"
                    f"Please create a new order to continue. 📦"
                ),
            )
        except Exception as e:
            logger.warning(f"Could not send expiry notice to {order.user_id}: {e}")
        await sync_order_feed(bot, order.order_id)

    if expired:
        logger.info(f"Expired {len(expired)} orders")


async def start_scheduler(bot: Bot) -> None:
    _scheduler.add_job(
        _check_expired_orders,
        trigger="interval",
        seconds=60,
        kwargs={"bot": bot},
        id="order_expiry",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("✅ Scheduler started")


async def stop_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
