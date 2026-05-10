"""
services/order_feed_service.py
Shared helpers for admin support reply sync and central order channel feed.
"""

from __future__ import annotations

import html
import logging
import re

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from database.models import Order, OrderStatus
from services.db_service import (
    get_all_admins,
    get_order,
    get_settings,
    get_user,
    set_order_channel_message_id,
)

logger = logging.getLogger(__name__)

_ORDER_ID_RE = re.compile(r"#?(ORD[A-Z0-9]+)", re.IGNORECASE)
_BOT_USERNAME_CACHE: str | None = None


def _format_dt(value) -> str:
    return value.strftime("%d %b %Y %H:%M") if value else "-"


def _safe(value: str | None) -> str:
    return html.escape(value or "-")


def _parse_chat_id(raw_value: str | None) -> int | None:
    value = (raw_value or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning(f"Invalid order feed chat id configured: {value!r}")
        return None


def _extract_order_context(message: Message | None) -> str | None:
    if not message:
        return None

    haystacks = [
        message.text or "",
        message.caption or "",
        message.html_text or "",
        message.html_caption or "",
    ]
    for text in haystacks:
        match = _ORDER_ID_RE.search(text)
        if match:
            return match.group(1).upper()
    return None


def _order_status_label(order: Order) -> str:
    if order.status == OrderStatus.pending:
        return "Pending Payment"
    if order.status == OrderStatus.submitted:
        return "Payment Submitted"
    if order.status == OrderStatus.rejected:
        return "Rejected"
    if order.status == OrderStatus.expired:
        return "Expired"
    if order.status == OrderStatus.cancelled:
        return "Cancelled"
    if order.status == OrderStatus.delivered:
        return "Delivered"
    if order.status == OrderStatus.paid:
        if (order.requirements_text_snapshot or "").strip() and not order.requirements_received:
            return "Awaiting Customer Details"
        if order.customer_requirements_response:
            return "Customer Details Received"
        return "Payment Approved"
    return str(order.status.value).replace("_", " ").title()


def _render_order_feed_text(order: Order) -> str:
    username = f"@{order.user.username}" if order.user and order.user.username else "No username"
    text = (
        "<b>Order Feed</b>\n\n"
        f"Order: <b>#{order.order_id}</b>\n"
        f"Status: <b>{_safe(_order_status_label(order))}</b>\n"
        f"User ID: <code>{order.user_id}</code>\n"
        f"Username: {_safe(username)}\n"
        f"Product: {_safe(order.product_name)}\n"
        f"Plan: {_safe(order.plan_name)}\n"
        f"Amount: <b>Rs {order.amount:.0f}</b>\n"
        f"Created: {_format_dt(order.created_at)}\n"
        f"Paid: {_format_dt(order.paid_at)}\n"
        f"Delivered: {_format_dt(order.delivered_at)}"
    )
    if order.reject_reason:
        text += f"\n\n<b>Reject Reason:</b>\n{_safe(order.reject_reason)}"
    if order.requirements_text_snapshot and not order.requirements_received:
        text += f"\n\n<b>Awaiting Details:</b>\n{_safe(order.requirements_text_snapshot)}"
    if order.customer_requirements_response:
        text += f"\n\n<b>Customer Details:</b>\n{_safe(order.customer_requirements_response)}"
    return text


def _is_feed_eligible(order: Order) -> bool:
    if order.status == OrderStatus.delivered:
        return True
    return order.status == OrderStatus.paid and bool(order.requirements_received)


async def _get_bot_username(bot: Bot) -> str | None:
    global _BOT_USERNAME_CACHE

    if _BOT_USERNAME_CACHE is not None:
        return _BOT_USERNAME_CACHE or None

    try:
        me = await bot.get_me()
        _BOT_USERNAME_CACHE = (me.username or "").strip()
    except Exception as e:
        logger.warning(f"Could not fetch bot username for order feed deep-links: {e}")
        _BOT_USERNAME_CACHE = ""
    return _BOT_USERNAME_CACHE or None


def _build_paid_order_markup(bot_username: str | None, order: Order) -> InlineKeyboardMarkup | None:
    if order.status != OrderStatus.paid:
        return None
    if not order.requirements_received:
        return None
    if not bot_username:
        return None

    deep_link = f"https://t.me/{bot_username}?start=deliver_{order.order_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Open Delivery Session", url=deep_link)],
        ]
    )


async def _cleanup_feed_message(bot: Bot, feed_chat_id: int, order: Order) -> None:
    if not order.channel_message_id:
        return

    try:
        await bot.delete_message(chat_id=feed_chat_id, message_id=order.channel_message_id)
    except Exception as e:
        logger.warning(
            f"Could not delete order feed message for order {order.order_id}: {e}"
        )
    await set_order_channel_message_id(order.order_id, None)


async def sync_order_feed(bot: Bot, order_id: str) -> None:
    """Create, update, or remove the central channel feed message for an order."""
    try:
        settings_row = await get_settings()
        feed_chat_id = _parse_chat_id(settings_row.order_feed_chat_id)
        if feed_chat_id is None:
            return

        order = await get_order(order_id)
        if not order:
            return

        if not _is_feed_eligible(order):
            await _cleanup_feed_message(bot, feed_chat_id, order)
            return

        text = _render_order_feed_text(order)
        bot_username = await _get_bot_username(bot) if order.status == OrderStatus.paid else None
        markup = _build_paid_order_markup(bot_username, order)

        if order.channel_message_id:
            try:
                await bot.edit_message_text(
                    chat_id=feed_chat_id,
                    message_id=order.channel_message_id,
                    text=text,
                    reply_markup=markup,
                )
                return
            except Exception as e:
                if "message is not modified" in str(e).lower():
                    return
                logger.warning(
                    f"Could not edit order feed message for order {order.order_id}: {e}"
                )

        sent = await bot.send_message(chat_id=feed_chat_id, text=text, reply_markup=markup)
        await set_order_channel_message_id(order.order_id, sent.message_id)
    except Exception as e:
        logger.warning(f"Could not sync order feed for order {order_id}: {e}")


async def broadcast_admin_support_reply(bot: Bot, message: Message, user_id: int) -> None:
    """Broadcast a successful admin support reply to all admins."""
    try:
        admins = await get_all_admins()
        if not admins:
            return

        user = await get_user(user_id)
        admin_name = html.escape(message.from_user.full_name or str(message.from_user.id))
        admin_username = f"@{html.escape(message.from_user.username)}" if message.from_user.username else "No username"
        user_name = html.escape(user.full_name if user else str(user_id))
        user_username = (
            f"@{html.escape(user.username)}"
            if user and user.username
            else "No username"
        )
        order_context = _extract_order_context(message.reply_to_message)

        text_or_caption = (message.html_text or message.html_caption or "").strip()
        header = (
            "<b>Support Reply Sent</b>\n\n"
            f"Admin: <b>{admin_name}</b> ({admin_username})\n"
            f"User: <b>{user_name}</b> ({user_username})\n"
            f"User ID: <code>{user_id}</code>"
        )
        if order_context:
            header += f"\nOrder: <b>#{html.escape(order_context)}</b>"
        if text_or_caption:
            header += f"\n\n<b>Reply:</b>\n{text_or_caption}"

        copy_actual_message = bool(
            message.photo
            or message.video
            or message.document
            or message.audio
            or message.voice
            or message.sticker
            or message.animation
            or message.video_note
        )
        for admin in admins:
            try:
                await bot.send_message(chat_id=admin.id, text=header)
                if copy_actual_message:
                    await bot.copy_message(
                        chat_id=admin.id,
                        from_chat_id=message.chat.id,
                        message_id=message.message_id,
                    )
            except Exception as e:
                logger.warning(f"Could not broadcast support reply to admin {admin.id}: {e}")
    except Exception as e:
        logger.warning(f"Could not broadcast support reply for user {user_id}: {e}")
