"""
handlers/support_chat.py
Relay direct user messages to admins and allow admins to reply in-thread.
"""

from __future__ import annotations

import html
import logging
from collections import OrderedDict

from aiogram import Bot, F, Router
from aiogram.types import Message

from middlewares.role_filter import AnyAdminFilter
from services.db_service import get_admin, get_all_admins
from services.order_feed_service import broadcast_admin_support_reply

logger = logging.getLogger(__name__)
router = Router()

_MAX_THREAD_MAP = 5000
_THREAD_MAP: "OrderedDict[tuple[int, int], int]" = OrderedDict()
_MAX_USER_THREAD_MAP = 3000
_USER_THREAD_LAST_MESSAGE: "OrderedDict[tuple[int, int], int]" = OrderedDict()


def _remember_thread(chat_id: int, message_id: int, user_id: int) -> None:
    key = (chat_id, message_id)
    _THREAD_MAP[key] = user_id
    _THREAD_MAP.move_to_end(key)

    while len(_THREAD_MAP) > _MAX_THREAD_MAP:
        _THREAD_MAP.popitem(last=False)


def _get_thread_user_id(chat_id: int, message_id: int) -> int | None:
    return _THREAD_MAP.get((chat_id, message_id))


def _remember_user_thread(chat_id: int, user_id: int, last_message_id: int) -> None:
    key = (chat_id, user_id)
    _USER_THREAD_LAST_MESSAGE[key] = last_message_id
    _USER_THREAD_LAST_MESSAGE.move_to_end(key)

    while len(_USER_THREAD_LAST_MESSAGE) > _MAX_USER_THREAD_MAP:
        _USER_THREAD_LAST_MESSAGE.popitem(last=False)


def _get_user_thread_last_message(chat_id: int, user_id: int) -> int | None:
    return _USER_THREAD_LAST_MESSAGE.get((chat_id, user_id))


def _user_label(message: Message) -> str:
    user = message.from_user
    name = html.escape(user.full_name or str(user.id))
    username = f"@{html.escape(user.username)}" if user.username else "No username"
    return f"{name} ({username})"


async def _copy_with_fallback(
    bot: Bot,
    target_chat_id: int,
    message: Message,
    reply_to_message_id: int | None = None,
) -> int | None:
    try:
        kwargs = {
            "chat_id": target_chat_id,
            "from_chat_id": message.chat.id,
            "message_id": message.message_id,
        }
        if reply_to_message_id is not None:
            kwargs["reply_to_message_id"] = reply_to_message_id

        copied = await bot.copy_message(**kwargs)
        return copied.message_id
    except Exception as e:
        if reply_to_message_id is not None:
            try:
                copied = await bot.copy_message(
                    chat_id=target_chat_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id,
                )
                return copied.message_id
            except Exception as second_error:
                logger.warning(
                    f"Could not copy message {message.message_id} to {target_chat_id}: "
                    f"{e} | retry failed: {second_error}"
                )
                return None

        logger.warning(f"Could not copy message {message.message_id} to {target_chat_id}: {e}")
        return None


@router.message(AnyAdminFilter(), F.reply_to_message)
async def handle_admin_support_reply(message: Message, bot: Bot):
    if not message.reply_to_message:
        return

    user_id = _get_thread_user_id(message.chat.id, message.reply_to_message.message_id)
    if not user_id:
        return

    admin_name = html.escape(message.from_user.full_name or str(message.from_user.id))
    header = (
        f"💬 <b>Reply From Support</b>\n\n"
        f"Admin: <b>{admin_name}</b>"
    )

    try:
        await bot.send_message(chat_id=user_id, text=header)
        await bot.copy_message(
            chat_id=user_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
        await broadcast_admin_support_reply(bot, message, user_id)
        await message.reply("Reply sent to user.")
    except Exception as e:
        logger.warning(f"Could not send admin reply to user {user_id}: {e}")
        await message.reply("Could not send this reply to the user.")


@router.message()
async def relay_user_message_to_admins(message: Message, bot: Bot):
    if await get_admin(message.from_user.id):
        return

    admins = await get_all_admins()
    if not admins:
        await message.reply("Support is not available right now. Please try again later.")
        return

    relayed = 0
    user_info = (
        f"📩 <b>New User Message</b>\n\n"
        f"User: <b>{_user_label(message)}</b>\n"
        f"ID: <code>{message.from_user.id}</code>\n\n"
        f"Reply to this message or the attached user message to continue the conversation."
    )

    for admin in admins:
        try:
            last_thread_message_id = _get_user_thread_last_message(
                admin.id, message.from_user.id
            )

            if last_thread_message_id is None:
                info_msg = await bot.send_message(chat_id=admin.id, text=user_info)
                _remember_thread(admin.id, info_msg.message_id, message.from_user.id)
                last_thread_message_id = info_msg.message_id

            copied_message_id = await _copy_with_fallback(
                bot,
                admin.id,
                message,
                reply_to_message_id=last_thread_message_id,
            )
            if copied_message_id is not None:
                _remember_thread(admin.id, copied_message_id, message.from_user.id)
                _remember_user_thread(admin.id, message.from_user.id, copied_message_id)
            relayed += 1
        except Exception as e:
            logger.warning(f"Could not relay user message to admin {admin.id}: {e}")

    if relayed:
        await message.reply("Your message has been sent to support. They can reply here.")
    else:
        await message.reply("Support could not be reached right now. Please try again later.")
