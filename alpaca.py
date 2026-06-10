import logging
import pandas as pd
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from config import Config

logger = logging.getLogger(__name__)

TF_MAP = {
    "1m": TimeFrame.Minute,
    "5m": TimeFrame.Minute * 5,
    "15m": TimeFrame.Minute * 15,
    "30m": TimeFrame.Minute * 30,
    "1h": TimeFrame.Hour,
    "4h": TimeFrame.Hour * 4,
    "1d": TimeFrame.Day,
}


class Alpaca:
    def __init__(self, api_key: str = None, secret_key: str = None, paper: bool = True):
        self.api_key = api_key or Config.ALPACA_API_KEY
        self.secret_key = secret_key or Config.ALPACA_SECRET_KEY
        self.paper = paper

        self.trading = TradingClient(self.api_key, self.secret_key, paper=self.paper)
        self.data = StockHistoricalDataClient(self.api_key, self.secret_key)

        logger.info("Alpaca %s", "PAPER" if paper else "LIVE")

    def fetch_ohlcv(self, symbol: str = None, timeframe: str = None, days: int = 30) -> pd.DataFrame:
        sym = symbol or Config.ALPACA_SYMBOL
        tf = TF_MAP.get(timeframe or Config.ALPACA_TIMEFRAME, TimeFrame.Hour)
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)

        request = StockBarsRequest(
            symbol_or_symbols=sym,
            timeframe=tf,
            start=start,
            end=end,
        )
        bars = self.data.get_stock_bars(request)
        if not bars.data:
            return pd.DataFrame()

        rows = []
        for bar in bars[sym]:
            rows.append({
                "timestamp": bar.timestamp,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            })

        df = pd.DataFrame(rows)
        df.set_index("timestamp", inplace=True)
        return df

    def get_price(self, symbol: str = None) -> float:
        df = self.fetch_ohlcv(symbol or Config.ALPACA_SYMBOL, "1m", days=1)
        if df.empty:
            return 0
        return float(df["close"].iloc[-1])

    def get_account(self) -> dict:
        acc = self.trading.get_account()
        return {
            "balance": float(acc.cash),
            "equity": float(acc.equity),
            "pnl": float(acc.equity) - float(acc.last_equity) if hasattr(acc, 'last_equity') and acc.last_equity else 0,
        }

    def get_balance(self) -> float:
        return float(self.trading.get_account().cash)

    def create_market_order(self, side: str, qty: int, symbol: str = None) -> dict:
        sym = symbol or Config.ALPACA_SYMBOL
        side_map = {"buy": "buy", "sell": "sell"}
        order_req = MarketOrderRequest(
            symbol=sym,
            qty=qty,
            side=side_map[side],
            time_in_force="day",
        )
        order = self.trading.submit_order(order_req)
        logger.info("Alpaca %s %d %s", side.upper(), qty, sym)
        return {"id": order.id, "qty": order.qty, "filled": order.filled_qty}

    def calculate_qty(self, quote_size: float = None, symbol: str = None) -> int:
        price = self.get_price(symbol)
        size = (quote_size or Config.ALPACA_QUOTE_SIZE) / price
        return max(1, int(size))

    def get_positions(self) -> list:
        return self.trading.get_all_positions()

    def has_position(self, symbol: str = None) -> bool:
        sym = symbol or Config.ALPACA_SYMBOL
        positions = self.get_positions()
        for p in positions:
            if p.symbol == sym:
                return abs(float(p.qty)) > 0
        return False

    def close_position(self, symbol: str = None):
        sym = symbol or Config.ALPACA_SYMBOL
        try:
            self.trading.close_position(sym)
            logger.info("Closed %s position", sym)
        except Exception as e:
            logger.warning("Close position error: %s", e)
