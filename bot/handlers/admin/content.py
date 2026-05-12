import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from keyboards.keyboards import back_to_admin_kb
from middlewares.role_filter import ProductAdminFilter
from services.db_service import add_plan, create_product, get_categories, log_action
from states.states import AddContentStates

logger = logging.getLogger(__name__)
router = Router()


def _delivery_item_from_message(message: Message) -> dict:
    return {
        "kind": "copy_message",
        "from_chat_id": message.chat.id,
        "message_id": message.message_id,
    }


@router.message(Command("addcontent"), ProductAdminFilter())
async def cmd_add_content(message: Message, state: FSMContext):
    categories = await get_categories()
    category_text = "\n".join(f"- {category}" for category in categories) or "- No categories yet"
    await state.set_state(AddContentStates.category)
    await message.answer(
        "<b>Add Content</b>\n\n"
        "Existing categories:\n"
        f"{category_text}\n\n"
        "Send existing category name or send a new category name:"
    )


@router.message(AddContentStates.category, ProductAdminFilter())
async def add_content_category(message: Message, state: FSMContext):
    category = message.text.strip()
    if not category:
        await message.answer("Category cannot be empty.")
        return
    await state.update_data(category=category)
    await state.set_state(AddContentStates.subcategory)
    await message.answer("Send subcategory/chapter name:")


@router.message(AddContentStates.subcategory, ProductAdminFilter())
async def add_content_subcategory(message: Message, state: FSMContext):
    name = message.text.strip()
    if not name:
        await message.answer("Subcategory name cannot be empty.")
        return
    await state.update_data(name=name)
    await state.set_state(AddContentStates.full_price)
    await message.answer("Send full price, for example 199:")


@router.message(AddContentStates.full_price, ProductAdminFilter())
async def add_content_price(message: Message, state: FSMContext):
    try:
        price = float(message.text.strip())
        if price <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Send a valid positive price.")
        return
    await state.update_data(price=price)
    await state.set_state(AddContentStates.notes)
    await message.answer("Send notes/description for this subcategory:")


@router.message(AddContentStates.notes, ProductAdminFilter())
async def add_content_notes(message: Message, state: FSMContext):
    await state.update_data(notes=message.text.strip())
    await state.set_state(AddContentStates.terms)
    await message.answer("Send Terms & Conditions text:")


@router.message(AddContentStates.terms, ProductAdminFilter())
async def add_content_terms(message: Message, state: FSMContext):
    await state.update_data(terms=message.text.strip())
    await state.set_state(AddContentStates.requirements)
    await message.answer(
        "Send post-payment buyer info prompt for this subcategory, or /skip.\n\n"
        "Use this for setbot/details flow. Example:\n"
        "Send your bot token, admin user ID, and setup notes in one message."
    )


@router.message(AddContentStates.requirements, ProductAdminFilter())
async def add_content_requirements(message: Message, state: FSMContext):
    requirements_text = "" if message.text == "/skip" else message.text.strip()
    await state.update_data(requirements_text=requirements_text, files=[])
    await state.set_state(AddContentStates.files)
    await message.answer(
        "Now send files/messages. You can upload them in bulk.\n"
        "Telegram will deliver bulk uploads as separate messages, and I will add all of them to this subcategory.\n"
        "When all files are uploaded, send /done."
    )


@router.message(AddContentStates.files, Command("done"), ProductAdminFilter())
async def add_content_done(message: Message, state: FSMContext):
    data = await state.get_data()
    files = list(data.get("files") or [])
    if not files:
        await message.answer("Upload at least one file before /done.")
        return

    product = await create_product(
        name=data["name"],
        emoji="*",
        tagline=data.get("terms", ""),
        description=data.get("notes", ""),
        requirements_text=data.get("requirements_text") or None,
        image_file_id=None,
        category=data["category"],
        created_by=message.from_user.id,
    )
    await add_plan(product.id, "Full Access", data["price"], files)
    await log_action(message.from_user.id, "add_content", str(product.id), product.name)
    await state.clear()

    await message.answer(
        "<b>Subcategory Added</b>\n\n"
        f"Category: <b>{data['category']}</b>\n"
        f"Subcategory: <b>{data['name']}</b>\n"
        f"Total Files: <b>{len(files)}</b>\n"
        f"Full Price: <b>Rs {data['price']:.0f}</b>\n\n"
        "Users can now buy full access or select a partial file range.",
        reply_markup=back_to_admin_kb(),
    )


@router.message(AddContentStates.files, ProductAdminFilter())
async def add_content_file(message: Message, state: FSMContext):
    data = await state.get_data()
    files = list(data.get("files") or [])
    files.append(_delivery_item_from_message(message))
    await state.update_data(files=files)
    if len(files) == 1 or len(files) % 10 == 0:
        await message.answer(f"Saved {len(files)} file/message(s). Send more files or /done.")
