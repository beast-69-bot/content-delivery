from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from keyboards.keyboards import (
    AcceptContentCD,
    BrowseCategoryCD,
    BrowseProductsCD,
    ConfirmPartialCD,
    PayFullCD,
    PayPartialCD,
    ProductCD,
)


def content_detail_kb(product_id: int, page: int, category: str | None = None, preview_url: str | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if preview_url:
        builder.button(text="Preview", url=preview_url)
    builder.button(text="Accept", callback_data=AcceptContentCD(product_id=product_id).pack())
    if category:
        builder.button(text="Back", callback_data=BrowseCategoryCD(category=category, page=page).pack())
    else:
        builder.button(text="Back", callback_data=BrowseProductsCD(page=page).pack())
    builder.button(text="Main Menu", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def payment_options_kb(plan_id: int, product_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Pay Full", callback_data=PayFullCD(plan_id=plan_id).pack())
    builder.button(text="Pay Partial", callback_data=PayPartialCD(plan_id=plan_id).pack())
    builder.button(text="Back", callback_data=ProductCD(id=product_id).pack())
    builder.adjust(1)
    return builder.as_markup()


def partial_confirm_kb(plan_id: int, start: int, end: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Pay", callback_data=ConfirmPartialCD(plan_id=plan_id, start=start, end=end).pack())
    builder.button(text="Cancel", callback_data="main_menu")
    builder.adjust(2)
    return builder.as_markup()
