import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from keyboards.keyboards import back_to_admin_kb
from middlewares.role_filter import ProductAdminFilter
from services.db_service import add_plan, create_product, get_categories, log_action
from states.states import AddContentStates

logger = logging.getLogger(__name__)
router = Router()


def _category_kb(categories: list[str]):
    builder = InlineKeyboardBuilder()
    for index, category in enumerate(categories):
        builder.button(text=category, callback_data=f"addcontent:category:{index}")
    builder.button(text="Add New Category", callback_data="addcontent:category:new")
    builder.adjust(1)
    return builder.as_markup()


async def _remember_messages(state: FSMContext, *message_ids: int) -> None:
    data = await state.get_data()
    known = list(data.get("cleanup_message_ids") or [])
    for message_id in message_ids:
        if message_id and message_id not in known:
            known.append(message_id)
    await state.update_data(cleanup_message_ids=known)


async def _answer_and_remember(message: Message, state: FSMContext, text: str, **kwargs) -> Message:
    sent = await message.answer(text, **kwargs)
    await _remember_messages(state, sent.message_id)
    return sent


async def _cleanup_add_content_messages(message: Message, message_ids: list[int]) -> None:
    for message_id in message_ids:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=message_id)
        except Exception:
            pass


def _delivery_item_from_message(message: Message) -> dict:
    item = {
        "kind": "copy_message",
        "from_chat_id": message.chat.id,
        "message_id": message.message_id,
    }
    if message.document:
        item.update({
            "send_as": "document",
            "file_id": message.document.file_id,
            "file_name": message.document.file_name,
            "caption": message.caption,
        })
    elif message.video:
        item.update({
            "send_as": "video",
            "file_id": message.video.file_id,
            "file_name": message.video.file_name,
            "caption": message.caption,
        })
    elif message.photo:
        item.update({"send_as": "photo", "file_id": message.photo[-1].file_id, "caption": message.caption})
    elif message.audio:
        item.update({"send_as": "audio", "file_id": message.audio.file_id, "caption": message.caption})
    elif message.voice:
        item.update({"send_as": "voice", "file_id": message.voice.file_id})
    elif message.text:
        item.update({"send_as": "text", "text": message.text})
    return item


@router.message(Command("addcontent"), ProductAdminFilter())
async def cmd_add_content(message: Message, state: FSMContext):
    categories = await get_categories()
    await state.set_state(AddContentStates.category)
    await state.update_data(category_options=categories, cleanup_message_ids=[message.message_id])
    sent = await message.answer(
        "<b>Add Content</b>\n\n"
        "Choose an existing category or add a new one:",
        reply_markup=_category_kb(categories),
    )
    await _remember_messages(state, sent.message_id)


@router.callback_query(F.data.startswith("addcontent:category:"), AddContentStates.category, ProductAdminFilter())
async def add_content_category_button(callback: CallbackQuery, state: FSMContext):
    choice = callback.data.rsplit(":", 1)[1]
    if choice == "new":
        sent = await callback.message.answer("Send new category name:")
        await _remember_messages(state, sent.message_id)
        await callback.answer()
        return

    data = await state.get_data()
    categories = list(data.get("category_options") or [])
    try:
        category = categories[int(choice)]
    except (ValueError, IndexError):
        await callback.answer("Category selection expired. Send /addcontent again.", show_alert=True)
        return

    await state.update_data(category=category)
    await state.set_state(AddContentStates.subcategory)
    sent = await callback.message.answer(f"Category selected: <b>{category}</b>\n\nSend subcategory/chapter name:")
    await _remember_messages(state, sent.message_id)
    await callback.answer()


@router.message(AddContentStates.category, ProductAdminFilter())
async def add_content_category(message: Message, state: FSMContext):
    await _remember_messages(state, message.message_id)
    category = message.text.strip()
    if not category:
        await _answer_and_remember(message, state, "Category cannot be empty.")
        return
    await state.update_data(category=category)
    await state.set_state(AddContentStates.subcategory)
    await _answer_and_remember(message, state, "Send subcategory/chapter name:")


@router.message(AddContentStates.subcategory, ProductAdminFilter())
async def add_content_subcategory(message: Message, state: FSMContext):
    await _remember_messages(state, message.message_id)
    name = message.text.strip()
    if not name:
        await _answer_and_remember(message, state, "Subcategory name cannot be empty.")
        return
    await state.update_data(name=name)
    await state.set_state(AddContentStates.full_price)
    await _answer_and_remember(message, state, "Send full price, for example 199:")


@router.message(AddContentStates.full_price, ProductAdminFilter())
async def add_content_price(message: Message, state: FSMContext):
    await _remember_messages(state, message.message_id)
    try:
        price = float(message.text.strip())
        if price <= 0:
            raise ValueError
    except ValueError:
        await _answer_and_remember(message, state, "Send a valid positive price.")
        return
    await state.update_data(price=price)
    await state.set_state(AddContentStates.notes)
    await _answer_and_remember(message, state, "Send notes/description for this subcategory:")


@router.message(AddContentStates.notes, ProductAdminFilter())
async def add_content_notes(message: Message, state: FSMContext):
    await _remember_messages(state, message.message_id)
    await state.update_data(notes=message.text.strip())
    await state.set_state(AddContentStates.terms)
    await _answer_and_remember(message, state, "Send Terms & Conditions text:")


@router.message(AddContentStates.terms, ProductAdminFilter())
async def add_content_terms(message: Message, state: FSMContext):
    await _remember_messages(state, message.message_id)
    await state.update_data(terms=message.text.strip())
    await state.set_state(AddContentStates.requirements)
    await _answer_and_remember(
        message,
        state,
        "Send /skip to continue.\n\n"
        "Setbot delivery is currently disabled; files will be delivered from this bot."
    )


@router.message(AddContentStates.requirements, ProductAdminFilter())
async def add_content_requirements(message: Message, state: FSMContext):
    await _remember_messages(state, message.message_id)
    text = (message.text or "").strip()
    requirements_text = "" if text.lower() in {"/skip", "/setbot"} else text
    await state.update_data(requirements_text=requirements_text, delivery_mode="main_bot", files=[])
    await state.set_state(AddContentStates.files)
    await _answer_and_remember(
        message,
        state,
        "Now send files/messages. You can upload them in bulk.\n"
        "Telegram will deliver bulk uploads as separate messages, and I will add all of them to this subcategory.\n"
        "When all files are uploaded, send /done."
    )


@router.message(AddContentStates.files, Command("done"), ProductAdminFilter())
async def add_content_done(message: Message, state: FSMContext):
    data = await state.get_data()
    await _remember_messages(state, message.message_id)
    files = list(data.get("files") or [])
    if not files:
        await _answer_and_remember(message, state, "Upload at least one file before /done.")
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
        delivery_mode=data.get("delivery_mode", "main_bot"),
    )
    await add_plan(product.id, "Full Access", data["price"], files)
    await log_action(message.from_user.id, "add_content", str(product.id), product.name)
    cleanup_message_ids = list((await state.get_data()).get("cleanup_message_ids") or [])
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
    await _cleanup_add_content_messages(message, cleanup_message_ids)


@router.message(AddContentStates.files, ProductAdminFilter())
async def add_content_file(message: Message, state: FSMContext):
    await _remember_messages(state, message.message_id)
    data = await state.get_data()
    files = list(data.get("files") or [])
    files.append(_delivery_item_from_message(message))
    await state.update_data(files=files)
    if len(files) == 1 or len(files) % 10 == 0:
        await _answer_and_remember(message, state, f"Saved {len(files)} file/message(s). Send more files or /done.")
