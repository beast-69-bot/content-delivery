"""
handlers/user/products.py
Category-first product browsing, product detail, plan selection, and search.
"""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from keyboards.keyboards import (
    BrowseCategoriesCD,
    BrowseCategoryCD,
    BrowseProductsCD,
    AcceptContentCD,
    PayPartialCD,
    ProductCD,
    PlansCD,
    SelectPlanCD,
    category_products_page_kb,
    categories_kb,
    main_menu_kb,
    order_confirm_kb,
    plans_kb,
    products_page_kb,
)
from keyboards.flow_keyboards import content_detail_kb, partial_confirm_kb, payment_options_kb
from services.db_service import (
    get_categories,
    get_plan,
    get_product,
    get_products_page,
    get_products_page_by_category,
    search_products,
)
from states.states import PaymentStates, ProductSearchStates

router = Router()
logger = logging.getLogger(__name__)


async def _ack(callback: CallbackQuery, *args, **kwargs) -> None:
    try:
        await callback.answer(*args, **kwargs)
    except Exception:
        pass


async def _edit_or_send(callback: CallbackQuery, text: str, reply_markup):
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except Exception:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(text, reply_markup=reply_markup)


@router.callback_query(BrowseCategoriesCD.filter())
async def cb_browse_categories(callback: CallbackQuery, callback_data: BrowseCategoriesCD, state: FSMContext):
    await _ack(callback)
    try:
        categories = await get_categories()
    except Exception as e:
        logger.error("DB Error in categories list: %s", e)
        await _ack(callback, "Something went wrong. Please try again or contact support.", show_alert=True)
        return

    await state.update_data(product_category=None, product_page=0)

    if not categories:
        await _ack(callback, "No categories available right now.", show_alert=True)
        return

    text = "<b>Content Categories</b>\n\nSelect a category to browse content:"
    await _edit_or_send(callback, text, categories_kb(categories))


@router.message(Command("categories"))
async def cmd_categories(message: Message, state: FSMContext):
    categories = await get_categories()
    await state.update_data(product_category=None, product_page=0)
    if not categories:
        await message.answer("No categories available right now.", reply_markup=main_menu_kb())
        return
    await message.answer(
        "<b>Content Categories</b>\n\nSelect a category to browse content:",
        reply_markup=categories_kb(categories),
    )


@router.callback_query(BrowseCategoryCD.filter())
async def cb_browse_category(callback: CallbackQuery, callback_data: BrowseCategoryCD, state: FSMContext):
    await _ack(callback)
    try:
        category = callback_data.category
        page = callback_data.page
        products, total = await get_products_page_by_category(category, page)
    except Exception as e:
        logger.error("DB Error in category products list: %s", e)
        await _ack(callback, "Something went wrong. Please try again or contact support.", show_alert=True)
        return

    if not products:
        await _ack(callback, "No content available in this category right now.", show_alert=True)
        return

    await state.update_data(product_category=category, product_page=page)
    text = f"<b>{category}</b>\n\nSelect content to view details:"
    kb = category_products_page_kb(category, products, page, total)

    await _edit_or_send(callback, text, kb)


@router.callback_query(BrowseProductsCD.filter())
async def cb_browse_products(callback: CallbackQuery, callback_data: BrowseProductsCD, state: FSMContext):
    await _ack(callback)
    try:
        page = callback_data.page
        products, total = await get_products_page(page)
    except Exception as e:
        logger.error("DB Error in products list: %s", e)
        await _ack(callback, "Something went wrong. Please try again or contact support.", show_alert=True)
        return

    if not products:
        await _ack(callback, "No content available right now.", show_alert=True)
        return

    await state.update_data(product_category=None, product_page=page)
    text = "<b>All Content</b>\n\nSelect content to view details:"
    kb = products_page_kb(products, page, total)

    await _edit_or_send(callback, text, kb)


@router.callback_query(ProductCD.filter())
async def cb_product_detail(callback: CallbackQuery, callback_data: ProductCD, state: FSMContext):
    await _ack(callback)
    try:
        product_id = callback_data.id
        product = await get_product(product_id)
    except Exception as e:
        logger.error("DB Error in product detail: %s", e)
        await _ack(callback, "Something went wrong. Please try again or contact support.", show_alert=True)
        return

    if not product:
        await _ack(callback, "Content not found.", show_alert=True)
        return

    full_plan = next((plan for plan in product.plans if plan.is_active), None)
    total_files = len(full_plan.delivery_items or []) if full_plan else 0
    full_price = full_plan.price if full_plan else 0
    notes = product.description or "Notes will be delivered in Telegram after payment."
    terms = product.tagline or product.requirements_text or (
        "- Files are for personal use only\n"
        "- Sharing/reselling is prohibited\n"
        "- No refund after payment\n"
        "- Contact admin for any issue"
    )

    text = (
        f"<b>{product.name}</b>\n\n"
        f"<b>Total Files:</b> {total_files}\n"
        f"<b>Full Price:</b> Rs {full_price:.0f}\n\n"
        f"<b>Notes:</b>\n{notes}\n\n"
        f"<b>Terms & Conditions:</b>\n{terms}\n\n"
        "Aage badhne ke liye Accept karein."
    )

    nav_data = await state.get_data()
    page = int(nav_data.get("product_page") or 0)
    category = nav_data.get("product_category") or product.category
    await state.update_data(product_page=page, product_category=category)
    kb = content_detail_kb(product_id, page=page, category=category, preview_url=product.preview_url)

    if product.image_file_id:
        try:
            await callback.message.delete()
            await callback.message.answer_photo(
                photo=product.image_file_id,
                caption=text,
                reply_markup=kb,
            )
        except Exception:
            await _edit_or_send(callback, text, kb)
    else:
        await _edit_or_send(callback, text, kb)



@router.callback_query(AcceptContentCD.filter())
async def cb_accept_content(callback: CallbackQuery, callback_data: AcceptContentCD):
    await _ack(callback)
    product = await get_product(callback_data.product_id)
    if not product:
        await _ack(callback, "Content not found.", show_alert=True)
        return
    plan = next((item for item in product.plans if item.is_active), None)
    if not plan:
        await _ack(callback, "No files available for this content.", show_alert=True)
        return
    total_files = len(plan.delivery_items or [])
    if total_files <= 0:
        await _ack(callback, "No files available for this content.", show_alert=True)
        return
    per_file = plan.price / total_files
    text = (
        f"<b>Payment Options - {product.name}</b>\n\n"
        f"Full Pay: Rs {plan.price:.0f} -> Saari {total_files} files milengi\n\n"
        "Partial Pay: Apni zaroorat ke hisaab se file range choose karein\n"
        f"- Price per file: Rs {plan.price:.0f} / {total_files} = ~Rs {per_file:.0f}/file\n"
        f"- Minimum range: 1 file\n"
        f"- Maximum range: {total_files} files\n\n"
        "Apna option chunein:"
    )
    await _edit_or_send(callback, text, payment_options_kb(plan.id, product.id))


@router.callback_query(PayPartialCD.filter())
async def cb_pay_partial(callback: CallbackQuery, callback_data: PayPartialCD, state: FSMContext):
    await _ack(callback)
    plan = await get_plan(callback_data.plan_id)
    if not plan:
        await _ack(callback, "Plan not found.", show_alert=True)
        return
    total_files = len(plan.delivery_items or [])
    if total_files <= 0:
        await _ack(callback, "No files available.", show_alert=True)
        return
    per_file = plan.price / total_files
    await state.set_state(PaymentStates.waiting_file_range)
    await state.update_data(partial_plan_id=plan.id)
    await _edit_or_send(
        callback,
        "<b>Kitni files chahiye aapko?</b>\n\n"
        "Please range enter karein.\n\n"
        "Format: <code>start-end</code>\n"
        "Example: <code>1-5</code>\n"
        "Example: <code>3-8</code>\n\n"
        f"Total {total_files} files available hain\n"
        f"Price per file: ~Rs {per_file:.0f}",
        None,
    )


@router.message(PaymentStates.waiting_file_range, F.text)
async def handle_partial_range(message: Message, state: FSMContext):
    data = await state.get_data()
    plan_id = data.get("partial_plan_id")
    plan = await get_plan(plan_id) if plan_id else None
    if not plan:
        await state.clear()
        await message.answer("Range session expired.", reply_markup=main_menu_kb())
        return

    try:
        start_raw, end_raw = message.text.strip().split("-", 1)
        start = int(start_raw.strip())
        end = int(end_raw.strip())
    except Exception:
        await message.answer("Invalid range. Format: <code>1-5</code>")
        return

    total_files = len(plan.delivery_items or [])
    if start < 1 or end < start or end > total_files:
        await message.answer(f"Invalid range. Please choose between 1 and {total_files}.")
        return

    selected_count = end - start + 1
    per_file = plan.price / total_files
    total_amount = round(per_file * selected_count)
    await state.clear()
    await message.answer(
        "<b>Your Order Summary</b>\n\n"
        f"Selected Range: File {start} to File {end}\n"
        f"Total Files: {selected_count}\n"
        f"Price per file: Rs {plan.price:.0f} / {total_files} = Rs {per_file:.2f}\n"
        f"Total Amount: <b>Rs {total_amount}</b>\n\n"
        "T&C same as above apply honge.\n\n"
        "Confirm karein:",
        reply_markup=partial_confirm_kb(plan.id, start, end),
    )


@router.callback_query(PlansCD.filter())
async def cb_plans(callback: CallbackQuery, callback_data: PlansCD):
    await _ack(callback)
    try:
        product_id = callback_data.product_id
        product = await get_product(product_id)
    except Exception as e:
        logger.error("DB Error in plans: %s", e)
        await _ack(callback, "Something went wrong. Please try again or contact support.", show_alert=True)
        return

    if not product or not product.plans:
        await _ack(callback, "No plans available.", show_alert=True)
        return

    active_plans = [p for p in product.plans if p.is_active]
    if not active_plans:
        await _ack(callback, "No active plans.", show_alert=True)
        return

    text = f"<b>{product.name}</b>\n\nSelect a plan:"
    kb = plans_kb(active_plans, product_id)

    await _edit_or_send(callback, text, kb)


@router.callback_query(SelectPlanCD.filter())
async def cb_select_plan(callback: CallbackQuery, callback_data: SelectPlanCD, state: FSMContext):
    await _ack(callback)
    try:
        plan_id = callback_data.plan_id
        plan = await get_plan(plan_id)
    except Exception as e:
        logger.error("DB Error in select_plan: %s", e)
        await _ack(callback, "Something went wrong. Please try again or contact support.", show_alert=True)
        return

    if not plan:
        await _ack(callback, "Plan not found.", show_alert=True)
        return

    text = (
        f"<b>Order Summary</b>\n\n"
        f"Content: <b>{plan.product.name}</b>\n"
        f"Plan: <b>{plan.name}</b>\n"
        f"Amount: <b>Rs {plan.price:.0f}</b>\n\n"
        f"Confirm your order?"
    )
    kb = order_confirm_kb(plan_id)

    await _edit_or_send(callback, text, kb)


@router.callback_query(F.data == "search_products")
async def cb_search_products(callback: CallbackQuery, state: FSMContext):
    await _ack(callback)
    await state.set_state(ProductSearchStates.waiting_query)
    await _edit_or_send(
        callback,
        "<b>Search Content</b>\n\nEnter the name or keywords of the content you're looking for:",
        main_menu_kb(),
    )


@router.message(ProductSearchStates.waiting_query, F.text)
async def handle_search_query(message: Message, state: FSMContext):
    query = message.text.strip()
    if len(query) < 2:
        await message.answer("Please enter at least 2 characters to search.")
        return

    try:
        products = await search_products(query)
    except Exception as e:
        logger.error("DB Error in search: %s", e)
        await message.answer("Something went wrong. Please try again or contact support.")
        return
    await state.clear()

    if not products:
        await message.answer(
            f"No content found for '<b>{query}</b>'.\n\nTry again or browse categories.",
            reply_markup=main_menu_kb(),
        )
        return

    text = f"<b>Search results for '{query}':</b>"
    kb = InlineKeyboardBuilder()
    for product in products[:10]:
        kb.button(text=f"{product.emoji or '*'} {product.name}", callback_data=ProductCD(id=product.id).pack())

    kb.button(text="Main Menu", callback_data="main_menu")
    kb.adjust(1)

    await message.answer(text, reply_markup=kb.as_markup())
