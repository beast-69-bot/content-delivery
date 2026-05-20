"""
Auto delivery helpers for paid orders.
"""

import logging
import asyncio
from dataclasses import dataclass
from io import BytesIO
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import BufferedInputFile, Message

from database.models import OrderStatus
from services.db_service import add_delivery_messages, get_order, get_settings, mark_delivered

logger = logging.getLogger(__name__)


@dataclass
class DeliveryResult:
    delivered: bool
    sent_count: int = 0
    total_count: int = 0
    error: str | None = None

    def __bool__(self) -> bool:
        return self.delivered


def _delivery_bot_token(order) -> str | None:
    return None


async def _copy_from_source_message(bot: Bot, chat_id: int, item: dict) -> Message:
    from_chat_id = item.get("from_chat_id")
    message_id = item.get("message_id")
    if from_chat_id is None or message_id is None:
        raise ValueError("Delivery item is missing source message reference")
    return await bot.copy_message(
        chat_id=chat_id,
        from_chat_id=int(from_chat_id),
        message_id=int(message_id),
    )


async def _download_as_input_file(source_bot: Bot, item: dict) -> BufferedInputFile | None:
    file_id = item.get("file_id")
    if not file_id:
        return None

    buffer = BytesIO()
    await source_bot.download(file_id, destination=buffer)
    buffer.seek(0)
    filename = item.get("file_name") or f"delivery_{item.get('send_as', 'file')}.bin"
    return BufferedInputFile(buffer.read(), filename=filename)


async def _send_item_with_customer_bot(source_bot: Bot, delivery_bot: Bot, chat_id: int, item: dict) -> Message:
    send_as = item.get("send_as")
    caption = item.get("caption")

    if send_as == "text":
        return await delivery_bot.send_message(chat_id=chat_id, text=item.get("text") or "")

    input_file = await _download_as_input_file(source_bot, item)
    if not input_file:
        raise ValueError("Delivery item has no reusable file_id")

    if send_as == "photo":
        return await delivery_bot.send_photo(chat_id=chat_id, photo=input_file, caption=caption)
    elif send_as == "video":
        return await delivery_bot.send_video(chat_id=chat_id, video=input_file, caption=caption)
    elif send_as == "audio":
        return await delivery_bot.send_audio(chat_id=chat_id, audio=input_file, caption=caption)
    elif send_as == "voice":
        return await delivery_bot.send_voice(chat_id=chat_id, voice=input_file)
    return await delivery_bot.send_document(chat_id=chat_id, document=input_file, caption=caption)


async def _send_item_with_main_bot(bot: Bot, chat_id: int, item: dict) -> Message:
    send_as = item.get("send_as")
    file_id = item.get("file_id")
    caption = item.get("caption")
    try:
        if send_as == "text":
            return await bot.send_message(chat_id=chat_id, text=item.get("text") or "")
        if send_as == "photo" and file_id:
            return await bot.send_photo(chat_id=chat_id, photo=file_id, caption=caption)
        if send_as == "video" and file_id:
            return await bot.send_video(chat_id=chat_id, video=file_id, caption=caption)
        if send_as == "audio" and file_id:
            return await bot.send_audio(chat_id=chat_id, audio=file_id, caption=caption)
        if send_as == "voice" and file_id:
            return await bot.send_voice(chat_id=chat_id, voice=file_id)
        if file_id:
            return await bot.send_document(chat_id=chat_id, document=file_id, caption=caption)
        return await _copy_from_source_message(bot, chat_id, item)
    except Exception:
        # Fallback for stale/bad file_id: copy from original source message if available.
        if file_id and item.get("from_chat_id") is not None and item.get("message_id") is not None:
            return await _copy_from_source_message(bot, chat_id, item)
        raise


async def _send_delivery_item(
    bot: Bot,
    delivery_bot: Bot,
    chat_id: int,
    item: dict,
    customer_bot_token: str | None,
) -> Message:
    if customer_bot_token:
        return await _send_item_with_customer_bot(bot, delivery_bot, chat_id, item)
    return await _send_item_with_main_bot(bot, chat_id, item)


async def _send_delivery_item_with_retry(
    bot: Bot,
    delivery_bot: Bot,
    chat_id: int,
    item: dict,
    customer_bot_token: str | None,
    attempts: int = 5,
) -> Message:
    for attempt in range(1, attempts + 1):
        try:
            return await _send_delivery_item(bot, delivery_bot, chat_id, item, customer_bot_token)
        except TelegramRetryAfter as e:
            if attempt >= attempts:
                raise
            await asyncio.sleep(float(e.retry_after) + 1)
        except Exception:
            if attempt >= attempts:
                raise
            await asyncio.sleep(attempt)
    raise RuntimeError("Delivery retry loop exhausted")


async def auto_deliver_order(bot: Bot, order_id: str, admin_id: int = 0) -> DeliveryResult:
    order = await get_order(order_id)
    if not order or order.status != OrderStatus.paid or not order.plan:
        return DeliveryResult(False)

    delivery_items = list(order.delivery_items or order.plan.delivery_items or [])
    if not delivery_items:
        logger.warning("Order %s has no delivery items configured", order_id)
        return DeliveryResult(False)

    customer_bot_token = _delivery_bot_token(order)
    delivery_bot = Bot(token=customer_bot_token) if customer_bot_token else bot
    sent_content_message_ids: list[int] = []
    failed_count = 0
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

        total_items = len(delivery_items)
        for index, item in enumerate(delivery_items, start=1):
            try:
                sent = await _send_delivery_item_with_retry(
                    bot,
                    delivery_bot,
                    order.user_id,
                    item,
                    customer_bot_token,
                )
                sent_content_message_ids.append(sent.message_id)
                # Keep a steady pause and a wider pause every 10 sends to reduce flood failures on large batches.
                if index % 10 == 0:
                    await asyncio.sleep(2.0)
                else:
                    await asyncio.sleep(0.7)
            except Exception as item_error:
                failed_count += 1
                logger.warning(
                    "Order %s delivery item %s/%s failed: %s",
                    order_id,
                    index,
                    total_items,
                    item_error,
                )
                await asyncio.sleep(1.5)

        if failed_count:
            return DeliveryResult(
                False,
                len(sent_content_message_ids),
                len(delivery_items),
                f"{failed_count} item(s) failed during delivery",
            )

        delivered = await mark_delivered(order_id, admin_id)
        if delivered:
            settings = await get_settings()
            delete_after = datetime.utcnow() + timedelta(minutes=settings.delivery_delete_minutes)
            await add_delivery_messages(order_id, order.user_id, sent_content_message_ids, delete_after)
            await delivery_bot.send_message(
                chat_id=order.user_id,
                text=(
                    f"Order <b>#{order.order_id}</b> delivered successfully.\n"
                    f"Delivered content will auto-delete after <b>{settings.delivery_delete_minutes} minutes</b>.\n\n"
                    "Warning: Please forward/share or save the content before the time limit, otherwise it will be deleted from this chat."
                ),
            )
        if not delivered:
            return DeliveryResult(
                False,
                len(sent_content_message_ids),
                len(delivery_items),
                "Order status update failed",
            )
        return DeliveryResult(True, len(sent_content_message_ids), len(delivery_items))
    except Exception as e:
        logger.warning("Auto delivery failed for order %s: %s", order_id, e)
        return DeliveryResult(False, len(sent_content_message_ids), len(delivery_items), str(e))
    finally:
        if customer_bot_token:
            await delivery_bot.session.close()
