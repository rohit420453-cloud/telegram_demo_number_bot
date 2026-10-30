import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Settings:
    bot_token: str = os.environ["BOT_TOKEN"]
    famgateway_api_key: str = os.environ["FAMGATEWAY_API_KEY"]
    public_base_url: str = os.environ["PUBLIC_BASE_URL"].rstrip("/")
    telegram_webhook_secret: str = os.environ["TELEGRAM_WEBHOOK_SECRET"]
    database_url: str = os.environ["DATABASE_URL"]
    admin_ids: tuple = tuple(
        int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()
    )
    price_inr: int = int(os.getenv("DEMO_NUMBER_PRICE_INR", "29"))
    currency: str = "INR"

settings = Settings()
