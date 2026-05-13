"""
config/settings.py
All configuration loaded from environment variables via pydantic-settings.
"""

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    BOT_TOKEN: str
    OWNER_ID: int

    MONGODB_URL: str = "mongodb://localhost:27017"
    MONGODB_DB_NAME: str = "my_selling_bot_vvip"

    UPI_ID: str = "store@upi"
    UPI_NAME: str = "My Store"
    STORE_NAME: str = "My Selling Bot VVIP"
    SUPPORT_USERNAME: str = "@support"
    XWALLET_API_KEY: str = ""
    XWALLET_BASE_URL: str = "https://xwalletbot.shop/wallet/getway"
    PAYMENT_GATEWAY: str = "manual"

    ORDER_EXPIRY_MINUTES: int = 10
    MAX_PENDING_ORDERS: int = 3
    DELIVERY_DELETE_MINUTES: int = 30
    REDELIVERY_PRICE: float = 5.0

    PRODUCTS_PER_PAGE: int = 5
    ORDERS_PER_PAGE: int = 8

    BROADCAST_DELAY: float = 0.05
    LOG_LEVEL: str = "INFO"

    @field_validator("BOT_TOKEN")
    def validate_bot_token(cls, v: str) -> str:
        if not (v and ":" in v and v.split(":")[0].isdigit()):
            raise ValueError("BOT_TOKEN must start with digits and contain ':'")
        return v

    @field_validator("OWNER_ID")
    def validate_owner_id(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("OWNER_ID must be positive integer > 0")
        return v

    @field_validator("ORDER_EXPIRY_MINUTES")
    def validate_expiry(cls, v: int) -> int:
        if not (1 <= v <= 60):
            raise ValueError("ORDER_EXPIRY_MINUTES must be between 1 and 60")
        return v

    @field_validator("MAX_PENDING_ORDERS")
    def validate_max_pending(cls, v: int) -> int:
        if not (1 <= v <= 10):
            raise ValueError("MAX_PENDING_ORDERS must be between 1 and 10")
        return v

    @field_validator("DELIVERY_DELETE_MINUTES")
    def validate_delivery_delete_minutes(cls, v: int) -> int:
        if not (1 <= v <= 1440):
            raise ValueError("DELIVERY_DELETE_MINUTES must be between 1 and 1440")
        return v

    @field_validator("REDELIVERY_PRICE")
    def validate_redelivery_price(cls, v: float) -> float:
        if v < 1:
            raise ValueError("REDELIVERY_PRICE must be at least 1")
        return v

    @field_validator("MONGODB_URL")
    def validate_mongodb_url(cls, v: str) -> str:
        if not (v.startswith("mongodb://") or v.startswith("mongodb+srv://")):
            raise ValueError("MONGODB_URL must start with 'mongodb://' or 'mongodb+srv://'")
        return v

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


try:
    settings = Settings()
except Exception as e:
    print(f"Environment Validation Error: {e}")
    import sys

    sys.exit(1)
