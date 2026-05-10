"""
Reusable emoji captcha gate for protected user delivery.
"""

from __future__ import annotations

import random
import secrets
import time
from typing import Any

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

router = Router()

CAPTCHA_SESSION_SECONDS = 3 * 60 * 60
CAPTCHA_TTL_SECONDS = 5 * 60
CAPTCHA_MAX_ATTEMPTS = 5
CAPTCHA_BAN_SECONDS = 10 * 60

CAPTCHA_EMOJIS = [
    "🍎", "🚗", "🐟", "⭐", "🌟", "🔥", "💎", "🎯", "🍕", "🎮",
    "🏀", "🎸", "🌈", "🍔", "⚡", "🎭", "🌺", "🎨", "🏆", "🎪",
]

# In-memory storage. Swap with Redis/MongoDB if bot runs multiple workers.
captcha_users: dict[int, dict[str, Any]] = {}
captcha_sessions: dict[str, dict[str, Any]] = {}
captcha_pending_requests: dict[int, list[dict[str, Any]]] = {}


def now_ts() -> int:
    return int(time.time())


def is_user_verified(user_id: int) -> bool:
    user = captcha_users.get(user_id, {})
    return int(user.get("verified_until", 0)) > now_ts()


def get_ban_until(user_id: int) -> int:
    user = captcha_users.setdefault(user_id, {})
    banned_until = int(user.get("banned_until", 0) or 0)

    if banned_until and banned_until <= now_ts():
        user["banned_until"] = 0
        user["failed_attempts"] = 0
        return 0

    return banned_until


def mark_user_verified(user_id: int) -> int:
    verified_until = now_ts() + CAPTCHA_SESSION_SECONDS
    user = captcha_users.setdefault(user_id, {})
    user["verified_until"] = verified_until
    user["failed_attempts"] = 0
    user["banned_until"] = 0
    user["total_verifications"] = int(user.get("total_verifications", 0)) + 1
    return verified_until


def increment_failed_attempts(user_id: int) -> int:
    user = captcha_users.setdefault(user_id, {})
    user["failed_attempts"] = int(user.get("failed_attempts", 0)) + 1
    return user["failed_attempts"]


def ban_user(user_id: int) -> int:
    banned_until = now_ts() + CAPTCHA_BAN_SECONDS
    user = captcha_users.setdefault(user_id, {})
    user["banned_until"] = banned_until
    user["failed_attempts"] = 0
    return banned_until


def generate_captcha() -> dict[str, Any]:
    correct = random.choice(CAPTCHA_EMOJIS)
    wrong = random.sample([emoji for emoji in CAPTCHA_EMOJIS if emoji != correct], 3)
    options = wrong + [correct]
    random.shuffle(options)

    return {
        "captcha_id": secrets.token_urlsafe(6),
        "options": options,
        "correct_index": options.index(correct),
        "expires_at": now_ts() + CAPTCHA_TTL_SECONDS,
    }


def captcha_keyboard(captcha_id: str, options: list[str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=emoji, callback_data=f"cap:{captcha_id}:{index}")]
            for index, emoji in enumerate(options)
        ]
    )


def _active_captcha_for_user(user_id: int) -> dict[str, Any] | None:
    for session in captcha_sessions.values():
        if (
            session.get("user_id") == user_id
            and session.get("status") == "pending"
            and int(session.get("expires_at", 0)) > now_ts()
        ):
            return session
    return None


async def send_captcha(bot: Bot, user_id: int, attempts: int = 0) -> None:
    data = generate_captcha()
    captcha_sessions[data["captcha_id"]] = {
        "captcha_id": data["captcha_id"],
        "user_id": user_id,
        "options": data["options"],
        "correct_index": data["correct_index"],
        "expires_at": data["expires_at"],
        "status": "pending",
    }

    correct_emoji = data["options"][data["correct_index"]]
    attempt_line = f"\nWrong attempts: {attempts}/{CAPTCHA_MAX_ATTEMPTS}" if attempts else ""

    await bot.send_message(
        chat_id=user_id,
        text=(
            "🤖 <b>Verification Required</b>\n\n"
            f"Select this emoji: {correct_emoji}\n"
            f"Captcha expires in 5 minutes.{attempt_line}\n\n"
            "After verification, access stays active for 3 hours."
        ),
        reply_markup=captcha_keyboard(data["captcha_id"], data["options"]),
    )


async def require_captcha_or_continue(bot: Bot, user_id: int, pending_request: dict[str, Any]) -> bool:
    banned_until = get_ban_until(user_id)
    if banned_until > now_ts():
        await bot.send_message(chat_id=user_id, text="⛔ Too many wrong attempts. Try again later.")
        return False

    if is_user_verified(user_id):
        return True

    captcha_pending_requests.setdefault(user_id, []).append(pending_request)
    if not _active_captcha_for_user(user_id):
        await send_captcha(bot, user_id)
    return False


async def deliver_pending_request(bot: Bot, pending: dict[str, Any], user_id: int) -> None:
    intro_text = pending.get("intro_text")
    if intro_text:
        await bot.send_message(chat_id=user_id, text=intro_text)

    if pending.get("kind") == "copy_message":
        await bot.copy_message(
            chat_id=user_id,
            from_chat_id=int(pending["from_chat_id"]),
            message_id=int(pending["message_id"]),
        )


async def deliver_queued_requests(bot: Bot, user_id: int) -> int:
    pending_items = captcha_pending_requests.pop(user_id, [])
    delivered = 0
    for pending in pending_items:
        await deliver_pending_request(bot, pending, user_id)
        delivered += 1
    return delivered


@router.callback_query(F.data.startswith("cap:"))
async def captcha_callback(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()

    user_id = callback.from_user.id
    parts = (callback.data or "").split(":")

    if len(parts) != 3:
        await callback.answer("Invalid captcha.", show_alert=True)
        return

    _, captcha_id, selected_raw = parts

    try:
        selected_index = int(selected_raw)
    except ValueError:
        await callback.answer("Invalid captcha.", show_alert=True)
        return

    session = captcha_sessions.get(captcha_id)
    if not session:
        await callback.answer("Captcha expired. Try again.", show_alert=True)
        return

    if session["user_id"] != user_id:
        await callback.answer("This captcha is not for you.", show_alert=True)
        return

    if session["status"] != "pending":
        await callback.answer("This captcha is no longer active.", show_alert=True)
        return

    if session["expires_at"] <= now_ts():
        session["status"] = "expired"
        await callback.message.edit_text("⏱ Captcha expired. Request again.")
        return

    if selected_index == session["correct_index"]:
        session["status"] = "solved"
        mark_user_verified(user_id)

        await callback.message.edit_text("✅ Verified successfully. Sending your content...")
        await deliver_queued_requests(bot, user_id)
        return

    session["status"] = "failed"
    attempts = increment_failed_attempts(user_id)

    if attempts >= CAPTCHA_MAX_ATTEMPTS:
        ban_user(user_id)
        captcha_pending_requests.pop(user_id, None)
        await callback.message.edit_text("⛔ Too many wrong attempts. Banned for 10 minutes.")
        return

    await callback.message.edit_text(
        f"❌ Wrong emoji.\nAttempts: {attempts}/{CAPTCHA_MAX_ATTEMPTS}\n\nNew captcha sent."
    )
    await send_captcha(bot, user_id, attempts=attempts)
