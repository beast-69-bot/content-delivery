"""
handlers/admin/premium.py
Owner/super_admin: manually grant premium status to a user.
"""

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from keyboards.keyboards import back_to_admin_kb
from middlewares.role_filter import OwnerOrSuperFilter
from services.db_service import add_premium_user, log_action
from states.states import AddPremiumStates

router = Router()


def _format_until(user) -> str:
    if not user.premium_until:
        return "Lifetime"
    return user.premium_until.strftime("%d %b %Y %H:%M UTC")


async def _grant_premium(message: Message, state: FSMContext, user_id: int, days: int | None):
    user = await add_premium_user(user_id, message.from_user.id, days)
    await log_action(
        message.from_user.id,
        "add_premium",
        str(user_id),
        "lifetime" if days is None else f"{days} days",
    )
    await state.clear()
    await message.answer(
        f"Premium added manually.\n\n"
        f"User: <code>{user.id}</code>\n"
        f"Valid Until: <b>{_format_until(user)}</b>",
        reply_markup=back_to_admin_kb(),
    )


@router.message(Command("addpremium"), OwnerOrSuperFilter())
async def cmd_addpremium(message: Message, command: CommandObject, state: FSMContext):
    args = (command.args or "").split()
    if args:
        try:
            user_id = int(args[0])
            days = int(args[1]) if len(args) > 1 else None
            if user_id <= 0 or (days is not None and days <= 0):
                raise ValueError
        except ValueError:
            await message.answer(
                "Usage: <code>/addpremium user_id</code> for lifetime\n"
                "or <code>/addpremium user_id days</code> for limited premium."
            )
            return

        await _grant_premium(message, state, user_id, days)
        return

    await state.set_state(AddPremiumStates.waiting_user_id)
    await message.answer(
        "<b>Add Premium</b>\n\n"
        "Send the Telegram user ID.\n\n"
        "Shortcut: <code>/addpremium user_id days</code>\n"
        "Example: <code>/addpremium 123456789 30</code>"
    )


@router.message(AddPremiumStates.waiting_user_id, OwnerOrSuperFilter())
async def step_premium_user_id(message: Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())
        if user_id <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Invalid user ID. Send a positive numeric Telegram user ID.")
        return

    await state.update_data(premium_user_id=user_id)
    await state.set_state(AddPremiumStates.waiting_days)
    await message.answer(
        "Send premium duration in days, or send <code>lifetime</code>."
    )


@router.message(AddPremiumStates.waiting_days, OwnerOrSuperFilter())
async def step_premium_days(message: Message, state: FSMContext):
    raw = message.text.strip().lower()
    days = None
    if raw not in {"lifetime", "life", "0"}:
        try:
            days = int(raw)
            if days <= 0:
                raise ValueError
        except ValueError:
            await message.answer("Send a positive number of days, or <code>lifetime</code>.")
            return

    data = await state.get_data()
    await _grant_premium(message, state, int(data["premium_user_id"]), days)
