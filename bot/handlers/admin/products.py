"""
handlers/admin/products.py
Product admin: add, edit, delete products and plans with auto-delivery files.
"""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from keyboards.keyboards import (
    AdminAddPlanCD,
    AdminDeleteProductCD,
    AdminEditProductCD,
    AdminProductCD,
    ConfirmDeleteProductCD,
    admin_product_actions_kb,
    admin_products_kb,
    back_to_admin_kb,
)
from middlewares.role_filter import ProductAdminFilter
from services.db_service import (
    add_plan,
    create_product,
    delete_product,
    get_all_products,
    get_product,
    log_action,
    update_product,
)
from states.states import AddPlanStates, AddProductStates, EditProductStates

logger = logging.getLogger(__name__)
router = Router()


def _delivery_item_from_message(message: Message) -> dict:
    return {
        "kind": "copy_message",
        "from_chat_id": message.chat.id,
        "message_id": message.message_id,
    }


def _edit_fields_kb(product_id: int):
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    builder = InlineKeyboardBuilder()
    builder.button(text="Edit Emoji", callback_data="admin_edit_field:emoji")
    builder.button(text="Edit Name", callback_data="admin_edit_field:name")
    builder.button(text="Edit Tagline", callback_data="admin_edit_field:tagline")
    builder.button(text="Edit Description", callback_data="admin_edit_field:description")
    builder.button(text="Edit Requirements", callback_data="admin_edit_field:requirements_text")
    builder.button(text="Edit Category", callback_data="admin_edit_field:category")
    builder.button(text="Toggle Active", callback_data="admin_edit_field:is_active")
    builder.button(text="Back", callback_data=AdminProductCD(id=product_id).pack())
    builder.adjust(2, 2, 2, 2)
    return builder.as_markup()


@router.callback_query(F.data == "admin:products", ProductAdminFilter())
async def cb_products_list(callback: CallbackQuery):
    products = await get_all_products()
    await callback.message.edit_text(
        f"<b>Content Management</b>\n\n{len(products)} content items total:",
        reply_markup=admin_products_kb(products),
    )
    await callback.answer()


@router.callback_query(AdminProductCD.filter(), ProductAdminFilter())
async def cb_product_actions(callback: CallbackQuery, callback_data: AdminProductCD):
    product = await get_product(callback_data.id)
    if not product:
        await callback.answer("Content not found.", show_alert=True)
        return

    plans_text = "\n".join(
        f"  - {plan.name} - Rs {plan.price:.0f} - {len(plan.delivery_items or [])} file(s)"
        for plan in product.plans if plan.is_active
    ) or "  No plans yet"

    text = (
        f"{product.emoji or '*'} <b>{product.name}</b>\n"
        f"Category: {product.category}\n"
        f"Status: {'Active' if product.is_active else 'Inactive'}\n\n"
        f"<b>Requirements:</b>\n{product.requirements_text or 'No extra requirements'}\n\n"
        f"<b>Plans:</b>\n{plans_text}"
    )
    await callback.message.edit_text(text, reply_markup=admin_product_actions_kb(product.id))
    await callback.answer()


@router.callback_query(AdminEditProductCD.filter(), ProductAdminFilter())
async def cb_edit_product_start(callback: CallbackQuery, callback_data: AdminEditProductCD, state: FSMContext):
    product = await get_product(callback_data.id)
    if not product:
        await callback.answer("Content not found.", show_alert=True)
        return

    await state.set_state(EditProductStates.choosing_field)
    await state.update_data(edit_product_id=product.id)
    await callback.message.edit_text(
        f"<b>Edit Content</b>\n\nContent: <b>{product.name}</b>\n\nChoose what to update:",
        reply_markup=_edit_fields_kb(product.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_edit_field:"), EditProductStates.choosing_field, ProductAdminFilter())
async def cb_choose_edit_field(callback: CallbackQuery, state: FSMContext):
    field = callback.data.split(":", 1)[1]
    data = await state.get_data()
    product_id = data.get("edit_product_id")
    product = await get_product(product_id) if product_id else None
    if not product_id or not product:
        await state.clear()
        await callback.answer("Edit session expired.", show_alert=True)
        return

    if field == "is_active":
        await update_product(product_id, is_active=not product.is_active)
        await log_action(callback.from_user.id, "edit_product", str(product_id), "toggle_active")
        await state.clear()
        await callback.message.edit_text(
            f"Content <b>{product.name}</b> status updated.",
            reply_markup=admin_product_actions_kb(product_id),
        )
        await callback.answer("Status updated.")
        return

    labels = {
        "emoji": "new emoji",
        "name": "new content name",
        "tagline": "new tagline",
        "description": "new description",
        "requirements_text": "new requirements text (/skip to clear)",
        "category": "new category",
    }
    if field not in labels:
        await callback.answer("Invalid edit field.", show_alert=True)
        return

    await state.set_state(EditProductStates.new_value)
    await state.update_data(edit_field=field)
    await callback.message.answer(f"Send the {labels[field]} for <b>{product.name}</b>:")
    await callback.answer()


@router.message(EditProductStates.new_value, ProductAdminFilter())
async def step_edit_product_value(message: Message, state: FSMContext):
    data = await state.get_data()
    product_id = data.get("edit_product_id")
    field = data.get("edit_field")
    if not product_id or not field:
        await state.clear()
        await message.answer("Edit session expired.", reply_markup=back_to_admin_kb())
        return

    value = message.text.strip()
    if field == "requirements_text" and message.text == "/skip":
        value = ""
    if not value and field != "requirements_text":
        await message.answer("Value cannot be empty. Please send a valid value.")
        return

    await update_product(product_id, **{field: value})
    await log_action(message.from_user.id, "edit_product", str(product_id), field)
    await state.clear()
    await message.answer("Content updated successfully.", reply_markup=admin_product_actions_kb(product_id))


@router.callback_query(F.data == "admin_add_product", ProductAdminFilter())
async def cb_add_product_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddProductStates.name)
    await callback.message.answer("<b>Add New Content</b>\n\nStep 1/7: Send the <b>content name</b>:")
    await callback.answer()


@router.message(AddProductStates.name, ProductAdminFilter())
async def step_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if not name or len(name) > 128:
        await message.answer("Content name must be between 1 and 128 characters. Please try again:")
        return
    await state.update_data(name=name)
    await state.set_state(AddProductStates.emoji)
    await message.answer("Step 2/7: Send a button emoji, or /skip.")


@router.message(AddProductStates.emoji, ProductAdminFilter())
async def step_emoji(message: Message, state: FSMContext):
    emoji = "*" if message.text == "/skip" else message.text.strip()
    if not emoji:
        await message.answer("Please send one emoji or /skip.")
        return

    await state.update_data(emoji=emoji[:8])
    await state.set_state(AddProductStates.image)
    await message.answer("Step 3/7: Send a preview image, or /skip.")


@router.message(AddProductStates.image, ProductAdminFilter())
async def step_image(message: Message, state: FSMContext):
    image_file_id = message.photo[-1].file_id if message.photo else None
    await state.update_data(image_file_id=image_file_id)
    await state.set_state(AddProductStates.tagline)
    await message.answer("Step 4/7: Send a short tagline, or /skip.")


@router.message(AddProductStates.tagline, ProductAdminFilter())
async def step_tagline(message: Message, state: FSMContext):
    await state.update_data(tagline="" if message.text == "/skip" else message.text.strip())
    await state.set_state(AddProductStates.description)
    await message.answer("Step 5/7: Send the content description:")


@router.message(AddProductStates.description, ProductAdminFilter())
async def step_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text.strip())
    await state.set_state(AddProductStates.requirements)
    await message.answer(
        "Step 6/7: Send required buyer info text, or /skip.\n"
        "For instant file delivery, usually send /skip."
    )


@router.message(AddProductStates.requirements, ProductAdminFilter())
async def step_requirements(message: Message, state: FSMContext):
    await state.update_data(requirements_text="" if message.text == "/skip" else message.text.strip())
    await state.set_state(AddProductStates.category)
    await message.answer("Step 7/7: Send the category, for example Courses, OTT, Software. Or /skip for General.")


@router.message(AddProductStates.category, ProductAdminFilter())
async def step_category(message: Message, state: FSMContext):
    category = "General" if message.text == "/skip" else message.text.strip()
    await state.update_data(category=category)
    await state.set_state(AddProductStates.plan_count)
    await message.answer("How many plans do you want to add? Enter 1 to 10:")


@router.message(AddProductStates.plan_count, ProductAdminFilter())
async def step_plan_count(message: Message, state: FSMContext):
    try:
        count = int(message.text.strip())
        assert 1 <= count <= 10
    except (ValueError, AssertionError):
        await message.answer("Please enter a valid number between 1 and 10.")
        return

    await state.update_data(plan_count=count, plans_added=0, plans=[])
    await state.set_state(AddProductStates.plan_name)
    await message.answer("Plan 1 - send the plan name:")


@router.message(AddProductStates.plan_name, ProductAdminFilter())
async def step_plan_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if not name or len(name) > 128:
        await message.answer("Plan name must be between 1 and 128 characters. Please try again:")
        return
    await state.update_data(current_plan_name=name)
    await state.set_state(AddProductStates.plan_price)
    await message.answer("Send the plan price, for example 109:")


@router.message(AddProductStates.plan_price, ProductAdminFilter())
async def step_plan_price(message: Message, state: FSMContext):
    try:
        price = float(message.text.strip())
        if not (0 < price <= 99999):
            raise ValueError
    except ValueError:
        await message.answer("Please enter a valid positive price up to Rs 99999.")
        return

    await state.update_data(current_plan_price=price, current_plan_files=[])
    await state.set_state(AddProductStates.plan_file_count)
    await message.answer("How many auto-delivery files/messages for this plan? Enter 1 to 20:")


@router.message(AddProductStates.plan_file_count, ProductAdminFilter())
async def step_plan_file_count(message: Message, state: FSMContext):
    try:
        count = int(message.text.strip())
        assert 1 <= count <= 20
    except (ValueError, AssertionError):
        await message.answer("Please enter a valid number between 1 and 20.")
        return

    await state.update_data(current_plan_file_count=count, current_plan_files=[])
    await state.set_state(AddProductStates.plan_file)
    await message.answer(
        "Send delivery file/message 1 now.\n"
        "Document, video, photo, audio, text, or any copyable Telegram message is supported."
    )


@router.message(AddProductStates.plan_file, ProductAdminFilter())
async def step_plan_file(message: Message, state: FSMContext):
    data = await state.get_data()
    files = list(data.get("current_plan_files") or [])
    files.append(_delivery_item_from_message(message))

    file_count = int(data.get("current_plan_file_count") or 1)
    if len(files) < file_count:
        await state.update_data(current_plan_files=files)
        await message.answer(f"Saved. Send delivery file/message {len(files) + 1}:")
        return

    plans = data.get("plans", [])
    plans.append({
        "name": data["current_plan_name"],
        "price": data["current_plan_price"],
        "delivery_items": files,
    })
    plans_added = data["plans_added"] + 1
    await state.update_data(plans=plans, plans_added=plans_added, current_plan_files=[])

    if plans_added < data["plan_count"]:
        await state.set_state(AddProductStates.plan_name)
        await message.answer(f"Plan {plans_added + 1} - send the plan name:")
    else:
        await _save_product(message, state)


async def _save_product(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()

    product = await create_product(
        name=data["name"],
        emoji=data.get("emoji", "*"),
        tagline=data.get("tagline", ""),
        description=data.get("description", ""),
        requirements_text=data.get("requirements_text") or None,
        image_file_id=data.get("image_file_id"),
        category=data.get("category", "General"),
        created_by=message.from_user.id,
    )

    for plan_data in data.get("plans", []):
        await add_plan(
            product.id,
            plan_data["name"],
            plan_data["price"],
            plan_data.get("delivery_items") or [],
        )

    await log_action(message.from_user.id, "add_product", str(product.id), product.name)
    await message.answer(
        f"<b>Content Added!</b>\n\n"
        f"{product.emoji} <b>{product.name}</b> with {len(data['plans'])} plan(s) and auto-delivery files.",
        reply_markup=back_to_admin_kb(),
    )


@router.callback_query(AdminAddPlanCD.filter(), ProductAdminFilter())
async def cb_add_plan_start(callback: CallbackQuery, callback_data: AdminAddPlanCD, state: FSMContext):
    await state.set_state(AddPlanStates.plan_name)
    await state.update_data(product_id=callback_data.product_id)
    await callback.message.answer("Send the new plan name:")
    await callback.answer()


@router.message(AddPlanStates.plan_name, ProductAdminFilter())
async def add_plan_name(message: Message, state: FSMContext):
    await state.update_data(plan_name=message.text.strip())
    await state.set_state(AddPlanStates.plan_price)
    await message.answer("Send the plan price, for example 109:")


@router.message(AddPlanStates.plan_price, ProductAdminFilter())
async def add_plan_price(message: Message, state: FSMContext):
    try:
        price = float(message.text.strip())
    except ValueError:
        await message.answer("Invalid price. Please enter a number.")
        return

    await state.update_data(plan_price=price, plan_files=[])
    await state.set_state(AddPlanStates.plan_file_count)
    await message.answer("How many auto-delivery files/messages for this plan? Enter 1 to 20:")


@router.message(AddPlanStates.plan_file_count, ProductAdminFilter())
async def add_plan_file_count(message: Message, state: FSMContext):
    try:
        count = int(message.text.strip())
        assert 1 <= count <= 20
    except (ValueError, AssertionError):
        await message.answer("Please enter a valid number between 1 and 20.")
        return

    await state.update_data(plan_file_count=count, plan_files=[])
    await state.set_state(AddPlanStates.plan_file)
    await message.answer("Send delivery file/message 1 now:")


@router.message(AddPlanStates.plan_file, ProductAdminFilter())
async def add_plan_file(message: Message, state: FSMContext):
    data = await state.get_data()
    files = list(data.get("plan_files") or [])
    files.append(_delivery_item_from_message(message))

    file_count = int(data.get("plan_file_count") or 1)
    if len(files) < file_count:
        await state.update_data(plan_files=files)
        await message.answer(f"Saved. Send delivery file/message {len(files) + 1}:")
        return

    await add_plan(data["product_id"], data["plan_name"], data["plan_price"], files)
    await log_action(message.from_user.id, "add_plan", str(data["product_id"]))
    await state.clear()
    await message.answer(
        f"Plan <b>{data['plan_name']}</b> added with {len(files)} auto-delivery file(s).",
        reply_markup=back_to_admin_kb(),
    )


@router.callback_query(AdminDeleteProductCD.filter(), ProductAdminFilter())
async def cb_delete_product(callback: CallbackQuery, callback_data: AdminDeleteProductCD):
    product = await get_product(callback_data.id)
    if not product:
        await callback.answer("Content not found.", show_alert=True)
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder

    builder = InlineKeyboardBuilder()
    builder.button(text="Yes, Delete", callback_data=ConfirmDeleteProductCD(id=product.id).pack())
    builder.button(text="Cancel", callback_data=AdminProductCD(id=product.id).pack())
    builder.adjust(2)

    await callback.message.edit_text(
        f"Delete <b>{product.name}</b>?\n\nThis action cannot be undone.",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(ConfirmDeleteProductCD.filter(), ProductAdminFilter())
async def cb_confirm_delete(callback: CallbackQuery, callback_data: ConfirmDeleteProductCD):
    await delete_product(callback_data.id)
    await log_action(callback.from_user.id, "delete_product", str(callback_data.id))
    await callback.message.edit_text("Content deleted successfully.", reply_markup=back_to_admin_kb())
    await callback.answer()
