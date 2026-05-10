"""
handlers/admin/orders.py
Order admin: pending deliveries + owner/superadmin full order history.
"""

import html
import math

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config.settings import settings
from database.models import Order, OrderStatus
from keyboards.keyboards import (
    AdminAllOrdersPageCD,
    AdminDeliverCD,
    AdminMessageUserCD,
    AdminOrderInfoCD,
    AdminOrdersPageCD,
    AdminStopMessageUserCD,
    DoDeliverCD,
    admin_orders_page_kb,
    back_to_admin_kb,
)
from middlewares.role_filter import OrderAdminFilter, OwnerOrSuperFilter
from services.db_service import (
    get_all_orders_page,
    get_delivery_ready_orders,
    get_order,
    log_action,
    mark_delivered,
)
from services.order_feed_service import sync_order_feed
from states.states import ContactUserStates, DeliverOrderStates
from handlers.user.captcha import deliver_pending_request, require_captcha_or_continue

router = Router()


def _status_label(status: OrderStatus) -> str:
    mapping = {
        OrderStatus.pending: "PENDING",
        OrderStatus.submitted: "SUBMITTED",
        OrderStatus.paid: "PAID",
        OrderStatus.delivered: "DELIVERED",
        OrderStatus.rejected: "REJECTED",
        OrderStatus.expired: "EXPIRED",
        OrderStatus.cancelled: "CANCELLED",
    }
    return mapping.get(status, str(status))


def _is_delivery_ready(order: Order) -> bool:
    return order.status == OrderStatus.paid and bool(order.requirements_received)


def _all_orders_kb(orders: list[Order], page: int, total: int):
    builder = InlineKeyboardBuilder()
    for order in orders:
        plan = (order.plan_name or "-")[:14]
        builder.button(
            text=f"#{order.order_id} | U:{order.user_id} | {plan}",
            callback_data=AdminOrderInfoCD(order_id=order.order_id, page=page).pack(),
        )
    builder.adjust(1)

    per_page = settings.ORDERS_PER_PAGE
    total_pages = max(1, math.ceil(total / per_page))
    nav = []
    if page > 0:
        nav.append(("Prev", AdminAllOrdersPageCD(page=page - 1).pack()))
    nav.append((f"{page + 1}/{total_pages}", "noop"))
    if (page + 1) * per_page < total:
        nav.append(("Next", AdminAllOrdersPageCD(page=page + 1).pack()))

    for text, data in nav:
        builder.button(text=text, callback_data=data)
    builder.adjust(1, len(nav), 1)
    builder.button(text="Admin Panel", callback_data="admin:panel")
    return builder.as_markup()


def _contact_user_nav_kb(order_id: str, page: int, with_stop: bool = False):
    builder = InlineKeyboardBuilder()
    if with_stop:
        builder.button(
            text="Stop Messaging",
            callback_data=AdminStopMessageUserCD(order_id=order_id, page=page).pack(),
        )
    builder.button(
        text="Back to Order",
        callback_data=AdminOrderInfoCD(order_id=order_id, page=page).pack(),
    )
    builder.button(
        text="All Orders",
        callback_data=AdminAllOrdersPageCD(page=page).pack(),
    )
    builder.button(text="Admin Panel", callback_data="admin:panel")
    builder.adjust(1)
    return builder.as_markup()


def _delivery_done_kb(source: str, order_id: str, page: int | None):
    builder = InlineKeyboardBuilder()
    if source == "panel":
        builder.button(text="Orders Queue", callback_data="admin:orders")
    if page is not None:
        builder.button(
            text="Back to Order",
            callback_data=AdminOrderInfoCD(order_id=order_id, page=page).pack(),
        )
        builder.button(
            text="Back to All Orders",
            callback_data=AdminAllOrdersPageCD(page=page).pack(),
        )
    builder.button(text="Admin Panel", callback_data="admin:panel")
    builder.adjust(1)
    return builder.as_markup()


async def _start_delivery_session(
    target_message: Message,
    state: FSMContext,
    order: Order,
    source: str,
    page: int | None = None,
) -> None:
    await state.set_state(DeliverOrderStates.waiting_product)
    await state.update_data(
        deliver_order_id=order.order_id,
        deliver_user_id=order.user_id,
        deliver_intro_sent=False,
        deliver_source=source,
        deliver_page=page,
    )

    text = (
        f"<b>Delivery Session Started</b>\n\n"
        f"Order: <b>#{order.order_id}</b>\n"
        f"User ID: <code>{order.user_id}</code>\n"
        f"Product: {order.product_name}\n"
        f"Plan: {order.plan_name}\n\n"
    )
    if order.customer_requirements_response:
        text += (
            "<b>User Details:</b>\n"
            f"{order.customer_requirements_response}\n\n"
        )
    if source == "feed":
        text += "Source: Order Feed deep-link\n\n"
    text += (
        "Now send any text/file/media to forward it to this user.\n"
        "Use /complete when delivery is finished.\n"
        "Use /done or /stop to close this session without marking delivered."
    )
    await target_message.answer(text)


@router.message(
    F.text.regexp(r"^/start(?:@[A-Za-z0-9_]+)?\s+deliver_[A-Za-z0-9]+$"),
    OrderAdminFilter(),
)
async def cmd_start_delivery_from_feed(message: Message, state: FSMContext):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        return

    payload = parts[1].strip()
    if not payload.lower().startswith("deliver_"):
        return

    order_id = payload.split("_", 1)[1].upper()
    order = await get_order(order_id)
    if not order:
        await message.answer("Order not found.")
        return
    if not _is_delivery_ready(order):
        await message.answer("This order is not ready for delivery.")
        return

    await _start_delivery_session(message, state, order, source="feed")


@router.callback_query(F.data == "admin:orders", OrderAdminFilter())
async def cb_orders_menu(callback: CallbackQuery):
    await _show_orders_page(callback, 0)


@router.callback_query(AdminOrdersPageCD.filter(), OrderAdminFilter())
async def cb_orders_page(callback: CallbackQuery, callback_data: AdminOrdersPageCD):
    await _show_orders_page(callback, callback_data.page)


async def _show_orders_page(callback: CallbackQuery, page: int):
    orders, total = await get_delivery_ready_orders(page)
    if not orders and page == 0:
        await callback.message.edit_text(
            "<b>Pending Deliveries</b>\n\nNo orders to deliver.",
            reply_markup=back_to_admin_kb(),
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        f"<b>Pending Deliveries</b> ({total})\n\nSelect an order to deliver:",
        reply_markup=admin_orders_page_kb(orders, page, total),
    )
    await callback.answer()


@router.callback_query(AdminDeliverCD.filter(), OrderAdminFilter())
@router.callback_query(DoDeliverCD.filter(), OrderAdminFilter())
async def cb_do_deliver(callback: CallbackQuery, callback_data: AdminDeliverCD | DoDeliverCD, state: FSMContext):
    order_id = callback_data.order_id
    order = await get_order(order_id)
    if not order or not _is_delivery_ready(order):
        await callback.answer("Order not available for delivery.", show_alert=True)
        return

    await _start_delivery_session(callback.message, state, order, source="panel")
    await callback.answer()


@router.message(DeliverOrderStates.waiting_product, OrderAdminFilter())
async def handle_deliver_product(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    order_id = data.get("deliver_order_id")
    user_id = data.get("deliver_user_id")
    intro_sent = bool(data.get("deliver_intro_sent", False))
    source = str(data.get("deliver_source", "panel"))
    page_raw = data.get("deliver_page")
    page = int(page_raw) if isinstance(page_raw, int | str) and str(page_raw).isdigit() else None

    if not order_id or not user_id:
        await state.clear()
        await message.answer("Delivery session expired. Please open the order again.")
        return

    raw_text = (message.text or "").strip()
    command = raw_text.lower()

    if command in {"/done", "/stop", "/cancel"}:
        await state.clear()
        await message.answer(
            f"Delivery session closed for order #{order_id}.",
            reply_markup=_delivery_done_kb(source, order_id, page),
        )
        return

    if command == "/complete":
        order = await get_order(order_id)
        if not order:
            await state.clear()
            await message.answer("Order not found. Session closed.")
            return
        if not _is_delivery_ready(order):
            await state.clear()
            await message.answer(
                f"Order #{order_id} is no longer in deliverable state.",
                reply_markup=_delivery_done_kb(source, order_id, page),
            )
            return

        try:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    "<b>Order Completed</b>\n\n"
                    f"Order: <b>#{order_id}</b>\n"
                    f"Product: {order.product_name}\n\n"
                    "Your order has been completed successfully."
                ),
            )
        except Exception:
            await message.answer(
                "Could not send completion message to user. "
                "Order is not marked delivered. Please retry /complete.",
            )
            return

        delivered = await mark_delivered(order_id, message.from_user.id)
        if not delivered:
            await message.answer(
                "User has been notified, but database update failed. "
                "Please retry /complete.",
            )
            return

        await log_action(message.from_user.id, "deliver_order", order_id)
        await sync_order_feed(bot, order_id)
        await state.clear()
        await message.answer(
            f"Order #{order_id} marked delivered successfully.",
            reply_markup=_delivery_done_kb(source, order_id, page),
        )
        return

    if raw_text.startswith("/"):
        await message.answer(
            "This command is not forwarded in delivery mode. "
            "Use /complete, /done, or /stop.",
        )
        return

    order = await get_order(order_id)
    if not order:
        await state.clear()
        await message.answer("Order not found. Session closed.")
        return
    if not _is_delivery_ready(order):
        await state.clear()
        await message.answer(
            f"Order #{order_id} is no longer in deliverable state.",
            reply_markup=_delivery_done_kb(source, order_id, page),
        )
        return

    pending_request = {
        "kind": "copy_message",
        "from_chat_id": message.chat.id,
        "message_id": message.message_id,
        "order_id": order_id,
    }
    if not intro_sent:
        admin_name = html.escape(message.from_user.full_name or str(message.from_user.id))
        pending_request["intro_text"] = (
            "<b>Order Update</b>\n\n"
            f"Order: <b>#{order_id}</b>\n"
            f"From: <b>{admin_name}</b>"
        )
        await state.update_data(deliver_intro_sent=True)

    if not await require_captcha_or_continue(bot, int(user_id), pending_request):
        await message.answer(
            "User verification required. This delivery message is queued and will be sent after captcha."
        )
        return

    try:
        await deliver_pending_request(bot, pending_request, int(user_id))
    except Exception:
        await message.answer("Could not forward this message to the user. Please retry.")
        return

    await message.answer("Sent to user. Continue messaging or use /complete when done.")


@router.callback_query(F.data == "admin:all_orders", OwnerOrSuperFilter())
async def cb_all_orders_menu(callback: CallbackQuery):
    await _show_all_orders_page(callback, 0)


@router.callback_query(AdminAllOrdersPageCD.filter(), OwnerOrSuperFilter())
async def cb_all_orders_page(callback: CallbackQuery, callback_data: AdminAllOrdersPageCD):
    await _show_all_orders_page(callback, callback_data.page)


async def _show_all_orders_page(callback: CallbackQuery, page: int):
    orders, total = await get_all_orders_page(page)
    if not orders and page == 0:
        await callback.message.edit_text(
            "<b>All Orders History</b>\n\nNo orders found.",
            reply_markup=back_to_admin_kb(),
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        f"<b>All Orders History</b> ({total})\n\nOpen any order for details:",
        reply_markup=_all_orders_kb(orders, page, total),
    )
    await callback.answer()


@router.callback_query(AdminOrderInfoCD.filter(), OwnerOrSuperFilter())
async def cb_all_order_info(callback: CallbackQuery, callback_data: AdminOrderInfoCD):
    order = await get_order(callback_data.order_id)
    if not order:
        await callback.answer("Order not found.", show_alert=True)
        return

    created = order.created_at.strftime("%d %b %Y %H:%M") if order.created_at else "-"
    paid_at = order.paid_at.strftime("%d %b %Y %H:%M") if order.paid_at else "-"
    delivered_at = order.delivered_at.strftime("%d %b %Y %H:%M") if order.delivered_at else "-"
    username = order.user.username if order.user and order.user.username else "-"

    text = (
        f"<b>Order #{order.order_id}</b>\n\n"
        f"Status: <b>{_status_label(order.status)}</b>\n"
        f"User ID: <code>{order.user_id}</code>\n"
        f"Username: @{username}\n"
        f"Product: {order.product_name}\n"
        f"Plan: {order.plan_name}\n"
        f"Amount: <b>Rs {order.amount:.0f}</b>\n"
        f"UPI: <code>{order.upi_id}</code>\n"
        f"Created: {created}\n"
        f"Paid: {paid_at}\n"
        f"Delivered: {delivered_at}"
    )
    if order.requirements_text_snapshot:
        text += (
            f"\n\n<b>Required Info Prompt:</b>\n"
            f"{order.requirements_text_snapshot}"
        )
    if order.customer_requirements_response:
        text += (
            f"\n\n<b>User Submitted Details:</b>\n"
            f"{order.customer_requirements_response}"
        )

    builder = InlineKeyboardBuilder()
    builder.button(
        text="Message User",
        callback_data=AdminMessageUserCD(order_id=order.order_id, page=callback_data.page).pack(),
    )
    builder.button(
        text="Back to All Orders",
        callback_data=AdminAllOrdersPageCD(page=callback_data.page).pack(),
    )
    builder.button(text="Admin Panel", callback_data="admin:panel")
    builder.adjust(1)

    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(AdminMessageUserCD.filter(), OwnerOrSuperFilter())
async def cb_message_user(callback: CallbackQuery, callback_data: AdminMessageUserCD, state: FSMContext):
    order = await get_order(callback_data.order_id)
    if not order:
        await callback.answer("Order not found.", show_alert=True)
        return

    await state.set_state(ContactUserStates.waiting_message)
    await state.update_data(
        contact_user_id=order.user_id,
        contact_order_id=order.order_id,
        contact_page=callback_data.page,
        contact_intro_sent=False,
    )
    await callback.message.answer(
        f"<b>Message Mode Enabled</b>\n\n"
        f"Order: <b>#{order.order_id}</b>\n"
        f"User ID: <code>{order.user_id}</code>\n\n"
        "Any text/file/photo/video you send now will be forwarded to this user.\n"
        "Use /done or Stop Messaging to close this mode.",
        reply_markup=_contact_user_nav_kb(order.order_id, callback_data.page, with_stop=True),
    )
    await callback.answer()


@router.callback_query(AdminStopMessageUserCD.filter(), OwnerOrSuperFilter())
async def cb_stop_message_user(callback: CallbackQuery, callback_data: AdminStopMessageUserCD, state: FSMContext):
    if await state.get_state() == ContactUserStates.waiting_message.state:
        await state.clear()

    await callback.message.answer(
        f"Message mode closed for order #{callback_data.order_id}.",
        reply_markup=_contact_user_nav_kb(callback_data.order_id, callback_data.page),
    )
    await callback.answer("Message mode closed.")


@router.message(ContactUserStates.waiting_message, OwnerOrSuperFilter())
async def handle_message_user(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    user_id = data.get("contact_user_id")
    order_id = data.get("contact_order_id")
    page = int(data.get("contact_page", 0))
    intro_sent = bool(data.get("contact_intro_sent", False))

    if not user_id or not order_id:
        await state.clear()
        await message.answer("Session expired. Please open order details again.")
        return

    text = (message.text or "").strip().lower()
    if text in {"/done", "/stop", "/cancel"}:
        await state.clear()
        await message.answer(
            f"Message mode closed for order #{order_id}.",
            reply_markup=_contact_user_nav_kb(order_id, page),
        )
        return

    if message.text and message.text.strip().startswith("/"):
        await message.answer(
            "Command detected; it was not forwarded to the user.\n"
            "Use /done to close message mode.",
            reply_markup=_contact_user_nav_kb(order_id, page, with_stop=True),
        )
        return

    try:
        if not intro_sent:
            admin_name = html.escape(message.from_user.full_name or str(message.from_user.id))
            await bot.send_message(
                chat_id=user_id,
                text=(
                    "<b>Support Update</b>\n\n"
                    f"Order: <b>#{order_id}</b>\n"
                    f"From: <b>{admin_name}</b>"
                ),
            )
            await state.update_data(contact_intro_sent=True)
        await message.copy_to(chat_id=user_id)
        await log_action(message.from_user.id, "message_user", order_id)
    except Exception:
        await message.answer("Could not send this message to the user.")
        return

    await message.answer(
        "Sent to user. Continue messaging or /done to close.",
        reply_markup=_contact_user_nav_kb(order_id, page, with_stop=True),
    )
