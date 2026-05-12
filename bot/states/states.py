"""
states/states.py
All FSM state groups for Aiogram.
"""

from aiogram.fsm.state import State, StatesGroup


# ── User States ───────────────────────────────────────────────────────────────

class PaymentStates(StatesGroup):
    waiting_screenshot = State()
    waiting_requirements = State()
    waiting_customer_bot_token = State()
    waiting_file_range = State()


class ProductSearchStates(StatesGroup):
    waiting_query = State()


# ── Admin: Product Management ─────────────────────────────────────────────────

class AddProductStates(StatesGroup):
    name        = State()
    emoji       = State()
    image       = State()
    tagline     = State()
    description = State()
    requirements = State()
    category    = State()
    plan_count  = State()
    plan_name   = State()
    plan_price  = State()
    plan_file_count = State()
    plan_file = State()


class EditProductStates(StatesGroup):
    choosing_field = State()
    new_value      = State()


class AddPlanStates(StatesGroup):
    plan_name  = State()
    plan_price = State()
    plan_file_count = State()
    plan_file = State()


# ── Admin: Settings ───────────────────────────────────────────────────────────

class SettingsStates(StatesGroup):
    setting_upi = State()


# ── Admin: Admins Management ──────────────────────────────────────────────────

class AdminManagementStates(StatesGroup):
    waiting_user_id = State()
    choosing_role   = State()


class AddPremiumStates(StatesGroup):
    waiting_user_id = State()
    waiting_days = State()


# ── Admin: Broadcast ──────────────────────────────────────────────────────────

class BroadcastStates(StatesGroup):
    waiting_message = State()
    confirming      = State()


# ── Admin: Payment Rejection ──────────────────────────────────────────────────

class RejectPaymentStates(StatesGroup):
    waiting_reason = State()


# ── Admin: Order Delivery ─────────────────────────────────────────────────────

class DeliverOrderStates(StatesGroup):
    waiting_product = State()


class ContactUserStates(StatesGroup):
    waiting_message = State()


class AddContentStates(StatesGroup):
    category = State()
    subcategory = State()
    full_price = State()
    notes = State()
    terms = State()
    requirements = State()
    files = State()
