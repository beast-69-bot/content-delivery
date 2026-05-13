"""
database/connection.py
MongoDB client bootstrap and index management.
"""

from __future__ import annotations

import logging

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING

from config.settings import settings

logger = logging.getLogger(__name__)

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


def get_db() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("Database has not been initialized")
    return _db


async def init_db() -> None:
    global _client, _db
    if _client is not None and _db is not None:
        return

    _client = AsyncIOMotorClient(settings.MONGODB_URL)
    _db = _client[settings.MONGODB_DB_NAME]
    await _db.command("ping")

    await _ensure_indexes(_db)
    await _seed_defaults(_db)
    logger.info("MongoDB initialized")


async def close_db() -> None:
    global _client, _db
    if _client is not None:
        _client.close()
    _client = None
    _db = None
    logger.info("MongoDB connection closed")


async def _ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.users.create_index([("last_active", DESCENDING)])
    await db.admins.create_index([("is_active", ASCENDING), ("role", ASCENDING)])
    await db.products.create_index([("is_active", ASCENDING), ("sort_order", ASCENDING), ("_id", ASCENDING)])
    await db.products.create_index([("is_active", ASCENDING), ("category", ASCENDING), ("sort_order", ASCENDING), ("_id", ASCENDING)])
    await db.plans.create_index([("product_id", ASCENDING), ("is_active", ASCENDING), ("sort_order", ASCENDING), ("_id", ASCENDING)])
    await db.orders.create_index("order_id", unique=True)
    await db.orders.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    await db.orders.create_index([("status", ASCENDING), ("created_at", ASCENDING)])
    await db.orders.create_index([("status", ASCENDING), ("expires_at", ASCENDING)])
    await db.delivery_messages.create_index([("deleted", ASCENDING), ("delete_after", ASCENDING)])
    await db.delivery_messages.create_index([("order_id", ASCENDING)])
    await db.audit_logs.create_index([("admin_id", ASCENDING), ("created_at", DESCENDING)])


async def _seed_defaults(db: AsyncIOMotorDatabase) -> None:
    await db.bot_settings.update_one(
        {"_id": 1},
        {
            "$setOnInsert": {
                "upi_id": settings.UPI_ID,
                "upi_name": settings.UPI_NAME,
                "payment_timeout_minutes": settings.ORDER_EXPIRY_MINUTES,
                "payment_gateway": settings.PAYMENT_GATEWAY,
                "xwallet_api_key": settings.XWALLET_API_KEY,
                "delivery_delete_minutes": settings.DELIVERY_DELETE_MINUTES,
                "redelivery_price": settings.REDELIVERY_PRICE,
                "order_feed_chat_id": None,
                "total_earnings": 0.0,
                "welcome_message": "Welcome! Select a category and browse available content below.",
                "maintenance_mode": False,
            }
        },
        upsert=True,
    )

    await db.admins.update_one(
        {"_id": settings.OWNER_ID},
        {
            "$set": {
                "username": "owner",
                "role": "owner",
                "is_active": True,
                "added_by": None,
            },
            "$setOnInsert": {
                "added_at": None,
            },
        },
        upsert=True,
    )
