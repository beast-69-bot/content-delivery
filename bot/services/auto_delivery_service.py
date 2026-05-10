"""
Auto delivery helpers for paid orders.
"""

import logging

from aiogram import Bot

from database.models import OrderStatus
from services.db_service import get_order, mark_delivered

logger = logging.getLogger(__name__)


async def auto_deliver_order(bot: Bot, order_id: str, admin_id: int = 0) -> bool:
    order = await get_order(order_id)
    if not order or order.status != OrderStatus.paid or not order.plan:
        return False

    delivery_items = list(order.delivery_items or order.plan.delivery_items or [])
    if not delivery_items:
        logger.warning("Order %s has no delivery items configured", order_id)
        return False

    await bot.send_message(
        chat_id=order.user_id,
        text=(
            "<b>Your content is ready</b>\n\n"
            f"Order: <b>#{order.order_id}</b>\n"
            f"Content: <b>{order.product_name}</b>\n"
            f"Plan: <b>{order.plan_name}</b>"
        ),
    )

    for item in delivery_items:
        await bot.copy_message(
            chat_id=order.user_id,
            from_chat_id=int(item["from_chat_id"]),
            message_id=int(item["message_id"]),
        )

    delivered = await mark_delivered(order_id, admin_id)
    if delivered:
        await bot.send_message(
            chat_id=order.user_id,
            text=f"Order <b>#{order.order_id}</b> delivered successfully.",
        )
    return delivered
