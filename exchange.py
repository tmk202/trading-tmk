import logging
import ccxt
import pandas as pd
from typing import Optional
from config import Config

logger = logging.getLogger(__name__)


class Exchange:
    def __init__(self):
        exchange_class = ccxt.binance
        self.exchange = exchange_class({
            "apiKey": Config.BINANCE_API_KEY,
            "secret": Config.BINANCE_SECRET_KEY,
            "enableRateLimit": True,
        })

        if Config.BINANCE_TESTNET:
            self.exchange.set_sandbox_mode(True)
            logger.info("Binance TESTNET mode enabled")

        self.markets = self.exchange.load_markets()
        logger.info("Connected to Binance (%s)", "TESTNET" if Config.BINANCE_TESTNET else "MAINNET")

    def fetch_ohlcv(self, symbol: str = None, timeframe: str = None, limit: int = 100) -> pd.DataFrame:
        symbol = symbol or Config.SYMBOL
        timeframe = timeframe or Config.TIMEFRAME

        ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit + 1)
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        return df

    def fetch_balance(self) -> dict:
        return self.exchange.fetch_balance()

    def get_free_balance(self, currency: str) -> float:
        balance = self.fetch_balance()
        return float(balance.get(currency, {}).get("free", 0))

    def get_ticker(self, symbol: str = None) -> dict:
        return self.exchange.fetch_ticker(symbol or Config.SYMBOL)

    def get_position_size(self, quote_size: float = None) -> float:
        symbol = Config.SYMBOL
        ticker = self.get_ticker(symbol)
        price = ticker["last"]
        size = (quote_size or Config.QUOTE_SIZE) / price

        market = self.markets[symbol]
        amount_precision = market["precision"]["amount"]
        size = self.exchange.amount_to_precision(symbol, size)
        return float(size)

    def create_market_order(self, side: str, amount: float, symbol: str = None) -> dict:
        symbol = symbol or Config.SYMBOL
        logger.info("Placing %s order: %s %.6f", side.upper(), symbol, amount)
        return self.exchange.create_market_order(symbol, side, amount)

    def create_limit_order(self, side: str, amount: float, price: float, symbol: str = None) -> dict:
        symbol = symbol or Config.SYMBOL
        logger.info("Placing LIMIT %s order: %s %.6f @ %.2f", side.upper(), symbol, amount, price)
        return self.exchange.create_limit_order(symbol, side, amount, price)

    def has_open_positions(self, symbol: str = None) -> bool:
        symbol = symbol or Config.SYMBOL
        try:
            orders = self.exchange.fetch_open_orders(symbol)
            return len(orders) > 0
        except Exception:
            return False

    def cancel_all_orders(self, symbol: str = None):
        symbol = symbol or Config.SYMBOL
        self.exchange.cancel_all_orders(symbol)
