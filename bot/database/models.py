"""
database/models.py
Dataclass-based domain models used by the MongoDB service layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional


class Base:
    metadata = None


class AdminRole(str, PyEnum):
    owner = "owner"
    super_admin = "super_admin"
    product_admin = "product_admin"
    payment_admin = "payment_admin"
    order_admin = "order_admin"


class OrderStatus(str, PyEnum):
    pending = "pending"
    submitted = "submitted"
    paid = "paid"
    delivered = "delivered"
    rejected = "rejected"
    expired = "expired"
    cancelled = "cancelled"


@dataclass
class User:
    id: int
    username: Optional[str] = None
    full_name: str = ""
    is_banned: bool = False
    is_premium: bool = False
    premium_until: Optional[datetime] = None
    premium_added_by: Optional[int] = None
    total_orders: int = 0
    total_spent: float = 0.0
    created_at: Optional[datetime] = None
    last_active: Optional[datetime] = None


@dataclass
class Admin:
    id: int
    username: Optional[str] = None
    role: AdminRole = AdminRole.product_admin
    is_active: bool = True
    added_by: Optional[int] = None
    added_at: Optional[datetime] = None


@dataclass
class Product:
    id: int
    name: str
    emoji: str = "📦"
    image_file_id: Optional[str] = None
    tagline: Optional[str] = None
    description: Optional[str] = None
    requirements_text: Optional[str] = None
    category: str = "General"
    is_active: bool = True
    sort_order: int = 0
    total_sales: int = 0
    created_by: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    plans: list["Plan"] = field(default_factory=list)


@dataclass
class Plan:
    id: int
    product_id: int
    name: str
    price: float
    delivery_items: list[dict] = field(default_factory=list)
    is_active: bool = True
    sort_order: int = 0
    product: Optional[Product] = None


@dataclass
class Order:
    id: int
    order_id: str
    user_id: int
    plan_id: int
    product_name: str
    plan_name: str
    amount: float
    upi_id: str
    delivery_items: list[dict] = field(default_factory=list)
    selected_range: Optional[str] = None
    status: OrderStatus = OrderStatus.pending
    screenshot_file_id: Optional[str] = None
    requirements_text_snapshot: Optional[str] = None
    customer_requirements_response: Optional[str] = None
    requirements_received: bool = True
    channel_message_id: Optional[int] = None
    verified_by: Optional[int] = None
    delivered_by: Optional[int] = None
    reject_reason: Optional[str] = None
    expires_at: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    user: Optional[User] = None
    plan: Optional[Plan] = None


@dataclass
class BotSettings:
    id: int = 1
    upi_id: str = "store@upi"
    upi_name: str = "My Store"
    payment_timeout_minutes: int = 10
    payment_gateway: str = "manual"
    xwallet_api_key: str = ""
    order_feed_chat_id: Optional[str] = None
    total_earnings: float = 0.0
    welcome_message: str = "Welcome! Browse our products below."
    maintenance_mode: bool = False
    updated_at: Optional[datetime] = None


@dataclass
class AuditLog:
    id: int
    admin_id: int
    action: str
    target_id: Optional[str] = None
    details: Optional[str] = None
    created_at: Optional[datetime] = None
