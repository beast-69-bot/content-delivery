"""
handlers/user/payment.py
Order creation -> UPI/XWallet payment flow -> admin notification.
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from config.settings import settings
from keyboards.keyboards import (
    cancel_screenshot_kb, customer_bot_started_kb, deliver_now_kb, main_menu_kb, payment_sent_kb,
    ConfirmCustomerBotCD, ConfirmPartialCD, DeliverNowCD, OrderConfirmCD, PayFullCD, RedeliverOrderCD,
    UploadScreenshotCD, CancelOrderCD
)
from services.db_service import (
    add_order_flow_message, add_payment_admin_message, approve_payment,
    clear_order_flow_messages, count_pending_orders, create_order, get_order,
    get_admins_by_role, get_order_flow_messages, get_pending_requirements_order_for_user, get_plan, get_settings,
    save_customer_bot, save_customer_requirements, submit_screenshot, update_order_status,
    log_action,
)
from database.models import AdminRole, BotSettings, Order, OrderStatus
from services.auto_delivery_service import auto_deliver_order
from services.order_feed_service import sync_order_feed
from services.xwallet_service import create_payment, wait_for_payment
from states.states import PaymentStates
from utils.qr_generator import generate_upi_qr

logger = logging.getLogger(__name__)
router = Router()


async def _ack(callback: CallbackQuery, *args, **kwargs) -> None:
    try:
        await callback.answer(*args, **kwargs)
    except Exception:
        pass


async def _delete_order_flow_messages(bot: Bot, order_id: str) -> None:
    for item in await get_order_flow_messages(order_id):
        try:
            await bot.delete_message(chat_id=item["chat_id"], message_id=item["message_id"])
        except Exception as e:
            logger.debug("Could not delete payment flow message %s for %s: %s", item, order_id, e)
    await clear_order_flow_messages(order_id)


class PendingRequirementsFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        pending_order = await get_pending_requirements_order_for_user(message.from_user.id)
        return pending_order is not None


def _order_needs_requirements(order: Order) -> bool:
    return bool((order.requirements_text_snapshot or "").strip()) and not bool(order.requirements_received)


def _order_needs_customer_bot(order: Order) -> bool:
    return (
        bool(order.plan and order.plan.product and order.plan.product.delivery_mode == "customer_bot")
        and not bool(order.customer_bot_token)
    )


async def _set_requirements_state(dispatcher: Dispatcher | None, bot: Bot, user_id: int, order_id: str) -> None:
    if dispatcher is None:
        logger.warning(f"Dispatcher unavailable while setting requirements state for order {order_id}")
        return

    try:
        state = FSMContext(
            storage=dispatcher.storage,
            key=StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id),
        )
        await state.set_state(PaymentStates.waiting_requirements)
        await state.update_data(requirements_order_id=order_id)
    except Exception as e:
        logger.warning(f"Could not set requirements state for order {order_id}: {e}")


async def _set_customer_bot_state(dispatcher: Dispatcher | None, bot: Bot, user_id: int, order_id: str) -> None:
    if dispatcher is None:
        logger.warning(f"Dispatcher unavailable while setting customer bot state for order {order_id}")
        return

    try:
        state = FSMContext(
            storage=dispatcher.storage,
            key=StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id),
        )
        await state.set_state(PaymentStates.waiting_customer_bot_token)
        await state.update_data(customer_bot_order_id=order_id)
    except Exception as e:
        logger.warning(f"Could not set customer bot state for order {order_id}: {e}")


async def _handle_post_payment_confirmation(
    bot: Bot,
    order_id: str,
    dispatcher: Dispatcher | None = None,
) -> None:
    latest_order = await get_order(order_id)
    if not latest_order:
        return

    await bot.send_message(
        chat_id=latest_order.user_id,
        text=(
            "✅ <b>Payment Confirmed!</b>\n\n"
            f"Order <b>#{latest_order.order_id}</b> confirmed."
        ),
    )
    await _delete_order_flow_messages(bot, latest_order.order_id)

    if _order_needs_customer_bot(latest_order):
        await _set_customer_bot_state(dispatcher, bot, latest_order.user_id, latest_order.order_id)
        await bot.send_message(
            chat_id=latest_order.user_id,
            text=(
                "🤖 <b>Set Your Delivery Bot</b>\n\n"
                "Payment confirm ho gaya hai. Files aapke bot se deliver hongi.\n\n"
                "Ab apne bot ka <b>BOT_TOKEN</b> bhejo."
            ),
        )
        await sync_order_feed(bot, latest_order.order_id)
        return

    if _order_needs_requirements(latest_order):
        await _set_requirements_state(dispatcher, bot, latest_order.user_id, latest_order.order_id)
        await bot.send_message(
            chat_id=latest_order.user_id,
            text=(
                "📝 <b>Required Information Needed</b>\n\n"
                "Aapka payment confirm ho gaya hai. Order process karne ke liye yeh details bhejna zaroori hai:\n\n"
                f"{latest_order.requirements_text_snapshot}\n\n"
                "Please ab isi chat me required information ek message me bhej dein."
            ),
        )
        await sync_order_feed(bot, latest_order.order_id)
        return

    await sync_order_feed(bot, latest_order.order_id)
    await bot.send_message(
        chat_id=latest_order.user_id,
        text=(
            "<b>Your order is ready</b>\n\n"
            "Files abhi auto-send nahi hongi. Jab aap ready ho, neeche <b>Deliver Now</b> dabao.\n"
            "Delivery ke baad content time limit ke andar auto-delete ho jayega."
        ),
        reply_markup=deliver_now_kb(latest_order.order_id),
    )


async def _process_requirements_response(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    order_id = data.get("requirements_order_id")

    if not order_id:
        pending_order = await get_pending_requirements_order_for_user(message.from_user.id)
        order_id = pending_order.order_id if pending_order else None

    if not order_id:
        await state.clear()
        return

    response = message.text.strip()
    if not response:
        await message.answer("Please send the required details in text format.")
        return

    order = await get_order(order_id)
    if not order or order.status != OrderStatus.paid:
        await state.clear()
        await message.answer("This order is no longer waiting for details.", reply_markup=main_menu_kb())
        return

    await save_customer_requirements(order_id, response)
    await state.clear()
    await log_action(message.from_user.id, "submit_order_requirements", order_id)

    await message.answer(
        "✅ <b>Details Received!</b>\n\n"
        f"Order <b>#{order_id}</b> ke required details mil gaye hain.\n"
        "Ab admin aapka order process karega. 📦",
        reply_markup=main_menu_kb(),
    )

    await sync_order_feed(bot, order_id)
    await message.answer(
        "Files ready hain. Jab aap ready ho, neeche Deliver Now dabao.",
        reply_markup=deliver_now_kb(order_id),
    )


async def _process_customer_bot_token(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    order_id = data.get("customer_bot_order_id")
    if not order_id:
        await state.clear()
        return

    token = (message.text or "").strip()
    if ":" not in token:
        await message.answer("Invalid BOT_TOKEN. Please send valid token from @BotFather.")
        return

    customer_bot = Bot(token=token)
    try:
        bot_info = await customer_bot.get_me()
    except Exception:
        await message.answer("Invalid BOT_TOKEN ya bot reachable nahi hai. Correct token bhejo.")
        return
    finally:
        await customer_bot.session.close()

    await save_customer_bot(order_id, token, bot_info.username or "")
    await state.clear()

    await message.answer(
        "✅ <b>Bot Connected</b>\n\n"
        f"Delivery bot: @{bot_info.username}\n\n"
        "Ab apna bot open karke /start karo. Uske baad neeche OK dabao, files wahi bot deliver karega.",
        reply_markup=customer_bot_started_kb(order_id),
    )


async def _create_and_start_payment(
    callback: CallbackQuery,
    bot: Bot,
    dispatcher: Dispatcher | None,
    plan_id: int,
    amount: float | None = None,
    plan_name: str | None = None,
    delivery_items: list[dict] | None = None,
    selected_range: str | None = None,
    redelivery_of: str | None = None,
) -> None:
    await _ack(callback, "Processing...")
    user_id = callback.from_user.id
    try:
        pending = await count_pending_orders(user_id)
        if pending >= settings.MAX_PENDING_ORDERS:
            await _ack(
                callback,
                f"⚠️ You already have {pending} pending orders.\n"
                "Please complete or wait for them to expire.",
                show_alert=True,
            )
            return

        bot_settings = await get_settings()
        order = await create_order(
            user_id=user_id,
            plan_id=plan_id,
            upi_id=bot_settings.upi_id,
            amount=amount,
            plan_name=plan_name,
            delivery_items=delivery_items,
            selected_range=selected_range,
            redelivery_of=redelivery_of,
        )
        await sync_order_feed(bot, order.order_id)
    except ValueError as e:
        await _ack(callback, str(e), show_alert=True)
        return
    except Exception as e:
        logger.error(f"Error creating order: {e}")
        await _ack(callback, "⚠️ Something went wrong. Please try again or contact support.", show_alert=True)
        return

    gateway = (getattr(bot_settings, "payment_gateway", settings.PAYMENT_GATEWAY) or "manual").lower()
    if gateway == "xwallet":
        await _handle_xwallet_payment(callback, bot, dispatcher, order)
    else:
        await _handle_manual_payment(callback, bot, order, bot_settings)


@router.callback_query(PayFullCD.filter())
async def cb_pay_full(callback: CallbackQuery, callback_data: PayFullCD, bot: Bot, dispatcher: Dispatcher | None = None):
    await _create_and_start_payment(callback, bot, dispatcher, callback_data.plan_id)


@router.callback_query(ConfirmPartialCD.filter())
async def cb_confirm_partial(callback: CallbackQuery, callback_data: ConfirmPartialCD, bot: Bot, dispatcher: Dispatcher | None = None):
    await _ack(callback, "Processing...")
    plan = await get_plan(callback_data.plan_id)
    if not plan:
        await _ack(callback, "Plan not found.", show_alert=True)
        return
    total_files = len(plan.delivery_items or [])
    start = callback_data.start
    end = callback_data.end
    if start < 1 or end < start or end > total_files:
        await _ack(callback, "Invalid file range.", show_alert=True)
        return
    selected_items = list(plan.delivery_items or [])[start - 1:end]
    selected_count = end - start + 1
    amount = round((plan.price / total_files) * selected_count)
    await _create_and_start_payment(
        callback,
        bot,
        dispatcher,
        plan.id,
        amount=amount,
        plan_name=f"Files {start}-{end}",
        delivery_items=selected_items,
        selected_range=f"{start}-{end}",
    )


@router.callback_query(RedeliverOrderCD.filter())
async def cb_redeliver_order(callback: CallbackQuery, callback_data: RedeliverOrderCD, bot: Bot, dispatcher: Dispatcher | None = None):
    await _ack(callback, "Processing...")
    original = await get_order(callback_data.order_id)
    if not original or original.user_id != callback.from_user.id:
        await _ack(callback, "Order not found.", show_alert=True)
        return
    if original.status != OrderStatus.delivered:
        await _ack(callback, "Only delivered orders can be redelivered.", show_alert=True)
        return
    if not original.plan:
        await _ack(callback, "Original plan not found.", show_alert=True)
        return
    delivery_items = list(original.delivery_items or original.plan.delivery_items or [])
    if not delivery_items:
        await _ack(callback, "No delivery files found for this order.", show_alert=True)
        return

    bot_settings = await get_settings()
    await _create_and_start_payment(
        callback,
        bot,
        dispatcher,
        original.plan.id,
        amount=bot_settings.redelivery_price,
        plan_name=f"Redelivery #{original.order_id}",
        delivery_items=delivery_items,
        selected_range=original.selected_range,
        redelivery_of=original.order_id,
    )


@router.callback_query(DeliverNowCD.filter())
async def cb_deliver_now(callback: CallbackQuery, callback_data: DeliverNowCD, bot: Bot) -> None:
    await _ack(callback, "Checking order...")
    order = await get_order(callback_data.order_id)
    if not order or order.user_id != callback.from_user.id:
        await _ack(callback, "Order not found.", show_alert=True)
        return
    if order.status != OrderStatus.paid:
        await _ack(callback, "This order is not ready for delivery.", show_alert=True)
        return
    if not order.requirements_received:
        await _ack(callback, "Required details pending hain.", show_alert=True)
        return

    await _ack(callback, "Delivering files...", show_alert=False)
    delivered = await auto_deliver_order(bot, order.order_id, admin_id=0)
    await sync_order_feed(bot, order.order_id)
    if delivered:
        try:
            await callback.message.edit_text(
                f"Order <b>#{order.order_id}</b> delivery started. Files sent above.",
                reply_markup=main_menu_kb(),
            )
        except Exception:
            await callback.message.answer(
                f"Order <b>#{order.order_id}</b> delivery started. Files sent above.",
                reply_markup=main_menu_kb(),
            )
        return

    from handlers.admin.payments import _notify_order_admins

    await callback.message.answer("Auto-delivery file is not configured correctly. Admin has been notified.")
    await _notify_order_admins(bot, order)


@router.callback_query(OrderConfirmCD.filter())
async def cb_confirm_order(
    callback: CallbackQuery,
    callback_data: OrderConfirmCD,
    state: FSMContext,
    bot: Bot,
    dispatcher: Dispatcher | None = None,
) -> None:
    await _ack(callback, "Processing...")
    plan_id = callback_data.plan_id
    user_id = callback.from_user.id

    try:
        pending = await count_pending_orders(user_id)
        if pending >= settings.MAX_PENDING_ORDERS:
            await _ack(
                callback,
                f"⚠️ You already have {pending} pending orders.\n"
                "Please complete or wait for them to expire.",
                show_alert=True,
            )
            return

        bot_settings = await get_settings()
        upi_id = bot_settings.upi_id
        order = await create_order(user_id=user_id, plan_id=plan_id, upi_id=upi_id)
        await sync_order_feed(bot, order.order_id)
    except ValueError as e:
        await _ack(callback, str(e), show_alert=True)
        return
    except Exception as e:
        logger.error(f"Error creating order: {e}")
        await _ack(callback, "⚠️ Something went wrong. Please try again or contact support.", show_alert=True)
        return

    gateway = (getattr(bot_settings, "payment_gateway", settings.PAYMENT_GATEWAY) or "manual").lower()
    if gateway == "xwallet":
        await _handle_xwallet_payment(callback, bot, dispatcher, order)
    else:
        await _handle_manual_payment(callback, bot, order, bot_settings)
    try:
        await _ack(callback)
    except Exception:
        pass
    return


async def _handle_manual_payment(callback: CallbackQuery, bot: Bot, order: Order, bot_settings: BotSettings) -> None:
    """Manual UPI flow: QR generation + screenshot upload."""
    _ = bot
    upi_id = bot_settings.upi_id
    qr_bytes = generate_upi_qr(
        upi_id=upi_id,
        amount=order.amount,
        order_id=order.order_id,
        name=bot_settings.upi_name,
    )

    timeout = bot_settings.payment_timeout_minutes
    text = (
        f"💳 <b>Payment Instructions</b>\n\n"
        f"📦 Product:  <b>{order.product_name}</b>\n"
        f"📋 Plan:     <b>{order.plan_name}</b>\n"
        f"💰 Amount:   <b>₹{order.amount:.0f}</b>\n"
        f"🆔 Order ID: <b>#{order.order_id}</b>\n\n"
        f"<b>UPI ID:</b> <code>{upi_id}</code>\n\n"
        f"Scan the QR code or copy the UPI ID to pay.\n"
        f"After payment, click <b>I've sent payment</b>.\n\n"
        f"⏳ This order expires in <b>{timeout} minutes</b>."
    )
    kb = payment_sent_kb(order.order_id)

    await callback.message.delete()
    payment_message = await callback.message.answer_photo(
        photo=BufferedInputFile(qr_bytes.read(), filename="payment_qr.png"),
        caption=text,
        reply_markup=kb,
    )
    await add_order_flow_message(order.order_id, order.user_id, payment_message.message_id)
    await _ack(callback)


async def _handle_xwallet_payment(
    callback: CallbackQuery,
    bot: Bot,
    dispatcher: Dispatcher,
    order: Order,
) -> None:
    """XWallet flow: create payment, send payment-link CTA, and start polling."""
    await _ack(callback)
    loading = await callback.message.answer("⏳ Payment link generate ho raha hai...")

    try:
        payment = await create_payment(order.amount)
    except Exception as e:
        logger.error(f"Error creating XWallet payment for order {order.order_id}: {e}")
        try:
            await loading.delete()
        except Exception:
            pass
        await callback.message.answer("⚠️ Payment gateway error. Please try again.")
        await _ack(callback)
        return

    qr_code_id = str(payment.get("qr_code_id", ""))
    if not qr_code_id:
        try:
            await loading.delete()
        except Exception:
            pass
        await callback.message.answer("⚠️ Payment gateway error. Please try again.")
        await _ack(callback)
        return

    try:
        await loading.delete()
    except Exception:
        pass

    payment_link = str(payment.get("payment_link", ""))
    if not payment_link:
        await callback.message.answer("⚠️ Payment gateway error. Please try again.")
        await _ack(callback)
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder

    kb = InlineKeyboardBuilder()
    kb.button(
        text=f"💳 Pay ₹{order.amount:.0f} — Tap Here",
        url=payment_link,
    )
    kb.button(
        text="❌ Cancel Order",
        callback_data=CancelOrderCD(order_id=order.order_id).pack(),
    )
    kb.adjust(1)

    await callback.message.delete()
    payment_message = await callback.message.answer(
        "💳 <b>Payment</b>\n\n"
        f"📦 {order.product_name}\n"
        f"📋 {order.plan_name}\n"
        f"💰 Amount: <b>₹{order.amount:.0f}</b>\n"
        f"🆔 Order: <b>#{order.order_id}</b>\n\n"
        "📝 <b>Steps:</b>\n"
        "1. Neeche <b>Pay</b> button par tap karo.\n"
        "2. Browser me payment complete karo.\n"
        "3. Payment hone ke baad browser page <b>Refresh</b> karo.\n"
        "4. Bot me wapas aao, verification auto ho jayega.\n\n"
        "⏳ 5 minutes ke andar payment karo, warna order expire ho jayega.",
        reply_markup=kb.as_markup(),
    )

    asyncio.create_task(
        _poll_and_complete(
            bot,
            dispatcher,
            order,
            qr_code_id,
            payment_message_id=payment_message.message_id,
        )
    )
    await _ack(callback)


async def _poll_and_complete(
    bot: Bot,
    dispatcher: Dispatcher | None,
    order: Order,
    qr_code_id: str,
    payment_message_id: int | None = None,
) -> None:
    """Poll payment status and mark order paid/expired based on result."""
    try:
        success = await wait_for_payment(qr_code_id, timeout_minutes=5)
        latest_order = await get_order(order.order_id)
        if latest_order is None or latest_order.status.value != "pending":
            return

        if payment_message_id is not None:
            try:
                await bot.delete_message(chat_id=order.user_id, message_id=payment_message_id)
            except Exception:
                pass

        if success:
            await approve_payment(order.order_id, admin_id=0)
            await _handle_post_payment_confirmation(bot, order.order_id, dispatcher)
            return

        await update_order_status(order.order_id, OrderStatus.expired)
        await bot.send_message(
            chat_id=order.user_id,
            text=(
                "⏰ <b>Payment Expired</b>\n\n"
                f"Order <b>#{order.order_id}</b> ka time out ho gaya.\n"
                "Please naya order create karein."
            ),
        )
        await sync_order_feed(bot, order.order_id)
    except Exception as e:
        logger.error(f"Error while polling XWallet payment for order {order.order_id}: {e}")


@router.callback_query(UploadScreenshotCD.filter())
async def cb_upload_screenshot(callback: CallbackQuery, callback_data: UploadScreenshotCD, state: FSMContext) -> None:
    await _ack(callback, "Processing...")
    try:
        order_id = callback_data.order_id
        order = await get_order(order_id)
    except Exception as e:
        logger.error(f"Error getting order for screenshot upload: {e}")
        await _ack(callback, "⚠️ Something went wrong. Please try again or contact support.", show_alert=True)
        return

    if not order or order.status != OrderStatus.pending:
        await _ack(callback, "Order not found or already processed.", show_alert=True)
        return

    await state.set_state(PaymentStates.waiting_screenshot)
    await state.update_data(order_id=order_id)

    prompt = await callback.message.answer(
        f"📸 <b>Send Payment Screenshot</b>\n\n"
        f"Please send a screenshot of your payment for order <b>#{order_id}</b>.\n\n"
        f"Make sure the screenshot shows:\n"
        f"• Amount paid\n"
        f"• UPI transaction ID\n"
        f"• Date and time",
        reply_markup=cancel_screenshot_kb(order_id),
    )
    await add_order_flow_message(order_id, callback.from_user.id, prompt.message_id)
    await _ack(callback)


@router.message(PaymentStates.waiting_screenshot, F.photo)
async def handle_screenshot(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    order_id = data.get("order_id")

    if not order_id:
        await state.clear()
        return

    try:
        order = await get_order(order_id)
        if not order or order.status != OrderStatus.pending:
            await state.clear()
            await message.answer(
                "⚠️ This order is no longer valid.",
                reply_markup=main_menu_kb(),
            )
            return

        # Save screenshot file_id
        file_id = message.photo[-1].file_id
        await submit_screenshot(order_id, file_id)
        await add_order_flow_message(order_id, message.chat.id, message.message_id)
    except Exception as e:
        logger.error(f"Error submitting screenshot: {e}")
        await message.answer("⚠️ Something went wrong. Please try again or contact support.")
        return
    await state.clear()

    received_message = await message.answer(
        f"✅ <b>Screenshot received!</b>\n\n"
        f"Order: <b>#{order_id}</b>\n"
        f"Our team will verify your payment shortly.\n\n"
        f"You'll be notified once verified. Thank you! 🙏",
        reply_markup=main_menu_kb(),
    )
    await add_order_flow_message(order_id, message.chat.id, received_message.message_id)
    await sync_order_feed(bot, order_id)

    # Notify payment admins
    await _notify_payment_admins(bot, order, file_id)


async def _notify_payment_admins(bot: Bot, order: Order, file_id: str) -> None:
    """Forward screenshot + approve/reject buttons to all payment admins."""
    from keyboards.keyboards import payment_verify_kb

    try:
        admins = await get_admins_by_role(AdminRole.payment_admin, AdminRole.super_admin, AdminRole.owner)
    except Exception as e:
        logger.error(f"Error getting admins for notification: {e}")
        return

    username = order.user.username or order.user.full_name
    text = (
        f"💳 <b>Payment Verification</b>\n\n"
        f"👤 User:     @{username} (<code>{order.user_id}</code>)\n"
        f"🆔 Order:    <b>#{order.order_id}</b>\n"
        f"📦 Product:  {order.product_name}\n"
        f"📋 Plan:     {order.plan_name}\n"
        f"💰 Amount:   <b>₹{order.amount:.0f}</b>"
    )
    kb = payment_verify_kb(order.order_id)

    for admin in admins:
        try:
            sent = await bot.send_photo(
                chat_id=admin.id,
                photo=file_id,
                caption=text,
                reply_markup=kb,
            )
            await add_payment_admin_message(order.order_id, admin.id, sent.message_id, "photo")
        except Exception as e:
            logger.warning(f"Could not notify admin {admin.id}: {e}")


@router.message(PaymentStates.waiting_requirements, F.text)
async def handle_requirements_response(message: Message, state: FSMContext, bot: Bot) -> None:
    await _process_requirements_response(message, state, bot)


@router.message(PaymentStates.waiting_customer_bot_token, F.text)
async def handle_customer_bot_token(message: Message, state: FSMContext) -> None:
    await _process_customer_bot_token(message, state)


@router.callback_query(ConfirmCustomerBotCD.filter())
async def cb_confirm_customer_bot_started(callback: CallbackQuery, callback_data: ConfirmCustomerBotCD, bot: Bot) -> None:
    await _ack(callback, "Checking delivery...")
    order = await get_order(callback_data.order_id)
    if not order or order.status != OrderStatus.paid or not order.customer_bot_token:
        await _ack(callback, "Order is not ready for bot delivery.", show_alert=True)
        return

    delivered = await auto_deliver_order(bot, order.order_id, admin_id=0)
    await sync_order_feed(bot, order.order_id)
    if delivered:
        await callback.message.edit_text(
            f"✅ Order <b>#{order.order_id}</b> delivered from @{order.customer_bot_username}.",
            reply_markup=main_menu_kb(),
        )
        await _ack(callback, "Delivered.", show_alert=True)
        return

    await callback.message.answer(
        "Delivery failed. Please make sure you opened your bot and pressed /start, then press OK again."
    )
    await _ack(callback, "Start your bot first, then press OK again.", show_alert=True)


@router.message(PendingRequirementsFilter(), F.text)
async def handle_requirements_response_fallback(message: Message, state: FSMContext, bot: Bot) -> None:
    await _process_requirements_response(message, state, bot)


@router.callback_query(CancelOrderCD.filter())
async def cb_cancel_order(callback: CallbackQuery, callback_data: CancelOrderCD, state: FSMContext) -> None:
    await _ack(callback, "Cancelling...")
    try:
        order_id = callback_data.order_id
        order = await get_order(order_id)

        if order and order.status in (OrderStatus.pending, OrderStatus.submitted):
            await update_order_status(order_id, OrderStatus.cancelled)
            await sync_order_feed(callback.bot, order_id)
    except Exception as e:
        logger.error(f"Error cancelling order: {e}")
        await _ack(callback, "⚠️ Something went wrong. Please try again or contact support.", show_alert=True)
        return

    await state.clear()
    try:
        await callback.message.edit_text(
            "❌ Order cancelled.\n\nYou can create a new order anytime.",
            reply_markup=main_menu_kb(),
        )
    except Exception:
        await callback.message.delete()
        await callback.message.answer(
            "❌ Order cancelled.\n\nYou can create a new order anytime.",
            reply_markup=main_menu_kb(),
        )
    await _ack(callback)


@router.message(PaymentStates.waiting_screenshot)
async def handle_non_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    order_id = data.get("order_id", "")
    await message.answer(
        "⚠️ Please send a <b>photo/screenshot</b> of your payment.",
        reply_markup=cancel_screenshot_kb(order_id),
    )


@router.message(PaymentStates.waiting_requirements)
async def handle_non_text_requirements(message: Message) -> None:
    await message.answer(
        "⚠️ Please send the required order details in <b>text format</b>."
    )


@router.message(PaymentStates.waiting_customer_bot_token)
async def handle_non_text_customer_bot_token(message: Message) -> None:
    await message.answer("Please send your bot token as text.")


@router.message(PendingRequirementsFilter())
async def handle_pending_requirements_non_text_fallback(message: Message) -> None:
    await message.answer(
        "⚠️ Please send the required order details in <b>text format</b>."
    )
