import os
from dotenv import load_dotenv

load_dotenv()


_TESTNET_API_KEY = "dfUr2axHYpL2yNwrjZx3NIcOkPsp99xYL5GXomLfu3DwXXXWXEXPoVgUqSQLERp3"
_TESTNET_SECRET_KEY = "PtUopgf3uloMgv0AaksRgx50XJsF03R87nJs1kLxWkcFIMdqwMYmMy5RWuB05zHL"


class Config:
    BINANCE_API_KEY: str = (
        os.getenv("BINANCE_FUTURES_API_KEY")
        or os.getenv("BINANCE_API_KEY")
        or (_TESTNET_API_KEY if os.getenv("BINANCE_TESTNET", "true").lower() == "true" else "")
    )
    BINANCE_SECRET_KEY: str = (
        os.getenv("BINANCE_FUTURES_SECRET_KEY")
        or os.getenv("BINANCE_SECRET_KEY")
        or (_TESTNET_SECRET_KEY if os.getenv("BINANCE_TESTNET", "true").lower() == "true" else "")
    )
    BINANCE_TESTNET: bool = os.getenv("BINANCE_TESTNET", "true").lower() == "true"

    TELEGRAM_ENABLED: bool = os.getenv("TELEGRAM_BOT_TOKEN", "") != ""
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
