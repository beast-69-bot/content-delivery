"""
middlewares/role_filter.py
Reusable filters — use these in handlers to gate by role.

Usage:
    @router.message(Command("admin"), RoleFilter(AdminRole.owner, AdminRole.super_admin))
    async def admin_cmd(message: Message): ...
"""

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from database.models import AdminRole
from services.db_service import get_admin


class RoleFilter(BaseFilter):
    def __init__(self, *roles: AdminRole):
        self.roles = set(roles)

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user_id = event.from_user.id
        admin = await get_admin(user_id)
        if not admin:
            return False
        return admin.role in self.roles


class AnyAdminFilter(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        admin = await get_admin(event.from_user.id)
        return admin is not None


class OwnerFilter(RoleFilter):
    def __init__(self):
        super().__init__(AdminRole.owner)


class OwnerOrSuperFilter(RoleFilter):
    def __init__(self):
        super().__init__(AdminRole.owner, AdminRole.super_admin)


class ProductAdminFilter(RoleFilter):
    def __init__(self):
        super().__init__(AdminRole.owner, AdminRole.super_admin, AdminRole.product_admin)


class PaymentAdminFilter(RoleFilter):
    def __init__(self):
        super().__init__(AdminRole.owner, AdminRole.super_admin, AdminRole.payment_admin)


class OrderAdminFilter(RoleFilter):
    def __init__(self):
        super().__init__(AdminRole.owner, AdminRole.super_admin, AdminRole.order_admin)
