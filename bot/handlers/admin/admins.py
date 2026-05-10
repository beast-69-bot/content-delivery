"""
handlers/admin/admins.py
Owner/super_admin: add or remove admins, assign roles.
"""

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from keyboards.keyboards import (
    admin_roles_kb, back_to_admin_kb,
    AdminInfoCD, RemoveAdminCD, SetRoleCD
)
from middlewares.role_filter import OwnerOrSuperFilter
from services.db_service import add_admin, get_all_admins, log_action, remove_admin
from database.models import AdminRole
from states.states import AdminManagementStates

router = Router()


def _role_help() -> str:
    roles = [AdminRole.super_admin, AdminRole.product_admin, AdminRole.payment_admin, AdminRole.order_admin]
    return ", ".join(role.value for role in roles)


async def _start_add_admin(message: Message, state: FSMContext):
    await state.set_state(AdminManagementStates.waiting_user_id)
    await message.answer(
        "<b>Add Admin</b>\n\n"
        "Send the Telegram <b>User ID</b> of the person to add as admin.\n\n"
        "They must have started the bot first.\n"
        "Use @userinfobot to get someone's ID."
    )


@router.message(Command("admins"), OwnerOrSuperFilter())
async def cmd_admins_list(message: Message):
    admins = await get_all_admins()

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    for a in admins:
        uname = a.username or str(a.id)
        builder.button(
            text=f"{a.role.value} - @{uname}",
            callback_data=AdminInfoCD(user_id=a.id).pack(),
        )
    builder.button(text="Add Admin", callback_data="admin_add_admin")
    builder.button(text="Admin Panel", callback_data="admin:panel")
    builder.adjust(1)

    await message.answer(
        f"<b>Admin Management</b>\n\n{len(admins)} admins active:",
        reply_markup=builder.as_markup(),
    )


@router.message(Command("addadmin"), OwnerOrSuperFilter())
async def cmd_add_admin(message: Message, command: CommandObject, state: FSMContext):
    args = (command.args or "").split()
    if not args:
        await _start_add_admin(message, state)
        return

    if len(args) < 2:
        await message.answer(
            "Usage: <code>/addadmin user_id role</code>\n"
            f"Roles: <code>{_role_help()}</code>"
        )
        return

    try:
        user_id = int(args[0])
        role = AdminRole(args[1])
        if role == AdminRole.owner:
            raise ValueError
    except ValueError:
        await message.answer(
            "Invalid user ID or role.\n"
            "Usage: <code>/addadmin user_id role</code>\n"
            f"Roles: <code>{_role_help()}</code>"
        )
        return

    await add_admin(user_id, None, role, message.from_user.id)
    await log_action(message.from_user.id, "add_admin", str(user_id), role.value)
    await state.clear()
    await message.answer(
        f"Admin added.\n\nUser: <code>{user_id}</code>\nRole: <b>{role.value}</b>",
        reply_markup=back_to_admin_kb(),
    )


@router.callback_query(F.data == "admin:admins", OwnerOrSuperFilter())
async def cb_admins_list(callback: CallbackQuery):
    admins = await get_all_admins()

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    for a in admins:
        uname = a.username or str(a.id)
        builder.button(
            text=f"{a.role.value} — @{uname}",
            callback_data=AdminInfoCD(user_id=a.id).pack(),
        )
    builder.button(text="➕ Add Admin",  callback_data="admin_add_admin")
    builder.button(text="◀️ Admin Panel", callback_data="admin:panel")
    builder.adjust(1)

    await callback.message.edit_text(
        f"👥 <b>Admin Management</b>\n\n{len(admins)} admins active:",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(AdminInfoCD.filter(), OwnerOrSuperFilter())
async def cb_admin_info(callback: CallbackQuery, callback_data: AdminInfoCD):
    admin_user_id = callback_data.user_id

    from services.db_service import get_admin
    admin = await get_admin(admin_user_id)
    if not admin:
        await callback.answer("Admin not found.", show_alert=True)
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    if admin.role != AdminRole.owner:
        builder.button(text="🗑 Remove Admin", callback_data=RemoveAdminCD(user_id=admin_user_id).pack())
    builder.button(text="◀️ Back", callback_data="admin:admins")
    builder.adjust(1)

    await callback.message.edit_text(
        f"👤 <b>Admin Info</b>\n\n"
        f"ID: <code>{admin.id}</code>\n"
        f"Username: @{admin.username or 'N/A'}\n"
        f"Role: {admin.role.value}\n"
        f"Active: {'✅' if admin.is_active else '❌'}",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


# ── Add Admin ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_add_admin", OwnerOrSuperFilter())
async def cb_add_admin_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminManagementStates.waiting_user_id)
    await callback.message.answer(
        "👤 <b>Add Admin</b>\n\n"
        "Send the Telegram <b>User ID</b> of the person to add as admin.\n\n"
        "They must have started the bot first.\n"
        "Use @userinfobot to get someone's ID."
    )
    await callback.answer()


@router.message(AdminManagementStates.waiting_user_id, OwnerOrSuperFilter())
async def step_admin_user_id(message: Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())
        if user_id <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Invalid ID. Please send a positive numeric Telegram user ID.")
        return

    await state.update_data(new_admin_id=user_id)
    await state.set_state(AdminManagementStates.choosing_role)
    await message.answer("Select a <b>role</b> for this admin:", reply_markup=admin_roles_kb())


@router.callback_query(
    SetRoleCD.filter(), AdminManagementStates.choosing_role, OwnerOrSuperFilter()
)
async def step_choose_role(callback: CallbackQuery, callback_data: SetRoleCD, state: FSMContext):
    role_str = callback_data.role
    data = await state.get_data()
    new_admin_id = data["new_admin_id"]
    role = AdminRole(role_str)

    await add_admin(new_admin_id, None, role, callback.from_user.id)
    await log_action(callback.from_user.id, "add_admin", str(new_admin_id), role_str)
    await state.clear()

    await callback.message.edit_text(
        f"✅ Admin added!\n\nUser <code>{new_admin_id}</code> → Role: <b>{role_str}</b>",
        reply_markup=back_to_admin_kb(),
    )
    await callback.answer()


# ── Remove Admin ──────────────────────────────────────────────────────────────

@router.callback_query(RemoveAdminCD.filter(), OwnerOrSuperFilter())
async def cb_remove_admin(callback: CallbackQuery, callback_data: RemoveAdminCD):
    admin_user_id = callback_data.user_id
    success = await remove_admin(admin_user_id)
    await log_action(callback.from_user.id, "remove_admin", str(admin_user_id))

    if success:
        await callback.message.edit_text(
            f"🗑 Admin <code>{admin_user_id}</code> removed.",
            reply_markup=back_to_admin_kb(),
        )
    else:
        await callback.answer("Could not remove (owner cannot be removed).", show_alert=True)

    await callback.answer()
