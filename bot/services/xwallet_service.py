"""
services/xwallet_service.py
Async wrapper around the XWallet payment gateway APIs.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from config.settings import settings

logger = logging.getLogger(__name__)


async def _resolve_api_key() -> str:
    """Resolve XWallet API key from DB settings with env fallback."""
    try:
        from services.db_service import get_settings

        db_settings = await get_settings()
        db_key = (getattr(db_settings, "xwallet_api_key", "") or "").strip()
        if db_key:
            return db_key
    except Exception as e:
        logger.warning(f"XWallet API key lookup from DB failed: {e}")

    return (settings.XWALLET_API_KEY or "").strip()


async def create_payment(amount: float) -> dict[str, Any]:
    """Create a payment request and return the full gateway response."""
    url = f"{settings.XWALLET_BASE_URL.rstrip('/')}/pay.php"
    api_key = await _resolve_api_key()
    if not api_key:
        raise ValueError("XWallet API key is not configured")

    params = {"key": api_key, "amount": f"{amount:.2f}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)
    except Exception as e:
        logger.warning(f"XWallet create_payment error: {e}")
        raise

    if str(data.get("status", "")).lower() != "pending":
        raise ValueError(f"Unexpected create_payment status: {data.get('status')}")

    return data


async def get_qr_data(qr_code_id: str) -> dict[str, Any]:
    """Fetch QR metadata for a previously created gateway code."""
    url = f"{settings.XWALLET_BASE_URL.rstrip('/')}/api_qr.php"
    params = {"code": qr_code_id}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)
    except Exception as e:
        logger.warning(f"XWallet get_qr_data error for {qr_code_id}: {e}")
        raise

    if str(data.get("status", "")).lower() != "success":
        raise ValueError(f"Unexpected get_qr_data status: {data.get('status')}")

    return data


async def check_status(qr_code_id: str) -> str:
    """Return the latest gateway status for the given QR code id."""
    url = f"{settings.XWALLET_BASE_URL.rstrip('/')}/check.php"
    params = {"code": qr_code_id}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)
    except Exception as e:
        logger.warning(f"XWallet check_status error for {qr_code_id}: {e}")
        return "pending"

    return str(data.get("status", "pending")).strip()


async def wait_for_payment(qr_code_id: str, timeout_minutes: int = 5) -> bool:
    """Poll gateway status every 5 seconds until success, failure, or timeout."""
    deadline = asyncio.get_running_loop().time() + (timeout_minutes * 60)
    while asyncio.get_running_loop().time() < deadline:
        status_raw = await check_status(qr_code_id)
        status = status_raw.strip().upper()
        logger.info(f"Polling {qr_code_id}: raw={status_raw}, normalized={status}")

        if status in {"TXN_SUCCESS", "SUCCESS", "PAID", "COMPLETED"}:
            return True
        if status in {"FAILED", "TXN_FAILED", "EXPIRED", "CANCELLED"}:
            return False

        await asyncio.sleep(5)
    return False
