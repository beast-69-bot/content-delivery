"""
handlers/admin/panel.py
/admin command: show admin panel and stats.
"""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database.models import AdminRole
from keyboards.keyboards import ToggleBanCD, admin_panel_kb, admin_recent_users_kb, back_to_admin_kb
from middlewares.role_filter import AnyAdminFilter, OwnerOrSuperFilter
from services.db_service import get_admin, get_recent_users, get_revenue_stats, get_stats, toggle_user_ban

router = Router()


def _can_view_all_orders(role: AdminRole | None) -> bool:
    return role in (AdminRole.owner, AdminRole.super_admin)


@router.message(Command("admin"), AnyAdminFilter())
async def cmd_admin(message: Message):
    await _show_panel(message)


@router.callback_query(F.data == "admin:panel", AnyAdminFilter())
async def cb_admin_panel(callback: CallbackQuery):
    await _show_panel(callback)
    await callback.answer()


async def _show_panel(target: Message | CallbackQuery):
    admin = await get_admin(target.from_user.id)
    show_all_orders = _can_view_all_orders(admin.role if admin else None)
    show_revenue = _can_view_all_orders(admin.role if admin else None)

    text = "<b>Admin Panel</b>\n\nWhat would you like to manage?"
    kb = admin_panel_kb(show_all_orders=show_all_orders, show_revenue=show_revenue)
    if isinstance(target, Message):
        await target.answer(text, reply_markup=kb)
    else:
        await target.message.edit_text(text, reply_markup=kb)


async def _stats_text() -> str:
    stats = await get_stats()
    revenue = await get_revenue_stats()
    return (
        "<b>Overall Bot Stats</b>\n\n"
        f"Total Users: <b>{stats['total_users']}</b>\n"
        f"Active Users: <b>{stats['active_users']}</b>\n"
        f"Banned Users: <b>{stats['banned_users']}</b>\n"
        f"Premium Users: <b>{stats['premium_users']}</b>\n\n"
        f"Total Orders: <b>{stats['total_orders']}</b>\n"
        f"Paid Orders: <b>{stats['paid_orders']}</b>\n"
        f"Delivered: <b>{stats['delivered']}</b>\n"
        f"Pending Verify: <b>{stats['pending_verify']}</b>\n\n"
        f"Total Revenue: <b>Rs {stats['total_revenue']:.2f}</b>\n"
        f"Lifetime Earnings: <b>Rs {revenue['total_earnings']:.2f}</b>\n"
        f"Pending Paid Amount: <b>Rs {revenue['pending_paid_amount']:.2f}</b>\n"
        f"Average/Delivered: <b>Rs {revenue['avg_per_delivered']:.2f}</b>"
    )


@router.message(Command("stats"), AnyAdminFilter())
async def cmd_stats(message: Message):
    await message.answer(await _stats_text(), reply_markup=back_to_admin_kb())


@router.callback_query(F.data == "admin:stats", AnyAdminFilter())
async def cb_stats(callback: CallbackQuery):
    text = await _stats_text()
    await callback.message.edit_text(text, reply_markup=back_to_admin_kb())
    await callback.answer()


@router.callback_query(F.data == "admin:revenue", OwnerOrSuperFilter())
async def cb_revenue(callback: CallbackQuery):
    data = await get_revenue_stats()
    text = (
        "<b>Revenue Dashboard</b>\n\n"
        f"Lifetime Earnings: <b>Rs {data['total_earnings']:.2f}</b>\n"
        f"Delivered Orders: <b>{data['delivered_orders']}</b>\n"
        f"Pending Paid Amount: <b>Rs {data['pending_paid_amount']:.2f}</b>\n"
        f"Average Earning/Delivered: <b>Rs {data['avg_per_delivered']:.2f}</b>"
    )
    await callback.message.edit_text(text, reply_markup=back_to_admin_kb())
    await callback.answer()


@router.callback_query(F.data == "admin:ban", OwnerOrSuperFilter())
async def cb_admin_ban(callback: CallbackQuery):
    users = await get_recent_users(20)
    kb = admin_recent_users_kb(users)
    await callback.message.edit_text(
        "<b>Ban/Unban Users</b>\n\nSelect a user to toggle status (recent 20):",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(ToggleBanCD.filter(), OwnerOrSuperFilter())
async def cb_toggle_ban(callback: CallbackQuery, callback_data: ToggleBanCD):
    user_id = callback_data.user_id
    is_banned = await toggle_user_ban(user_id)
    status = "Banned" if is_banned else "Unbanned"
    await callback.answer(f"User {user_id} {status}.", show_alert=True)
    await cb_admin_ban(callback)
