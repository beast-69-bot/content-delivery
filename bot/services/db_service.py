"""
services/db_service.py
MongoDB-backed data access layer preserving the existing handler-facing API.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from pymongo import ReturnDocument

from config.settings import settings
from database.connection import get_db
from database.models import Admin, AdminRole, AuditLog, BotSettings, Order, OrderStatus, Plan, Product, User
from utils.order_id import generate_order_id

logger = logging.getLogger(__name__)


def _utc_now_naive() -> datetime:
    return datetime.utcnow()


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _as_admin_role(value: Any) -> AdminRole:
    return value if isinstance(value, AdminRole) else AdminRole(value or AdminRole.product_admin.value)


def _as_order_status(value: Any) -> OrderStatus:
    return value if isinstance(value, OrderStatus) else OrderStatus(value or OrderStatus.pending.value)


def _user_from_doc(doc: dict | None) -> Optional[User]:
    if not doc:
        return None
    return User(
        id=int(doc["_id"]),
        username=doc.get("username"),
        full_name=doc.get("full_name", ""),
        is_banned=bool(doc.get("is_banned", False)),
        is_premium=bool(doc.get("is_premium", False)),
        premium_until=doc.get("premium_until"),
        premium_added_by=doc.get("premium_added_by"),
        total_orders=int(doc.get("total_orders", 0)),
        total_spent=float(doc.get("total_spent", 0.0) or 0.0),
        created_at=doc.get("created_at"),
        last_active=doc.get("last_active"),
    )


def _admin_from_doc(doc: dict | None) -> Optional[Admin]:
    if not doc:
        return None
    return Admin(
        id=int(doc["_id"]),
        username=doc.get("username"),
        role=_as_admin_role(doc.get("role")),
        is_active=bool(doc.get("is_active", True)),
        added_by=doc.get("added_by"),
        added_at=doc.get("added_at"),
    )


def _product_from_doc(doc: dict | None) -> Optional[Product]:
    if not doc:
        return None
    return Product(
        id=int(doc["_id"]),
        name=doc.get("name", ""),
        emoji=doc.get("emoji") or "📦",
        image_file_id=doc.get("image_file_id"),
        tagline=doc.get("tagline"),
        description=doc.get("description"),
        requirements_text=doc.get("requirements_text"),
        delivery_mode=doc.get("delivery_mode", "main_bot"),
        category=doc.get("category", "General"),
        is_active=bool(doc.get("is_active", True)),
        sort_order=int(doc.get("sort_order", 0)),
        total_sales=int(doc.get("total_sales", 0)),
        created_by=doc.get("created_by"),
        created_at=doc.get("created_at"),
        updated_at=doc.get("updated_at"),
    )


def _plan_from_doc(doc: dict | None) -> Optional[Plan]:
    if not doc:
        return None
    return Plan(
        id=int(doc["_id"]),
        product_id=int(doc["product_id"]),
        name=doc.get("name", ""),
        price=float(doc.get("price", 0.0) or 0.0),
        delivery_items=list(doc.get("delivery_items") or []),
        is_active=bool(doc.get("is_active", True)),
        sort_order=int(doc.get("sort_order", 0)),
    )


def _order_from_doc(doc: dict | None) -> Optional[Order]:
    if not doc:
        return None
    return Order(
        id=int(doc["_id"]),
        order_id=doc.get("order_id", ""),
        user_id=int(doc["user_id"]),
        plan_id=int(doc["plan_id"]),
        product_name=doc.get("product_name", ""),
        plan_name=doc.get("plan_name", ""),
        amount=float(doc.get("amount", 0.0) or 0.0),
        upi_id=doc.get("upi_id", ""),
        delivery_items=list(doc.get("delivery_items") or []),
        selected_range=doc.get("selected_range"),
        status=_as_order_status(doc.get("status")),
        screenshot_file_id=doc.get("screenshot_file_id"),
        requirements_text_snapshot=doc.get("requirements_text_snapshot"),
        customer_requirements_response=doc.get("customer_requirements_response"),
        customer_bot_token=doc.get("customer_bot_token"),
        customer_bot_username=doc.get("customer_bot_username"),
        requirements_received=bool(doc.get("requirements_received", True)),
        channel_message_id=doc.get("channel_message_id"),
        verified_by=doc.get("verified_by"),
        delivered_by=doc.get("delivered_by"),
        reject_reason=doc.get("reject_reason"),
        expires_at=doc.get("expires_at"),
        paid_at=doc.get("paid_at"),
        delivered_at=doc.get("delivered_at"),
        created_at=doc.get("created_at"),
    )


def _settings_from_doc(doc: dict | None) -> BotSettings:
    payload = doc or {"_id": 1}
    return BotSettings(
        id=int(payload.get("_id", 1)),
        upi_id=payload.get("upi_id", settings.UPI_ID),
        upi_name=payload.get("upi_name", settings.UPI_NAME),
        payment_timeout_minutes=int(payload.get("payment_timeout_minutes", settings.ORDER_EXPIRY_MINUTES)),
        payment_gateway=payload.get("payment_gateway", settings.PAYMENT_GATEWAY),
        xwallet_api_key=payload.get("xwallet_api_key", settings.XWALLET_API_KEY),
        order_feed_chat_id=payload.get("order_feed_chat_id"),
        total_earnings=float(payload.get("total_earnings", 0.0) or 0.0),
        welcome_message=payload.get("welcome_message", "Welcome! Browse our products below."),
        maintenance_mode=bool(payload.get("maintenance_mode", False)),
        updated_at=payload.get("updated_at"),
    )


async def _next_sequence(name: str) -> int:
    db = get_db()
    doc = await db.counters.find_one_and_update(
        {"_id": name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(doc["seq"])


async def _generate_unique_order_id() -> str:
    db = get_db()
    for _ in range(20):
        candidate = generate_order_id()
        if not await db.orders.find_one({"order_id": candidate}, {"_id": 1}):
            return candidate
    raise RuntimeError("Could not generate a unique order ID")


async def _load_product_plans(product_id: int, active_only: bool = True) -> list[Plan]:
    db = get_db()
    query: dict[str, Any] = {"product_id": product_id}
    if active_only:
        query["is_active"] = True
    docs = await db.plans.find(query).sort([("sort_order", 1), ("_id", 1)]).to_list(length=None)
    return [_plan_from_doc(doc) for doc in docs if doc]


async def _attach_product_plans(product: Product | None, active_only: bool = True) -> Product | None:
    if not product:
        return None
    product.plans = await _load_product_plans(product.id, active_only=active_only)
    return product


async def _attach_plan_product(plan: Plan | None) -> Plan | None:
    if not plan:
        return None
    db = get_db()
    plan.product = _product_from_doc(await db.products.find_one({"_id": plan.product_id}))
    return plan


async def _hydrate_order(order: Order | None, include_user: bool = False, include_plan: bool = False, include_plan_product: bool = False) -> Order | None:
    if not order:
        return None
    db = get_db()
    if include_user:
        order.user = _user_from_doc(await db.users.find_one({"_id": order.user_id}))
    if include_plan:
        order.plan = _plan_from_doc(await db.plans.find_one({"_id": order.plan_id}))
        if include_plan_product and order.plan:
            await _attach_plan_product(order.plan)
    return order


async def upsert_user(user_id: int, username: Optional[str], full_name: str) -> User:
    db = get_db()
    now = _utc_now_naive()
    await db.users.update_one(
        {"_id": user_id},
        {
            "$set": {"username": username, "full_name": full_name, "last_active": now},
            "$setOnInsert": {
                "is_banned": False,
                "is_premium": False,
                "premium_until": None,
                "premium_added_by": None,
                "total_orders": 0,
                "total_spent": 0.0,
                "created_at": now,
            },
        },
        upsert=True,
    )
    await db.users.update_one(
        {"_id": user_id, "is_premium": True, "premium_until": {"$ne": None, "$lt": now}},
        {"$set": {"is_premium": False}},
    )
    user = await get_user(user_id)
    if not user:
        raise RuntimeError(f"Failed to upsert user {user_id}")
    return user


async def get_user(user_id: int) -> Optional[User]:
    return _user_from_doc(await get_db().users.find_one({"_id": user_id}))


async def get_all_users() -> list[User]:
    docs = await get_db().users.find({"is_banned": False}).sort([("created_at", 1), ("_id", 1)]).to_list(length=None)
    return [_user_from_doc(doc) for doc in docs if doc]


async def get_recent_users(limit: int = 20) -> list[User]:
    docs = await get_db().users.find().sort([("last_active", -1), ("_id", -1)]).limit(limit).to_list(length=limit)
    return [_user_from_doc(doc) for doc in docs if doc]


async def toggle_user_ban(user_id: int) -> bool:
    user = await get_user(user_id)
    if not user:
        return False
    new_state = not user.is_banned
    await get_db().users.update_one({"_id": user_id}, {"$set": {"is_banned": new_state}})
    return new_state


async def add_premium_user(user_id: int, added_by: int, days: int | None = None) -> User:
    now = _utc_now_naive()
    premium_until = now + timedelta(days=days) if days else None
    await get_db().users.update_one(
        {"_id": user_id},
        {
            "$set": {
                "is_premium": True,
                "premium_until": premium_until,
                "premium_added_by": added_by,
                "last_active": now,
            },
            "$setOnInsert": {
                "username": None,
                "full_name": "",
                "is_banned": False,
                "total_orders": 0,
                "total_spent": 0.0,
                "created_at": now,
            },
        },
        upsert=True,
    )
    user = await get_user(user_id)
    if not user:
        raise RuntimeError(f"Failed to add premium user {user_id}")
    return user


async def get_admin(user_id: int) -> Optional[Admin]:
    return _admin_from_doc(await get_db().admins.find_one({"_id": user_id, "is_active": True}))


async def get_all_admins() -> list[Admin]:
    docs = await get_db().admins.find({"is_active": True}).sort([("role", 1), ("_id", 1)]).to_list(length=None)
    return [_admin_from_doc(doc) for doc in docs if doc]


async def add_admin(user_id: int, username: Optional[str], role: AdminRole, added_by: int) -> Admin:
    now = _utc_now_naive()
    await get_db().admins.update_one(
        {"_id": user_id},
        {
            "$set": {"username": username, "role": _enum_value(role), "is_active": True, "added_by": added_by},
            "$setOnInsert": {"added_at": now},
        },
        upsert=True,
    )
    admin = await get_admin(user_id)
    if not admin:
        raise RuntimeError(f"Failed to add admin {user_id}")
    return admin


async def remove_admin(user_id: int) -> bool:
    admin = await get_admin(user_id)
    if not admin or admin.role == AdminRole.owner:
        return False
    result = await get_db().admins.update_one({"_id": user_id}, {"$set": {"is_active": False}})
    return result.modified_count > 0


async def get_admins_by_role(*roles: AdminRole) -> list[Admin]:
    docs = await get_db().admins.find({"role": {"$in": [_enum_value(role) for role in roles]}, "is_active": True}).to_list(length=None)
    return [_admin_from_doc(doc) for doc in docs if doc]


async def get_products_page(page: int = 0) -> tuple[list[Product], int]:
    db = get_db()
    limit = settings.PRODUCTS_PER_PAGE
    total = await db.products.count_documents({"is_active": True})
    docs = await db.products.find({"is_active": True}).sort([("sort_order", 1), ("_id", 1)]).skip(page * limit).limit(limit).to_list(length=limit)
    return [_product_from_doc(doc) for doc in docs if doc], total


async def get_categories() -> list[str]:
    categories = await get_db().products.distinct("category", {"is_active": True})
    clean = sorted({str(category or "General").strip() or "General" for category in categories}, key=str.casefold)
    return clean


async def get_products_page_by_category(category: str, page: int = 0) -> tuple[list[Product], int]:
    db = get_db()
    limit = settings.PRODUCTS_PER_PAGE
    normalized = (category or "General").strip() or "General"
    query = {"is_active": True, "category": normalized}
    total = await db.products.count_documents(query)
    docs = await (
        db.products.find(query)
        .sort([("sort_order", 1), ("_id", 1)])
        .skip(page * limit)
        .limit(limit)
        .to_list(length=limit)
    )
    return [_product_from_doc(doc) for doc in docs if doc], total


async def get_product(product_id: int) -> Optional[Product]:
    product = _product_from_doc(await get_db().products.find_one({"_id": product_id, "is_active": True}))
    return await _attach_product_plans(product)


async def get_all_products() -> list[Product]:
    docs = await get_db().products.find({"is_active": True}).sort([("sort_order", 1), ("_id", 1)]).to_list(length=None)
    products = [_product_from_doc(doc) for doc in docs if doc]
    for product in products:
        await _attach_product_plans(product)
    return products


async def search_products(query: str) -> list[Product]:
    regex = {"$regex": re.escape(query), "$options": "i"}
    docs = await get_db().products.find(
        {"is_active": True, "$or": [{"name": regex}, {"description": regex}, {"tagline": regex}]}
    ).sort([("sort_order", 1), ("_id", 1)]).to_list(length=None)
    return [_product_from_doc(doc) for doc in docs if doc]


async def create_product(
    name: str,
    emoji: str,
    tagline: str,
    description: str,
    requirements_text: Optional[str],
    image_file_id: Optional[str],
    category: str,
    created_by: int,
    delivery_mode: str = "main_bot",
) -> Product:
    db = get_db()
    now = _utc_now_naive()
    product_id = await _next_sequence("products")
    await db.products.insert_one(
        {
            "_id": product_id,
            "name": name,
            "emoji": emoji or "📦",
            "image_file_id": image_file_id,
            "tagline": tagline,
            "description": description,
            "requirements_text": requirements_text,
            "delivery_mode": delivery_mode,
            "category": category or "General",
            "is_active": True,
            "sort_order": 0,
            "total_sales": 0,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }
    )
    product = await get_product(product_id)
    if not product:
        raise RuntimeError(f"Failed to create product {product_id}")
    return product


async def update_product(product_id: int, **kwargs) -> bool:
    if not kwargs:
        return True
    kwargs["updated_at"] = _utc_now_naive()
    result = await get_db().products.update_one({"_id": product_id}, {"$set": kwargs})
    return result.matched_count > 0


async def delete_product(product_id: int) -> bool:
    result = await get_db().products.update_one({"_id": product_id}, {"$set": {"is_active": False, "updated_at": _utc_now_naive()}})
    return result.matched_count > 0


async def add_plan(product_id: int, name: str, price: float, delivery_items: list[dict] | None = None) -> Plan:
    plan_id = await _next_sequence("plans")
    await get_db().plans.insert_one(
        {
            "_id": plan_id,
            "product_id": product_id,
            "name": name,
            "price": float(price),
            "delivery_items": delivery_items or [],
            "is_active": True,
            "sort_order": 0,
        }
    )
    plan = await get_plan(plan_id)
    if not plan:
        raise RuntimeError(f"Failed to create plan {plan_id}")
    return plan


async def get_plan(plan_id: int) -> Optional[Plan]:
    plan = _plan_from_doc(await get_db().plans.find_one({"_id": plan_id, "is_active": True}))
    return await _attach_plan_product(plan)


async def count_pending_orders(user_id: int) -> int:
    return await get_db().orders.count_documents({"user_id": user_id, "status": OrderStatus.pending.value})


async def create_order(
    user_id: int,
    plan_id: int,
    upi_id: str,
    amount: float | None = None,
    plan_name: str | None = None,
    delivery_items: list[dict] | None = None,
    selected_range: str | None = None,
) -> Order:
    db = get_db()
    plan = await get_plan(plan_id)
    if not plan or not plan.product:
        raise ValueError("Plan not found")

    timeout = await get_setting("payment_timeout_minutes") or settings.ORDER_EXPIRY_MINUTES
    now = _utc_now_naive()
    order_pk = await _next_sequence("orders")
    order_id = await _generate_unique_order_id()
    requirements_text = (plan.product.requirements_text or "").strip()
    requires_setbot = False
    doc = {
        "_id": order_pk,
        "order_id": order_id,
        "user_id": user_id,
        "plan_id": plan_id,
        "product_name": plan.product.name,
        "plan_name": plan_name or plan.name,
        "amount": float(plan.price if amount is None else amount),
        "upi_id": upi_id,
        "delivery_items": delivery_items if delivery_items is not None else list(plan.delivery_items or []),
        "selected_range": selected_range,
        "status": OrderStatus.pending.value,
        "screenshot_file_id": None,
        "requirements_text_snapshot": requirements_text or None,
        "customer_requirements_response": None,
        "customer_bot_token": None,
        "customer_bot_username": None,
        "requirements_received": not (bool(requirements_text) or requires_setbot),
        "channel_message_id": None,
        "verified_by": None,
        "delivered_by": None,
        "reject_reason": None,
        "expires_at": now + timedelta(minutes=int(timeout)),
        "paid_at": None,
        "delivered_at": None,
        "created_at": now,
    }
    await db.orders.insert_one(doc)
    order = _order_from_doc(doc)
    if not order:
        raise RuntimeError(f"Failed to create order {order_pk}")
    return order


async def get_order(order_id: str) -> Optional[Order]:
    order = _order_from_doc(await get_db().orders.find_one({"order_id": order_id}))
    return await _hydrate_order(order, include_user=True, include_plan=True, include_plan_product=True)


async def get_order_by_id(pk: int) -> Optional[Order]:
    order = _order_from_doc(await get_db().orders.find_one({"_id": pk}))
    return await _hydrate_order(order, include_user=True)


async def get_orders_by_status(status: OrderStatus, page: int = 0) -> tuple[list[Order], int]:
    db = get_db()
    limit = settings.ORDERS_PER_PAGE
    query = {"status": _enum_value(status)}
    total = await db.orders.count_documents(query)
    docs = await db.orders.find(query).sort([("created_at", 1), ("_id", 1)]).skip(page * limit).limit(limit).to_list(length=limit)
    orders = [_order_from_doc(doc) for doc in docs if doc]
    for order in orders:
        await _hydrate_order(order, include_user=True)
    return orders, total


async def get_delivery_ready_orders(page: int = 0) -> tuple[list[Order], int]:
    db = get_db()
    limit = settings.ORDERS_PER_PAGE
    query = {"status": OrderStatus.paid.value, "requirements_received": True}
    total = await db.orders.count_documents(query)
    docs = await db.orders.find(query).sort([("created_at", 1), ("_id", 1)]).skip(page * limit).limit(limit).to_list(length=limit)
    orders = [_order_from_doc(doc) for doc in docs if doc]
    for order in orders:
        await _hydrate_order(order, include_user=True, include_plan=True)
    return orders, total


async def get_all_orders_page(page: int = 0) -> tuple[list[Order], int]:
    db = get_db()
    limit = settings.ORDERS_PER_PAGE
    total = await db.orders.count_documents({})
    docs = await db.orders.find().sort([("created_at", -1), ("_id", -1)]).skip(page * limit).limit(limit).to_list(length=limit)
    orders = [_order_from_doc(doc) for doc in docs if doc]
    for order in orders:
        await _hydrate_order(order, include_user=True, include_plan=True)
    return orders, total


async def update_order_status(order_id: str, status: OrderStatus, **kwargs) -> bool:
    payload = {"status": _enum_value(status), **{k: _enum_value(v) for k, v in kwargs.items()}}
    result = await get_db().orders.update_one({"order_id": order_id}, {"$set": payload})
    return result.matched_count > 0


async def submit_screenshot(order_id: str, file_id: str) -> bool:
    return await update_order_status(order_id, OrderStatus.submitted, screenshot_file_id=file_id)


async def approve_payment(order_id: str, admin_id: int) -> bool:
    return await update_order_status(order_id, OrderStatus.paid, verified_by=admin_id, paid_at=_utc_now_naive())


async def reject_payment(order_id: str, admin_id: int, reason: str) -> bool:
    return await update_order_status(order_id, OrderStatus.rejected, verified_by=admin_id, reject_reason=reason)


async def mark_delivered(order_id: str, admin_id: int) -> bool:
    order = await get_order(order_id)
    if not order or order.status != OrderStatus.paid or not order.plan:
        return False

    updated = await update_order_status(order_id, OrderStatus.delivered, delivered_by=admin_id, delivered_at=_utc_now_naive())
    if not updated:
        return False

    db = get_db()
    await db.products.update_one({"_id": order.plan.product_id}, {"$inc": {"total_sales": 1}})
    await db.users.update_one({"_id": order.user_id}, {"$inc": {"total_orders": 1, "total_spent": float(order.amount)}})
    await db.bot_settings.update_one({"_id": 1}, {"$inc": {"total_earnings": float(order.amount)}})
    return True


async def save_customer_requirements(order_id: str, response: str) -> bool:
    return await update_order_status(
        order_id,
        OrderStatus.paid,
        customer_requirements_response=response,
        requirements_received=True,
    )


async def save_customer_bot(order_id: str, token: str, username: str) -> bool:
    return await update_order_status(
        order_id,
        OrderStatus.paid,
        customer_bot_token=token,
        customer_bot_username=username,
        requirements_received=True,
    )


async def set_order_channel_message_id(order_id: str, message_id: int | None) -> bool:
    result = await get_db().orders.update_one({"order_id": order_id}, {"$set": {"channel_message_id": message_id}})
    return result.matched_count > 0


async def get_pending_requirements_order_for_user(user_id: int) -> Optional[Order]:
    docs = await get_db().orders.find(
        {"user_id": user_id, "status": OrderStatus.paid.value, "requirements_received": False}
    ).sort([("paid_at", -1), ("created_at", -1), ("_id", -1)]).limit(1).to_list(length=1)
    order = _order_from_doc(docs[0]) if docs else None
    return await _hydrate_order(order, include_user=True, include_plan=True)


async def expire_old_orders() -> list[Order]:
    db = get_db()
    docs = await db.orders.find({"status": OrderStatus.pending.value, "expires_at": {"$lte": _utc_now_naive()}}).to_list(length=None)
    orders = [_order_from_doc(doc) for doc in docs if doc]
    if orders:
        await db.orders.update_many(
            {"_id": {"$in": [order.id for order in orders]}},
            {"$set": {"status": OrderStatus.expired.value}},
        )
        for order in orders:
            order.status = OrderStatus.expired
            await _hydrate_order(order, include_user=True)
    return orders


async def get_user_orders(user_id: int) -> list[Order]:
    docs = await get_db().orders.find({"user_id": user_id}).sort([("created_at", -1), ("_id", -1)]).limit(10).to_list(length=10)
    return [_order_from_doc(doc) for doc in docs if doc]


async def get_settings() -> BotSettings:
    return _settings_from_doc(await get_db().bot_settings.find_one({"_id": 1}))


async def get_setting(key: str):
    settings_row = await get_settings()
    return getattr(settings_row, key, None)


async def update_setting(key: str, value) -> bool:
    result = await get_db().bot_settings.update_one(
        {"_id": 1},
        {"$set": {key: value, "updated_at": _utc_now_naive()}},
        upsert=True,
    )
    return result.matched_count > 0 or result.upserted_id is not None


async def log_action(admin_id: int, action: str, target_id: str = None, details: str = None):
    log_id = await _next_sequence("audit_logs")
    doc = {
        "_id": log_id,
        "admin_id": admin_id,
        "action": action,
        "target_id": str(target_id) if target_id else None,
        "details": details,
        "created_at": _utc_now_naive(),
    }
    await get_db().audit_logs.insert_one(doc)
    return AuditLog(
        id=log_id,
        admin_id=admin_id,
        action=action,
        target_id=doc["target_id"],
        details=details,
        created_at=doc["created_at"],
    )


async def get_stats() -> dict:
    db = get_db()
    total_users = await db.users.count_documents({})
    active_users = await db.users.count_documents({"is_banned": False})
    banned_users = await db.users.count_documents({"is_banned": True})
    premium_users = await db.users.count_documents(
        {
            "is_premium": True,
            "$or": [{"premium_until": None}, {"premium_until": {"$gt": _utc_now_naive()}}],
        }
    )
    total_orders = await db.orders.count_documents({})
    paid_orders = await db.orders.count_documents({"status": OrderStatus.paid.value})
    delivered = await db.orders.count_documents({"status": OrderStatus.delivered.value})
    pending_verify = await db.orders.count_documents({"status": OrderStatus.submitted.value})
    revenue_docs = await db.orders.aggregate(
        [
            {"$match": {"status": {"$in": [OrderStatus.paid.value, OrderStatus.delivered.value]}}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ]
    ).to_list(length=1)
    return {
        "total_users": total_users,
        "active_users": active_users,
        "banned_users": banned_users,
        "premium_users": premium_users,
        "total_orders": total_orders,
        "paid_orders": paid_orders,
        "delivered": delivered,
        "total_revenue": float(revenue_docs[0]["total"]) if revenue_docs else 0.0,
        "pending_verify": pending_verify,
    }


async def get_revenue_stats() -> dict:
    db = get_db()
    bot_settings = await get_settings()
    delivered_orders = await db.orders.count_documents({"status": OrderStatus.delivered.value})
    pending_docs = await db.orders.aggregate(
        [
            {"$match": {"status": OrderStatus.paid.value}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ]
    ).to_list(length=1)
    total_earnings = float(bot_settings.total_earnings or 0.0)
    avg_per_delivered = (total_earnings / delivered_orders) if delivered_orders else 0.0
    return {
        "total_earnings": total_earnings,
        "delivered_orders": delivered_orders,
        "pending_paid_amount": float(pending_docs[0]["total"]) if pending_docs else 0.0,
        "avg_per_delivered": float(avg_per_delivered),
    }
