"""
handlers/admin/broadcast.py
Owner/super_admin: broadcast a message to all users.
"""

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config.settings import settings
from keyboards.keyboards import back_to_admin_kb, confirm_broadcast_kb
from middlewares.role_filter import OwnerOrSuperFilter
from services.db_service import get_all_users
from states.states import BroadcastStates

logger = logging.getLogger(__name__)
router = Router()


async def _start_broadcast_prompt(message: Message, state: FSMContext):
    await state.set_state(BroadcastStates.waiting_message)
    await message.answer(
        "<b>Broadcast</b>\n\n"
        "Send the message you want to broadcast to all users.\n"
        "Supports text, photos, videos, documents.\n\n"
        "This will be sent to <b>all registered users</b>."
    )


@router.message(Command("broadcast"), OwnerOrSuperFilter())
async def cmd_broadcast_start(message: Message, state: FSMContext):
    await _start_broadcast_prompt(message, state)


@router.callback_query(F.data == "admin:broadcast", OwnerOrSuperFilter())
async def cb_broadcast_start(callback: CallbackQuery, state: FSMContext):
    await _start_broadcast_prompt(callback.message, state)
    await callback.answer()


@router.message(BroadcastStates.waiting_message, OwnerOrSuperFilter())
async def handle_broadcast_message(message: Message, state: FSMContext):
    await state.update_data(
        broadcast_chat_id=message.chat.id,
        broadcast_message_id=message.message_id,
    )
    await state.set_state(BroadcastStates.confirming)

    users = await get_all_users()
    await message.answer(
        f"<b>Confirm Broadcast</b>\n\n"
        f"This message will be sent to <b>{len(users)} users</b>.\n\n"
        f"Proceed?",
        reply_markup=confirm_broadcast_kb(),
    )


@router.callback_query(F.data == "broadcast_confirm", BroadcastStates.confirming, OwnerOrSuperFilter())
async def cb_confirm_broadcast(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()

    users = await get_all_users()
    total = len(users)
    sent = 0
    failed = 0

    status_msg = await callback.message.edit_text(
        f"Broadcasting to {total} users...\n0% complete"
    )

    for i, user in enumerate(users, 1):
        try:
            await bot.copy_message(
                chat_id=user.id,
                from_chat_id=data["broadcast_chat_id"],
                message_id=data["broadcast_message_id"],
            )
            sent += 1
        except Exception as exc:
            failed += 1
            logger.warning("Broadcast failed for user %s: %s", user.id, exc)

        if total and i % 50 == 0:
            pct = int(i / total * 100)
            try:
                await status_msg.edit_text(
                    f"Broadcasting... {pct}% ({i}/{total})"
                )
            except Exception:
                pass

        await asyncio.sleep(settings.BROADCAST_DELAY)

    await status_msg.edit_text(
        f"<b>Broadcast Complete</b>\n\n"
        f"Sent: <b>{sent}</b>\n"
        f"Failed: <b>{failed}</b>\n"
        f"Total: <b>{total}</b>",
        reply_markup=back_to_admin_kb(),
    )
    await callback.answer()
