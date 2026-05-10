"""
handlers/user/orders.py
User can view their order history with /myorders or the button.
"""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from keyboards.keyboards import main_menu_kb, my_orders_kb, OrderDetailCD
from services.db_service import get_order, get_user_orders

router = Router()

STATUS_TEXT = {
    "pending":   "⏳ Awaiting payment",
    "submitted": "📤 Payment under review",
    "paid":      "✅ Payment approved",
    "delivered": "📦 Delivered",
    "rejected":  "❌ Payment rejected",
    "expired":   "🕐 Expired",
    "cancelled": "🚫 Cancelled",
}


async def _edit_or_send(callback: CallbackQuery, text: str, reply_markup):
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except Exception:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(text, reply_markup=reply_markup)


@router.message(Command("myorders"))
@router.callback_query(F.data == "my_orders")
async def show_orders(event: Message | CallbackQuery):
    user_id = event.from_user.id
    orders = await get_user_orders(user_id)

    if not orders:
        text = "📦 <b>My Orders</b>\n\nYou haven't placed any orders yet."
        kb = main_menu_kb()
    else:
        text = f"📦 <b>My Orders</b> ({len(orders)} recent)\n\nTap an order for details:"
        kb = my_orders_kb(orders)

    if isinstance(event, CallbackQuery):
        await _edit_or_send(event, text, kb)
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb)


@router.callback_query(OrderDetailCD.filter())
async def order_detail(callback: CallbackQuery, callback_data: OrderDetailCD):
    order_id = callback_data.order_id
    order = await get_order(order_id)

    if not order:
        await callback.answer("Order not found.", show_alert=True)
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Back to Orders", callback_data="my_orders")
    builder.button(text="🏠 Main Menu",      callback_data="main_menu")
    builder.adjust(1)

    status_label = STATUS_TEXT.get(order.status.value, order.status.value)

    text = (
        f"🆔 <b>Order #{order.order_id}</b>\n\n"
        f"📦 Product: {order.product_name}\n"
        f"📋 Plan:    {order.plan_name}\n"
        f"💰 Amount:  ₹{order.amount:.0f}\n"
        f"📊 Status:  {status_label}\n"
        f"🕐 Created: {order.created_at.strftime('%d %b %Y, %H:%M')}\n"
    )

    if order.reject_reason:
        text += f"\n❌ Reject reason: {order.reject_reason}"

    await _edit_or_send(callback, text, builder.as_markup())
    await callback.answer()
