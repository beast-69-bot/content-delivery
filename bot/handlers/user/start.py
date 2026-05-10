"""
handlers/user/start.py
/start command and main menu navigation.
"""

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from aiogram.utils.keyboard import InlineKeyboardBuilder
from config.settings import settings
from keyboards.keyboards import ProductCD, main_menu_kb
from services.db_service import get_settings, get_user

router = Router()


async def _edit_or_send(callback: CallbackQuery, text: str, reply_markup):
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except Exception:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(text, reply_markup=reply_markup)


async def _send_main_menu(target: Message | CallbackQuery, state: FSMContext, edit: bool = False):
    await state.clear()
    bot_settings = await get_settings()
    text = (
        f"<b>{settings.STORE_NAME}</b>\n\n"
        f"{bot_settings.welcome_message}\n\n"
        f"Choose an option below 👇"
    )
    kb = main_menu_kb()

    if edit and isinstance(target, CallbackQuery):
        await _edit_or_send(target, text, kb)
    else:
        msg = target if isinstance(target, Message) else target.message
        await msg.answer(text, reply_markup=kb)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    args = message.text.split()
    if len(args) > 1:
        param = args[1]
        if param.startswith("prod_"):
            try:
                product_id = int(param.split("_")[1])
                # Redirect to product detail
                from handlers.user.products import cb_product_detail
                
                # Mock a callback query to reuse existing logic
                mock_cb = CallbackQuery(
                    id="0",
                    from_user=message.from_user,
                    chat_instance="0",
                    message=message,
                    data=f"product:{product_id}"
                )
                await cb_product_detail(mock_cb, ProductCD(id=product_id), state)
                return
            except Exception:
                pass

    await _send_main_menu(message, state)


@router.callback_query(F.data == "main_menu")
async def cb_main_menu(callback: CallbackQuery, state: FSMContext):
    await _send_main_menu(callback, state, edit=True)
    await callback.answer()


@router.callback_query(F.data == "support")
async def cb_support(callback: CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.button(text="🏠 Main Menu", callback_data="main_menu")
    kb.adjust(1)

    await _edit_or_send(
        callback,
        f"💬 <b>Support</b>\n\n"
        f"Contact us at {settings.SUPPORT_USERNAME}\n\n"
        f"You can also send a message directly in this chat and an admin will be notified.\n\n"
        f"Our team will assist you with any issues.",
        kb.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "my_profile")
async def cb_profile(callback: CallbackQuery, state: FSMContext):
    user = await get_user(callback.from_user.id)
    
    if not user:
        await callback.answer("User not found.", show_alert=True)
        return

    joined_date = user.created_at.strftime("%d %b %Y")
    
    text = (
        f"👤 <b>My Profile</b>\n\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"👤 Name: <b>{user.full_name}</b>\n"
        f"📅 Joined: <b>{joined_date}</b>\n\n"
        f"📊 <b>Stats:</b>\n"
        f"📦 Total Orders: <b>{user.total_orders}</b>\n"
        f"💰 Total Spent:  <b>₹{user.total_spent:.2f}</b>\n"
    )
    
    kb = InlineKeyboardBuilder()
    kb.button(text="📦 My Orders", callback_data="my_orders")
    kb.button(text="🏠 Main Menu", callback_data="main_menu")
    kb.adjust(1)
    
    await _edit_or_send(callback, text, kb.as_markup())
    await callback.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()
