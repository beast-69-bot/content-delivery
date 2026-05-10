"""
middlewares/auth.py
RBAC middleware — attaches admin role to every update.
Also auto-registers new users into the database.
"""

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

from database.models import AdminRole
from services.db_service import get_admin, upsert_user


# Roles that can access the admin panel (in addition to owner/super_admin)
ADMIN_ROLES = {AdminRole.owner, AdminRole.super_admin, AdminRole.product_admin,
               AdminRole.payment_admin, AdminRole.order_admin}


class AuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # Extract Telegram user from message or callback
        tg_user = None
        if isinstance(event, Message):
            tg_user = event.from_user
        elif isinstance(event, CallbackQuery):
            tg_user = event.from_user

        if tg_user:
            # Auto-upsert user record
            user = await upsert_user(
                user_id   = tg_user.id,
                username  = tg_user.username,
                full_name = tg_user.full_name,
            )

            if getattr(user, "is_banned", False):
                if isinstance(event, Message):
                    await event.answer("🚫 You are banned from using this bot.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("🚫 You are banned from using this bot.", show_alert=True)
                return

            # Attach admin object (or None) to handler data
            admin = await get_admin(tg_user.id)
            data["admin"]      = admin
            data["admin_role"] = admin.role if admin else None
            data["is_admin"]   = admin is not None


        return await handler(event, data)
