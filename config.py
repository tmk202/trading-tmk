import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Binance
    BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
    BINANCE_SECRET_KEY: str = os.getenv("BINANCE_SECRET_KEY", "")
    BINANCE_TESTNET: bool = os.getenv("BINANCE_TESTNET", "true").lower() == "true"

    # Telegram
    TELEGRAM_ENABLED: bool = os.getenv("TELEGRAM_BOT_TOKEN", "") != ""
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Trading
    SYMBOL: str = os.getenv("SYMBOL", "BTC/USDT")
    TIMEFRAME: str = os.getenv("TIMEFRAME", "1h")
    QUOTE_SIZE: float = float(os.getenv("QUOTE_SIZE", "50"))
    MAX_POSITIONS: int = int(os.getenv("MAX_POSITIONS", "1"))
    RISK_PER_TRADE: float = float(os.getenv("RISK_PER_TRADE", "0.02"))
    STRATEGY: str = os.getenv("STRATEGY", "sma_crossover")

    # Strategy params
    SMA_FAST: int = int(os.getenv("SMA_FAST", "9"))
    SMA_SLOW: int = int(os.getenv("SMA_SLOW", "21"))

    # Scalper
    HIGHER_TF: str = os.getenv("HIGHER_TF", "15m")
    SCALPER_VOL_MULT: float = float(os.getenv("SCALPER_VOL_MULT", "1.5"))
    SCALPER_MAX_EMA_DIST_PCT: float = float(os.getenv("SCALPER_MAX_EMA_DIST_PCT", "0.8"))
    SCALPER_LOOKBACK_BARS: int = int(os.getenv("SCALPER_LOOKBACK_BARS", "12"))
    SCALPER_TP_RR: float = float(os.getenv("SCALPER_TP_RR", "1.5"))

    # OANDA
    OANDA_TOKEN: str = os.getenv("OANDA_TOKEN", "")
    OANDA_ACCOUNT_ID: str = os.getenv("OANDA_ACCOUNT_ID", "")
    OANDA_DEMO: bool = os.getenv("OANDA_DEMO", "true").lower() == "true"
    OANDA_SYMBOL: str = os.getenv("OANDA_SYMBOL", "XAU_USD")
    OANDA_TIMEFRAME: str = os.getenv("OANDA_TIMEFRAME", "15m")
    OANDA_QUOTE_SIZE: float = float(os.getenv("OANDA_QUOTE_SIZE", "100"))

    # IB
    IB_HOST: str = os.getenv("IB_HOST", "127.0.0.1")
    IB_PORT: int = int(os.getenv("IB_PORT", "7497"))
    IB_CLIENT_ID: int = int(os.getenv("IB_CLIENT_ID", "1"))
    IB_SYMBOL: str = os.getenv("IB_SYMBOL", "MGC")
    IB_TIMEFRAME: str = os.getenv("IB_TIMEFRAME", "1h")
    IB_QUOTE_SIZE: float = float(os.getenv("IB_QUOTE_SIZE", "500"))

    # Alpaca
    ALPACA_API_KEY: str = os.getenv("ALPACA_API_KEY", "")
    ALPACA_SECRET_KEY: str = os.getenv("ALPACA_SECRET_KEY", "")
    ALPACA_PAPER: bool = os.getenv("ALPACA_PAPER", "true").lower() == "true"
    ALPACA_SYMBOL: str = os.getenv("ALPACA_SYMBOL", "GLD")
    ALPACA_TIMEFRAME: str = os.getenv("ALPACA_TIMEFRAME", "1h")
    ALPACA_QUOTE_SIZE: float = float(os.getenv("ALPACA_QUOTE_SIZE", "500"))

    # Cooldown
    COOLDOWN_LOSSES: int = int(os.getenv("COOLDOWN_LOSSES", "2"))
    COOLDOWN_MINUTES: int = int(os.getenv("COOLDOWN_MINUTES", "30"))
    DAILY_LOSS_LIMIT: int = int(os.getenv("DAILY_LOSS_LIMIT", "3"))

    # Bot
    CHECK_INTERVAL_MINUTES: int = int(os.getenv("CHECK_INTERVAL_MINUTES", "15"))
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
