"""
handlers/admin/settings.py
Owner/super-admin settings: UPI, timeout, maintenance, gateway, XWallet key, order feed.
"""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from keyboards.keyboards import SettingCD, back_to_admin_kb
from middlewares.role_filter import OwnerOrSuperFilter
from services.db_service import get_settings, update_setting
from states.states import SettingsStates

router = Router()


def _mask_api_key(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return "Not set"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _format_channel_id(value: str | None) -> str:
    value = (value or "").strip()
    return value if value else "Not set"


@router.callback_query(F.data == "admin:settings", OwnerOrSuperFilter())
async def cb_settings(callback: CallbackQuery):
    s = await get_settings()
    gateway = (s.payment_gateway or "manual").lower()

    from aiogram.utils.keyboard import InlineKeyboardBuilder

    builder = InlineKeyboardBuilder()
    builder.button(text="Change UPI ID", callback_data=SettingCD(key="upi_id").pack())
    builder.button(text="Change UPI Name", callback_data=SettingCD(key="upi_name").pack())
    builder.button(text="Payment Timeout", callback_data=SettingCD(key="payment_timeout_minutes").pack())
    builder.button(text="Welcome Message", callback_data=SettingCD(key="welcome_message").pack())
    builder.button(text="Switch Gateway", callback_data=SettingCD(key="payment_gateway_toggle").pack())
    builder.button(text="Set XWallet API Key", callback_data=SettingCD(key="xwallet_api_key").pack())
    builder.button(text="Set Order Feed Channel", callback_data=SettingCD(key="order_feed_chat_id").pack())
    builder.button(text="Maintenance Mode", callback_data=SettingCD(key="maintenance_toggle").pack())
    builder.button(text="Admin Panel", callback_data="admin:panel")
    builder.adjust(2, 2, 2, 1, 1, 1)

    text = (
        "<b>Bot Settings</b>\n\n"
        f"UPI ID: <code>{s.upi_id}</code>\n"
        f"UPI Name: {s.upi_name}\n"
        f"Timeout: {s.payment_timeout_minutes} min\n"
        f"Gateway: <b>{gateway}</b>\n"
        f"XWallet Key: <code>{_mask_api_key(s.xwallet_api_key)}</code>\n"
        f"Order Feed Channel: <code>{_format_channel_id(s.order_feed_chat_id)}</code>\n"
        f"Maintenance: {'ON' if s.maintenance_mode else 'OFF'}"
    )
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(SettingCD.filter(), OwnerOrSuperFilter())
async def cb_change_setting(callback: CallbackQuery, callback_data: SettingCD, state: FSMContext):
    key = callback_data.key

    if key == "maintenance_toggle":
        s = await get_settings()
        new_val = not s.maintenance_mode
        await update_setting("maintenance_mode", new_val)
        status = "ON" if new_val else "OFF"
        await callback.answer(f"Maintenance mode: {status}", show_alert=True)
        await cb_settings(callback)
        return

    if key == "payment_gateway_toggle":
        s = await get_settings()
        current = (s.payment_gateway or "manual").lower()
        new_val = "xwallet" if current != "xwallet" else "manual"
        await update_setting("payment_gateway", new_val)
        await callback.answer(f"Payment gateway set to: {new_val}", show_alert=True)
        await cb_settings(callback)
        return

    prompts = {
        "upi_id": "Send new <b>UPI ID</b> (e.g. store@upi):",
        "upi_name": "Send new <b>UPI Name</b>:",
        "payment_timeout_minutes": "Send new <b>timeout in minutes</b> (e.g. 10):",
        "welcome_message": "Send new <b>welcome message</b>:",
        "xwallet_api_key": "Send new <b>XWallet API Key</b>:",
        "order_feed_chat_id": (
            "Send new <b>Order Feed Channel ID</b> (e.g. <code>-1001234567890</code>).\n"
            "Send <code>/clear</code> to disable the central order feed."
        ),
    }
    prompt = prompts.get(key, "Send new value:")
    await state.set_state(SettingsStates.setting_upi)
    await state.update_data(setting_key=key)
    await callback.message.answer(prompt)
    await callback.answer()


@router.message(SettingsStates.setting_upi, OwnerOrSuperFilter())
async def handle_setting_value(message: Message, state: FSMContext):
    data = await state.get_data()
    key = data["setting_key"]
    value = message.text.strip()

    if key == "payment_timeout_minutes":
        try:
            value = int(value)
        except ValueError:
            await message.answer("Please send a valid number.")
            return
        if value < 1:
            await message.answer("Timeout must be at least 1 minute.")
            return

    if key == "xwallet_api_key" and not value:
        await message.answer("API key cannot be empty.")
        return

    if key == "order_feed_chat_id":
        if value.lower() == "/clear":
            value = ""
        else:
            try:
                int(value)
            except ValueError:
                await message.answer("Please send a valid numeric Telegram channel ID or /clear.")
                return

    await update_setting(key, value)
    await state.clear()
    await message.answer(
        f"<b>{key.replace('_', ' ').title()}</b> updated.",
        reply_markup=back_to_admin_kb(),
    )
