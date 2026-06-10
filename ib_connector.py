import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, Tuple

from ib_insync import *

from config import Config

logger = logging.getLogger(__name__)

TF_MAP = {
    "1m": "1 min",
    "5m": "5 mins",
    "15m": "15 mins",
    "30m": "30 mins",
    "1h": "1 hour",
    "4h": "4 hours",
    "1d": "1 day",
}


class IBConnector:
    def __init__(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 1):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.ib = IB()
        self.connected = False

    def connect(self) -> bool:
        try:
            self.ib.connect(self.host, self.port, clientId=self.client_id)
            self.connected = True
            logger.info("IB connected (port=%d, client=%d)", self.port, self.client_id)
            return True
        except Exception as e:
            logger.error("IB connect failed: %s", e)
            return False

    def disconnect(self):
        if self.connected:
            self.ib.disconnect()
            self.connected = False

    def _get_gold_contract(self) -> Future:
        # Micro Gold Futures (MGC) - COMEX
        # Get current front month
        contracts = self.ib.reqContractDetails(Future("MGC", "", "COMEX"))
        if not contracts:
            raise ValueError("No MGC contracts found")
        # Sort by expiry, take front month
        contracts.sort(key=lambda c: c.contract.lastTradeDateOrContractMonth)
        contract = contracts[0].contract
        logger.info("Gold contract: %s %s @ %s", contract.localSymbol, contract.lastTradeDateOrContractMonth, contract.exchange)
        return contract

    def fetch_ohlcv(self, contract=None, timeframe: str = None, days: int = 30) -> pd.DataFrame:
        if contract is None:
            contract = self._get_gold_contract()
        tf = TF_MAP.get(timeframe or Config.IB_TIMEFRAME, "1 hour")
        end = datetime.now()
        duration = f"{days} D"

        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime=end,
            durationStr=duration,
            barSizeSetting=tf,
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
        )

        if not bars:
            return pd.DataFrame()

        df = pd.DataFrame(bars)
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        df.rename(columns={
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
        }, inplace=True)
        return df[["open", "high", "low", "close", "volume"]]

    def get_price(self, contract=None) -> float:
        if contract is None:
            contract = self._get_gold_contract()
        ticker = self.ib.reqMktData(contract, "", False, False)
        self.ib.sleep(1)
        return ticker.marketPrice() if ticker.marketPrice() else ticker.close

    def get_account_summary(self) -> dict:
        summary = self.ib.accountSummary()
        result = {}
        for s in summary:
            result[s.tag] = s.value
        return result

    def get_balance(self) -> float:
        summary = self.get_account_summary()
        return float(summary.get("TotalCashBalance", 0))

    def create_market_order(self, side: str, quantity: int, contract=None):
        if contract is None:
            contract = self._get_gold_contract()
        action = "BUY" if side == "buy" else "SELL"
        order = MarketOrder(action, quantity)
        trade = self.ib.placeOrder(contract, order)
        logger.info("IB order: %s %d %s", action, quantity, contract.localSymbol)
        return trade

    def calculate_quantity(self, quote_size: float = None) -> int:
        price = self.get_price()
        size = (quote_size or Config.IB_QUOTE_SIZE) / price
        # MGC = 10 oz, so 1 contract ~= 10 * price
        return max(1, int(size / 10))

    def get_positions(self) -> list:
        return self.ib.positions()

    def has_position(self, contract=None) -> bool:
        if contract is None:
            contract = self._get_gold_contract()
        positions = self.get_positions()
        for p in positions:
            if p.contract.localSymbol == contract.localSymbol:
                return abs(p.position) > 0
        return False

    def close_position(self, contract=None):
        if contract is None:
            contract = self._get_gold_contract()
        positions = self.get_positions()
        for p in positions:
            if p.contract.localSymbol == contract.localSymbol and abs(p.position) > 0:
                side = "SELL" if p.position > 0 else "BUY"
                order = MarketOrder(side, abs(p.position))
                self.ib.placeOrder(p.contract, order)
                logger.info("Closed %s position", contract.localSymbol)

    def get_pnl(self) -> float:
        summary = self.get_account_summary()
        return float(summary.get("UnrealizedPnL", 0)) + float(summary.get("RealizedPnL", 0))
