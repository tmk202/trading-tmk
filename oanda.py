import logging
import requests
import pandas as pd
from datetime import datetime
from typing import Optional, Tuple

from config import Config

logger = logging.getLogger(__name__)

OANDA_PRACTICE = "https://api-fxpractice.oanda.com"
OANDA_LIVE = "https://api-fxtrade.oanda.com"

TF_MAP = {
    "1m": "M1", "5m": "M5", "15m": "M15",
    "30m": "M30", "1h": "H1", "4h": "H4",
    "1d": "D", "1w": "W",
}


class Oanda:
    def __init__(self, token: str = None, account_id: str = None, demo: bool = True):
        self.token = token or Config.OANDA_TOKEN
        self.account_id = account_id or Config.OANDA_ACCOUNT_ID
        self.demo = demo
        self.base_url = OANDA_PRACTICE if demo else OANDA_LIVE
        self.headers = {"Authorization": f"Bearer {self.token}"}

        logger.info("OANDA %s | Account: %s", "DEMO" if demo else "LIVE", self.account_id)

    def _get(self, path: str, params: dict = None) -> dict:
        url = f"{self.base_url}{path}"
        resp = requests.get(url, headers=self.headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, data: dict) -> dict:
        url = f"{self.base_url}{path}"
        resp = requests.post(url, headers=self.headers, json=data, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def fetch_account_summary(self) -> dict:
        data = self._get(f"/v3/accounts/{self.account_id}")
        return data["account"]

    def get_balance(self) -> float:
        summary = self.fetch_account_summary()
        return float(summary.get("balance", 0))

    def fetch_ohlcv(self, instrument: str = None, timeframe: str = None,
                     count: int = 100) -> pd.DataFrame:
        inst = instrument or Config.OANDA_SYMBOL
        tf = TF_MAP.get(timeframe or Config.OANDA_TIMEFRAME, "H1")

        params = {
            "price": "M",
            "granularity": tf,
            "count": count,
            "dailyAlignment": 0,
        }
        data = self._get(f"/v3/accounts/{self.account_id}/instruments/{inst}/candles", params)

        rows = []
        for c in data.get("candles", []):
            rows.append({
                "timestamp": pd.to_datetime(c["time"].split(".")[0]),
                "open": float(c["mid"]["o"]),
                "high": float(c["mid"]["h"]),
                "low": float(c["mid"]["l"]),
                "close": float(c["mid"]["c"]),
                "volume": int(c["volume"]),
            })

        df = pd.DataFrame(rows)
        if not df.empty:
            df.set_index("timestamp", inplace=True)
        return df

    def get_price(self, instrument: str = None) -> Tuple[float, float]:
        inst = instrument or Config.OANDA_SYMBOL
        params = {"instruments": inst}
        data = self._get(f"/v3/accounts/{self.account_id}/pricing", params)
        prices = data.get("prices", [])
        if prices:
            return float(prices[0]["bids"][0]["price"]), float(prices[0]["asks"][0]["price"])
        return 0, 0

    def get_mid_price(self, instrument: str = None) -> float:
        bid, ask = self.get_price(instrument)
        return (bid + ask) / 2

    def create_market_order(self, side: str, units: float, instrument: str = None) -> dict:
        inst = instrument or Config.OANDA_SYMBOL
        order_spec = {
            "order": {
                "type": "MARKET",
                "instrument": inst,
                "units": str(units),
            }
        }

        logger.info("OANDA %s %s %.2f %s", side.upper(), inst, units, "units")
        return self._post(f"/v3/accounts/{self.account_id}/orders", order_spec)

    def calculate_units(self, quote_size: float = None, instrument: str = None) -> float:
        price = self.get_mid_price(instrument)
        size = (quote_size or Config.OANDA_QUOTE_SIZE) / price
        return round(size, 0)

    def fetch_open_trades(self) -> list:
        data = self._get(f"/v3/accounts/{self.account_id}/trades")
        return data.get("trades", [])

    def has_open_position(self, instrument: str = None) -> bool:
        inst = instrument or Config.OANDA_SYMBOL
        data = self._get(f"/v3/accounts/{self.account_id}/positions/{inst}")
        position = data.get("position", {})
        long_units = float(position.get("long", {}).get("units", "0"))
        short_units = float(position.get("short", {}).get("units", "0"))
        return long_units > 0 or short_units > 0

    def close_trade(self, trade_id: str) -> dict:
        logger.info("Closing trade %s", trade_id)
        return self._put(f"/v3/accounts/{self.account_id}/trades/{trade_id}/close", {})

    def fetch_pending_orders(self) -> list:
        data = self._get(f"/v3/accounts/{self.account_id}/orders", {"state": "PENDING"})
        return data.get("orders", [])

    def _put(self, path: str, data: dict = None) -> dict:
        url = f"{self.base_url}{path}"
        resp = requests.put(url, headers=self.headers, json=data or {}, timeout=15)
        resp.raise_for_status()
        return resp.json()
