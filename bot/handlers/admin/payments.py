"""
handlers/admin/payments.py
Payment admin: approve or reject payment screenshots.
"""

import logging
from aiogram import Bot, Dispatcher, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from keyboards.keyboards import (
    admin_panel_kb, back_to_admin_kb, payment_verify_kb,
    ApprovePaymentCD, RejectPaymentCD, ViewPaymentCD
)
from middlewares.role_filter import PaymentAdminFilter
from services.order_feed_service import sync_order_feed
from services.db_service import (
    approve_payment, get_order, get_orders_by_status,
    log_action, reject_payment,
)
from database.models import OrderStatus
from states.states import RejectPaymentStates

logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data == "admin:payments", PaymentAdminFilter())
async def cb_payments_menu(callback: CallbackQuery):
    try:
        orders, total = await get_orders_by_status(OrderStatus.submitted)
    except Exception as e:
        logger.error(f"Error fetching payments: {e}")
        await callback.answer("⚠️ Something went wrong. Please try again or contact support.", show_alert=True)
        return

    if not orders:
        await callback.message.edit_text(
            "💳 <b>Payments</b>\n\n✅ No pending verifications.",
            reply_markup=back_to_admin_kb(),
        )
        await callback.answer()
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    for o in orders:
        builder.button(
            text=f"#{o.order_id} — ₹{o.amount:.0f} — {o.product_name[:20]}",
            callback_data=ViewPaymentCD(order_id=o.order_id).pack(),
        )
    builder.button(text="◀️ Admin Panel", callback_data="admin:panel")
    builder.adjust(1)

    await callback.message.edit_text(
        f"💳 <b>Pending Verifications</b> ({total})\n\nSelect to review:",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(ViewPaymentCD.filter(), PaymentAdminFilter())
async def cb_view_payment(callback: CallbackQuery, callback_data: ViewPaymentCD, bot: Bot):
    try:
        order_id = callback_data.order_id
        order = await get_order(order_id)
    except Exception as e:
        logger.error(f"Error viewing payment: {e}")
        await callback.answer("⚠️ Something went wrong. Please try again or contact support.", show_alert=True)
        return

    if not order or order.status != OrderStatus.submitted:
        await callback.answer("Order not available.", show_alert=True)
        return

    username = order.user.username or order.user.full_name
    text = (
        f"💳 <b>Payment Review</b>\n\n"
        f"👤 User:    @{username}\n"
        f"🆔 Order:   #{order.order_id}\n"
        f"📦 Product: {order.product_name}\n"
        f"📋 Plan:    {order.plan_name}\n"
        f"💰 Amount:  ₹{order.amount:.0f}"
    )
    kb = payment_verify_kb(order.order_id)

    # Send screenshot to this admin
    if order.screenshot_file_id:
        await bot.send_photo(
            chat_id=callback.from_user.id,
            photo=order.screenshot_file_id,
            caption=text,
            reply_markup=kb,
        )
        await callback.answer()
    else:
        await callback.message.edit_text(text, reply_markup=kb)
        await callback.answer()


# ── Approve ───────────────────────────────────────────────────────────────────

@router.callback_query(ApprovePaymentCD.filter(), PaymentAdminFilter())
async def cb_approve_payment(
    callback: CallbackQuery,
    callback_data: ApprovePaymentCD,
    bot: Bot,
    dispatcher: Dispatcher | None = None,
):
    try:
        order_id = callback_data.order_id
        order = await get_order(order_id)

        if not order or order.status != OrderStatus.submitted:
            await callback.answer("Order already processed.", show_alert=True)
            return

        await approve_payment(order_id, callback.from_user.id)
        await log_action(callback.from_user.id, "approve_payment", order_id)
    except Exception as e:
        logger.error(f"Error approving payment: {e}")
        await callback.answer("⚠️ Something went wrong. Please try again or contact support.", show_alert=True)
        return

    # Update admin message
    await callback.message.edit_caption(
        caption=callback.message.caption + "\n\n✅ <b>APPROVED</b>",
        reply_markup=None,
    ) if callback.message.caption else await callback.message.edit_text(
        callback.message.text + "\n\n✅ <b>APPROVED</b>",
        reply_markup=None,
    )

    from handlers.user.payment import _handle_post_payment_confirmation

    await _handle_post_payment_confirmation(bot, order_id, dispatcher)

    await _notify_payment_admins_status(
        bot=bot,
        order=order,
        actor_id=callback.from_user.id,
        actor_label=callback.from_user.username or callback.from_user.full_name,
        approved=True,
    )

    await callback.answer("✅ Payment approved!", show_alert=True)


async def _notify_order_admins(bot: Bot, order):
    from services.db_service import get_admins_by_role
    from database.models import AdminRole
    from keyboards.keyboards import confirm_deliver_kb

    admins = await get_admins_by_role(AdminRole.order_admin, AdminRole.super_admin, AdminRole.owner)
    text = (
        f"📬 <b>New Order Ready to Deliver</b>\n\n"
        f"🆔 Order:   #{order.order_id}\n"
        f"📦 Product: {order.product_name}\n"
        f"📋 Plan:    {order.plan_name}\n"
        f"💰 Amount:  ₹{order.amount:.0f}"
    )
    if order.customer_requirements_response:
        text += f"\n\n📝 <b>User Details:</b>\n{order.customer_requirements_response}"
    kb = confirm_deliver_kb(order.order_id)
    for admin in admins:
        try:
            await bot.send_message(chat_id=admin.id, text=text, reply_markup=kb)
        except Exception as e:
            logger.warning(f"Could not notify order admin {admin.id}: {e}")


async def _notify_payment_admins_status(
    bot: Bot,
    order,
    actor_id: int,
    actor_label: str,
    approved: bool,
    reason: str | None = None,
):
    from services.db_service import get_admins_by_role
    from database.models import AdminRole

    admins = await get_admins_by_role(AdminRole.payment_admin, AdminRole.super_admin, AdminRole.owner)
    status_text = "APPROVED" if approved else "REJECTED"
    actor_display = actor_label or str(actor_id)
    text = (
        f"🔄 <b>Payment Update</b>\n\n"
        f"Order: <b>#{order.order_id}</b>\n"
        f"Product: {order.product_name}\n"
        f"Plan: {order.plan_name}\n"
        f"Amount: ₹{order.amount:.0f}\n"
        f"Status: <b>{status_text}</b>\n"
        f"By: <b>{actor_display}</b>"
    )
    if reason:
        text += f"\nReason: {reason}"

    for admin in admins:
        if admin.id == actor_id:
            continue
        try:
            await bot.send_message(chat_id=admin.id, text=text)
        except Exception as e:
            logger.warning(f"Could not notify payment admin {admin.id}: {e}")


# ── Reject ────────────────────────────────────────────────────────────────────

@router.callback_query(RejectPaymentCD.filter(), PaymentAdminFilter())
async def cb_reject_payment(callback: CallbackQuery, callback_data: RejectPaymentCD, state: FSMContext):
    order_id = callback_data.order_id
    await state.set_state(RejectPaymentStates.waiting_reason)
    await state.update_data(order_id=order_id, msg_id=callback.message.message_id)
    await callback.message.answer(
        f"❌ <b>Reject Payment</b>\n\n"
        f"Order: #{order_id}\n\n"
        f"Please type the reason for rejection:"
    )
    await callback.answer()


@router.message(RejectPaymentStates.waiting_reason, PaymentAdminFilter())
async def handle_reject_reason(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    order_id = data["order_id"]
    reason = message.text.strip()

    try:
        order = await get_order(order_id)
        if not order:
            await state.clear()
            return

        await reject_payment(order_id, message.from_user.id, reason)
        await log_action(message.from_user.id, "reject_payment", order_id, reason)
    except Exception as e:
        logger.error(f"Error rejecting payment: {e}")
        await message.answer("⚠️ Something went wrong. Please try again or contact support.")
        return
    await state.clear()
    await sync_order_feed(bot, order_id)

    await message.answer(
        f"❌ Order #{order_id} rejected.\nReason: {reason}",
        reply_markup=back_to_admin_kb(),
    )

    # Notify user
    try:
        await bot.send_message(
            chat_id=order.user_id,
            text=(
                f"❌ <b>Payment Rejected</b>\n\n"
                f"Order: <b>#{order_id}</b>\n"
                f"Reason: {reason}\n\n"
                f"Please create a new order and try again."
            ),
        )
    except Exception as e:
        logger.warning(f"Could not notify user {order.user_id}: {e}")

    await _notify_payment_admins_status(
        bot=bot,
        order=order,
        actor_id=message.from_user.id,
        actor_label=message.from_user.username or message.from_user.full_name,
        approved=False,
        reason=reason,
    )
