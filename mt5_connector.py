"""
MT5 Connector for Gold (XAUUSD)
Requires: Python 3.10+, MetaTrader5 terminal installed, broker demo account

Usage:
  pip install MetaTrader5
  python -c "import MetaTrader5 as mt5; mt5.initialize()"
"""
import logging
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional

from config import Config

logger = logging.getLogger(__name__)

TF_MAP = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "4h": 240, "1d": 1440, "1w": 10080,
}


class MT5Connector:
    def __init__(self, symbol: str = None, login: int = None, password: str = None, server: str = None):
        self.symbol = symbol or Config.MT5_SYMBOL
        self.login = login or Config.MT5_LOGIN
        self.password = password or Config.MT5_PASSWORD
        self.server = server or Config.MT5_SERVER
        self.initialized = False

    def initialize(self) -> bool:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            logger.error("MT5 init failed: %s", mt5.last_error())
            return False
        if self.login and self.password:
            if not mt5.login(self.login, password=self.password, server=self.server):
                logger.error("MT5 login failed: %s", mt5.last_error())
                mt5.shutdown()
                return False
        if not mt5.symbol_select(self.symbol, True):
            logger.warning("Symbol %s not found", self.symbol)
        self.initialized = True
        logger.info("MT5 ready: %s @ %s", self.symbol, self.server or "local")
        return True

    def _mt5(self):
        import MetaTrader5 as mt5
        if not self.initialized:
            raise RuntimeError("MT5 not initialized. Call initialize() first.")
        return mt5

    def shutdown(self):
        if self.initialized:
            self._mt5().shutdown()
            self.initialized = False

    def fetch_ohlcv(self, timeframe: str = None, count: int = 500) -> pd.DataFrame:
        mt5 = self._mt5()
        tf = TF_MAP.get(timeframe or Config.MT5_TIMEFRAME, 60)
        rates = mt5.copy_rates_from_pos(self.symbol, tf, 0, count)
        if rates is None:
            logger.warning("No data for %s", self.symbol)
            return pd.DataFrame()
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df.set_index("time", inplace=True)
        df.rename(columns={
            "tick_volume": "volume",
        }, inplace=True)
        return df[["open", "high", "low", "close", "volume"]]

    def get_price(self) -> float:
        mt5 = self._mt5()
        tick = mt5.symbol_info_tick(self.symbol)
        if tick is None:
            return 0
        return (tick.bid + tick.ask) / 2

    def get_balance(self) -> float:
        mt5 = self._mt5()
        info = mt5.account_info()
        if info is None:
            return 0
        return info.balance

    def get_equity(self) -> float:
        mt5 = self._mt5()
        info = mt5.account_info()
        return info.equity if info else 0

    def create_market_order(self, side: str, volume: float, sl: float = 0, tp: float = 0) -> dict:
        mt5 = self._mt5()
        symbol_info = mt5.symbol_info(self.symbol)
        if symbol_info is None:
            logger.error("Symbol %s not found", self.symbol)
            return {}

        point = symbol_info.point
        price = symbol_info.ask if side == "buy" else symbol_info.bid

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": volume,
            "type": mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 10,
            "magic": 123456,
            "comment": "gold_bot",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error("Order failed: %s (retcode=%d)", result.comment, result.retcode)
            return {}
        logger.info("MT5 %s %.2f %s @ %.2f", side.upper(), volume, self.symbol, price)
        return {"order": result.order, "volume": volume, "price": price}

    def calculate_volume(self, quote_size: float = None) -> float:
        price = self.get_price()
        size = (quote_size or Config.MT5_QUOTE_SIZE) / price
        # Standardize lot size (0.01 minimum for XAUUSD)
        volume = max(0.01, round(size, 2))
        return volume

    def get_positions(self) -> list:
        mt5 = self._mt5()
        return mt5.positions_get(symbol=self.symbol) or []

    def has_position(self) -> bool:
        positions = self.get_positions()
        return len(positions) > 0

    def close_position(self):
        mt5 = self._mt5()
        positions = mt5.positions_get(symbol=self.symbol)
        if not positions:
            return
        for pos in positions:
            side = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": self.symbol,
                "volume": pos.volume,
                "type": side,
                "position": pos.ticket,
                "price": mt5.symbol_info_tick(self.symbol).bid if side == mt5.ORDER_TYPE_SELL else mt5.symbol_info_tick(self.symbol).ask,
                "deviation": 10,
                "magic": 123456,
                "comment": "gold_bot_close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request)
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info("Closed position %d", pos.ticket)
