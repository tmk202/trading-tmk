import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "") or os.getenv("BINANCE_FUTURES_API_KEY", "")
    BINANCE_SECRET_KEY: str = os.getenv("BINANCE_SECRET_KEY", "") or os.getenv("BINANCE_FUTURES_SECRET_KEY", "")
    BINANCE_TESTNET: bool = os.getenv("BINANCE_TESTNET", "true").lower() == "true"

    TELEGRAM_ENABLED: bool = os.getenv("TELEGRAM_BOT_TOKEN", "") != ""
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
