import logging
import ccxt
import pandas as pd
import numpy as np
from typing import Optional

logger = logging.getLogger(__name__)


class MarketData:
    def __init__(self):
        self.ex = ccxt.binance({"enableRateLimit": True})

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100,
                    since_days: int = 60) -> pd.DataFrame:
        now = self.ex.milliseconds()
        since = now - since_days * 24 * 60 * 60 * 1000
        all_candles = []
        while since < now:
            ohlcv = self.ex.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
            if not ohlcv:
                break
            all_candles.extend(ohlcv)
            since = ohlcv[-1][0] + 1
            if len(ohlcv) < limit:
                break
        df = pd.DataFrame(all_candles, columns=["ts", "open", "high", "low", "close", "volume"])
        df = df.drop_duplicates(subset="ts")
        df["date"] = pd.to_datetime(df["ts"], unit="ms")
        df.set_index("date", inplace=True)
        return df

    def fetch_funding_rate(self, symbol: str = "BTC/USDT", limit: int = 100) -> pd.DataFrame:
        try:
            data = self.ex.fetch_funding_rate_history(symbol, limit=limit)
            if not data:
                return pd.DataFrame()
            rows = []
            for d in data:
                rows.append({
                    "timestamp": pd.to_datetime(d["timestamp"], unit="ms"),
                    "funding_rate": d["fundingRate"],
                })
            df = pd.DataFrame(rows)
            df.set_index("timestamp", inplace=True)
            return df
        except Exception as e:
            logger.warning("Funding rate fetch failed: %s", e)
            return pd.DataFrame()

    def fetch_open_interest(self, symbol: str = "BTC/USDT") -> pd.DataFrame:
        try:
            data = self.ex.fetch_open_interest_history(symbol)
            if not data:
                return pd.DataFrame()
            rows = []
            for d in data:
                rows.append({
                    "timestamp": pd.to_datetime(d["timestamp"], unit="ms"),
                    "open_interest": d["openInterest"],
                })
            df = pd.DataFrame(rows)
            df.set_index("timestamp", inplace=True)
            return df
        except Exception as e:
            logger.warning("OI fetch failed: %s", e)
            return pd.DataFrame()

    def get_current_funding(self, symbol: str = "BTC/USDT") -> float:
        try:
            data = self.ex.fetch_funding_rate(symbol)
            return data["fundingRate"] if data else 0
        except Exception:
            return 0

    def get_funding_sentiment(self, df: pd.DataFrame, lookback: int = 24) -> str:
        if df.empty or len(df) < lookback:
            return "neutral"
        recent = df["funding_rate"].iloc[-lookback:]
        avg = recent.mean()
        if avg > 0.005 / 100:
            return "overheated"  # funding cao -> nhiều long, dễ short
        elif avg < -0.005 / 100:
            return "oversold"  # funding âm -> nhiều short, dễ long
        return "neutral"

    def get_oi_trend(self, df: pd.DataFrame, lookback: int = 24) -> str:
        if df.empty or len(df) < lookback:
            return "neutral"
        recent = df["open_interest"].iloc[-lookback:]
        oi_change = (recent.iloc[-1] - recent.iloc[0]) / recent.iloc[0] * 100
        if oi_change > 5:
            return "rising"  # OI tăng -> xu hướng được xác nhận
        elif oi_change < -5:
            return "falling"  # OI giảm -> xu hướng yếu dần
        return "neutral"
