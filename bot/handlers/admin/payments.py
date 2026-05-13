"""
handlers/admin/payments.py
Payment admin: approve or reject payment screenshots.
"""

import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import AdminRole, OrderStatus
from keyboards.keyboards import (
    ApprovePaymentCD,
    RejectPaymentCD,
    ViewPaymentCD,
    back_to_admin_kb,
    confirm_deliver_kb,
    payment_verify_kb,
)
from middlewares.role_filter import PaymentAdminFilter
from services.db_service import (
    add_payment_admin_message,
    approve_payment,
    clear_payment_admin_messages,
    get_admins_by_role,
    get_order,
    get_orders_by_status,
    get_payment_admin_messages,
    log_action,
    reject_payment,
)
from services.order_feed_service import sync_order_feed
from states.states import RejectPaymentStates

logger = logging.getLogger(__name__)
router = Router()


async def _ack(callback: CallbackQuery, *args, **kwargs) -> None:
    try:
        await callback.answer(*args, **kwargs)
    except Exception:
        pass


def _payment_review_text(order) -> str:
    username = order.user.username or order.user.full_name
    return (
        "<b>Payment Review</b>\n\n"
        f"User: @{username} (<code>{order.user_id}</code>)\n"
        f"Order: <b>#{order.order_id}</b>\n"
        f"Product: {order.product_name}\n"
        f"Plan: {order.plan_name}\n"
        f"Amount: <b>Rs {order.amount:.0f}</b>"
    )


def _payment_processed_text(order, actor_label: str, approved: bool, reason: str | None = None) -> str:
    status = "APPROVED" if approved else "REJECTED"
    text = (
        f"{_payment_review_text(order)}\n\n"
        f"<b>{status}</b>\n"
        f"By: <b>{actor_label}</b>"
    )
    if reason:
        text += f"\nReason: {reason}"
    return text


async def _edit_payment_admin_messages(bot: Bot, order, text: str) -> None:
    messages = await get_payment_admin_messages(order.order_id)
    for item in messages:
        try:
            if item.get("message_type") == "photo":
                await bot.edit_message_caption(
                    chat_id=item["chat_id"],
                    message_id=item["message_id"],
                    caption=text,
                    reply_markup=None,
                )
            else:
                await bot.edit_message_text(
                    chat_id=item["chat_id"],
                    message_id=item["message_id"],
                    text=text,
                    reply_markup=None,
                )
        except Exception as e:
            logger.warning("Could not update payment admin message %s: %s", item, e)


@router.callback_query(F.data == "admin:payments", PaymentAdminFilter())
async def cb_payments_menu(callback: CallbackQuery):
    await _ack(callback)
    try:
        orders, total = await get_orders_by_status(OrderStatus.submitted)
    except Exception as e:
        logger.error("Error fetching payments: %s", e)
        await _ack(callback, "Something went wrong. Please try again or contact support.", show_alert=True)
        return

    if not orders:
        await callback.message.edit_text(
            "<b>Payments</b>\n\nNo pending verifications.",
            reply_markup=back_to_admin_kb(),
        )
        return

    builder = InlineKeyboardBuilder()
    for order in orders:
        builder.button(
            text=f"#{order.order_id} - Rs {order.amount:.0f} - {order.product_name[:20]}",
            callback_data=ViewPaymentCD(order_id=order.order_id).pack(),
        )
    builder.button(text="Admin Panel", callback_data="admin:panel")
    builder.adjust(1)

    await callback.message.edit_text(
        f"<b>Pending Verifications</b> ({total})\n\nSelect to review:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(ViewPaymentCD.filter(), PaymentAdminFilter())
async def cb_view_payment(callback: CallbackQuery, callback_data: ViewPaymentCD, bot: Bot):
    await _ack(callback)
    try:
        order = await get_order(callback_data.order_id)
    except Exception as e:
        logger.error("Error viewing payment: %s", e)
        await _ack(callback, "Something went wrong. Please try again or contact support.", show_alert=True)
        return

    if not order or order.status != OrderStatus.submitted:
        await _ack(callback, "Order not available.", show_alert=True)
        return

    text = _payment_review_text(order)
    kb = payment_verify_kb(order.order_id)

    if order.screenshot_file_id:
        sent = await bot.send_photo(
            chat_id=callback.from_user.id,
            photo=order.screenshot_file_id,
            caption=text,
            reply_markup=kb,
        )
        await add_payment_admin_message(order.order_id, callback.from_user.id, sent.message_id, "photo")
    else:
        await callback.message.edit_text(text, reply_markup=kb)
        await add_payment_admin_message(order.order_id, callback.from_user.id, callback.message.message_id, "text")


@router.callback_query(ApprovePaymentCD.filter(), PaymentAdminFilter())
async def cb_approve_payment(
    callback: CallbackQuery,
    callback_data: ApprovePaymentCD,
    bot: Bot,
    dispatcher: Dispatcher | None = None,
):
    await _ack(callback, "Approving payment...")
    try:
        order_id = callback_data.order_id
        order = await get_order(order_id)
        if not order or order.status != OrderStatus.submitted:
            await _ack(callback, "Order already processed.", show_alert=True)
            return

        await approve_payment(order_id, callback.from_user.id)
        await log_action(callback.from_user.id, "approve_payment", order_id)
    except Exception as e:
        logger.error("Error approving payment: %s", e)
        await _ack(callback, "Something went wrong. Please try again or contact support.", show_alert=True)
        return

    actor_label = callback.from_user.username or callback.from_user.full_name or str(callback.from_user.id)
    await _edit_payment_admin_messages(
        bot,
        order,
        _payment_processed_text(order, actor_label, approved=True),
    )
    await clear_payment_admin_messages(order_id)

    from handlers.user.payment import _handle_post_payment_confirmation

    await _handle_post_payment_confirmation(bot, order_id, dispatcher)
    await _ack(callback, "Payment approved.", show_alert=True)


async def _notify_order_admins(bot: Bot, order):
    admins = await get_admins_by_role(AdminRole.order_admin, AdminRole.super_admin, AdminRole.owner)
    text = (
        "<b>New Order Ready to Deliver</b>\n\n"
        f"Order: #{order.order_id}\n"
        f"Product: {order.product_name}\n"
        f"Plan: {order.plan_name}\n"
        f"Amount: Rs {order.amount:.0f}"
    )
    if order.customer_requirements_response:
        text += f"\n\n<b>User Details:</b>\n{order.customer_requirements_response}"

    kb = confirm_deliver_kb(order.order_id)
    for admin in admins:
        try:
            await bot.send_message(chat_id=admin.id, text=text, reply_markup=kb)
        except Exception as e:
            logger.warning("Could not notify order admin %s: %s", admin.id, e)


async def _notify_payment_admins_status(
    bot: Bot,
    order,
    actor_id: int,
    actor_label: str,
    approved: bool,
    reason: str | None = None,
):
    admins = await get_admins_by_role(AdminRole.payment_admin, AdminRole.super_admin, AdminRole.owner)
    status_text = "APPROVED" if approved else "REJECTED"
    actor_display = actor_label or str(actor_id)
    text = (
        "<b>Payment Update</b>\n\n"
        f"Order: <b>#{order.order_id}</b>\n"
        f"Product: {order.product_name}\n"
        f"Plan: {order.plan_name}\n"
        f"Amount: Rs {order.amount:.0f}\n"
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
            logger.warning("Could not notify payment admin %s: %s", admin.id, e)


@router.callback_query(RejectPaymentCD.filter(), PaymentAdminFilter())
async def cb_reject_payment(callback: CallbackQuery, callback_data: RejectPaymentCD, state: FSMContext):
    await _ack(callback)
    await state.set_state(RejectPaymentStates.waiting_reason)
    await state.update_data(order_id=callback_data.order_id)
    await callback.message.answer(
        f"<b>Reject Payment</b>\n\n"
        f"Order: #{callback_data.order_id}\n\n"
        "Please type the reason for rejection:"
    )


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
        logger.error("Error rejecting payment: %s", e)
        await message.answer("Something went wrong. Please try again or contact support.")
        return

    await state.clear()
    await sync_order_feed(bot, order_id)

    actor_label = message.from_user.username or message.from_user.full_name or str(message.from_user.id)
    await _edit_payment_admin_messages(
        bot,
        order,
        _payment_processed_text(order, actor_label, approved=False, reason=reason),
    )
    await clear_payment_admin_messages(order_id)

    await message.answer(
        f"Order #{order_id} rejected.\nReason: {reason}",
        reply_markup=back_to_admin_kb(),
    )

    try:
        await bot.send_message(
            chat_id=order.user_id,
            text=(
                f"<b>Payment Rejected</b>\n\n"
                f"Order: <b>#{order_id}</b>\n"
                f"Reason: {reason}\n\n"
                "Please create a new order and try again."
            ),
        )
    except Exception as e:
        logger.warning("Could not notify user %s: %s", order.user_id, e)
