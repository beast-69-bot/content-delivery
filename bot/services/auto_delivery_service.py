"""
Auto delivery helpers for paid orders.
"""

import logging
from io import BytesIO

from aiogram import Bot
from aiogram.types import BufferedInputFile

from database.models import OrderStatus
from services.db_service import get_order, mark_delivered

logger = logging.getLogger(__name__)


def _delivery_bot_token(order) -> str | None:
    return None


async def _download_as_input_file(source_bot: Bot, item: dict) -> BufferedInputFile | None:
    file_id = item.get("file_id")
    if not file_id:
        return None

    buffer = BytesIO()
    await source_bot.download(file_id, destination=buffer)
    buffer.seek(0)
    filename = item.get("file_name") or f"delivery_{item.get('send_as', 'file')}.bin"
    return BufferedInputFile(buffer.read(), filename=filename)


async def _send_item_with_customer_bot(source_bot: Bot, delivery_bot: Bot, chat_id: int, item: dict) -> None:
    send_as = item.get("send_as")
    caption = item.get("caption")

    if send_as == "text":
        await delivery_bot.send_message(chat_id=chat_id, text=item.get("text") or "")
        return

    input_file = await _download_as_input_file(source_bot, item)
    if not input_file:
        raise ValueError("Delivery item has no reusable file_id")

    if send_as == "photo":
        await delivery_bot.send_photo(chat_id=chat_id, photo=input_file, caption=caption)
    elif send_as == "video":
        await delivery_bot.send_video(chat_id=chat_id, video=input_file, caption=caption)
    elif send_as == "audio":
        await delivery_bot.send_audio(chat_id=chat_id, audio=input_file, caption=caption)
    elif send_as == "voice":
        await delivery_bot.send_voice(chat_id=chat_id, voice=input_file)
    else:
        await delivery_bot.send_document(chat_id=chat_id, document=input_file, caption=caption)


async def auto_deliver_order(bot: Bot, order_id: str, admin_id: int = 0) -> bool:
    order = await get_order(order_id)
    if not order or order.status != OrderStatus.paid or not order.plan:
        return False

    delivery_items = list(order.delivery_items or order.plan.delivery_items or [])
    if not delivery_items:
        logger.warning("Order %s has no delivery items configured", order_id)
        return False

    customer_bot_token = _delivery_bot_token(order)
    delivery_bot = Bot(token=customer_bot_token) if customer_bot_token else bot
    try:
        await delivery_bot.send_message(
            chat_id=order.user_id,
            text=(
                "<b>Your content is ready</b>\n\n"
                f"Order: <b>#{order.order_id}</b>\n"
                f"Content: <b>{order.product_name}</b>\n"
                f"Plan: <b>{order.plan_name}</b>"
            ),
        )

        for item in delivery_items:
            if customer_bot_token:
                await _send_item_with_customer_bot(bot, delivery_bot, order.user_id, item)
            else:
                await bot.copy_message(
                    chat_id=order.user_id,
                    from_chat_id=int(item["from_chat_id"]),
                    message_id=int(item["message_id"]),
                )

        delivered = await mark_delivered(order_id, admin_id)
        if delivered:
            await delivery_bot.send_message(
                chat_id=order.user_id,
                text=f"Order <b>#{order.order_id}</b> delivered successfully.",
            )
        return delivered
    except Exception as e:
        logger.warning("Auto delivery failed for order %s: %s", order_id, e)
        return False
    finally:
        if customer_bot_token:
            await delivery_bot.session.close()
