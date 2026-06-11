#!/usr/bin/env python3
"""
Signal Engine — funding/OI driven hypothesis scanner.

This module is intentionally separate from live execution. It answers:
1. What is the current market state?
2. Did any microstructure signal fire?
3. How did those signals behave historically?

Usage:
  python3 signal_engine.py scan
  python3 signal_engine.py backtest --days 180 --hold 24
  python3 signal_engine.py export --days 180
"""
from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

import ccxt
import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("signal_engine")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
REPORT_DIR = os.path.join(os.path.dirname(__file__), "research")


@dataclass(frozen=True)
class SignalDefinition:
    name: str
    side: str
    description: str


SIGNALS = {
    "h9_crowded_long_short": SignalDefinition(
        name="h9_crowded_long_short",
        side="short",
        description="Funding high + OI rising + price sideway/up weak; long side may be crowded.",
    ),
    "h9_crowded_short_long": SignalDefinition(
        name="h9_crowded_short_long",
        side="long",
        description="Funding low/negative + OI rising + price sideway/down weak; short side may be crowded.",
    ),
    "funding_flip_short": SignalDefinition(
        name="funding_flip_short",
        side="short",
        description="Funding flips from positive to negative after positive carry; possible sentiment break.",
    ),
    "funding_flip_long": SignalDefinition(
        name="funding_flip_long",
        side="long",
        description="Funding flips from negative to positive after negative carry; possible rebound.",
    ),
    "flush_rebound_long": SignalDefinition(
        name="flush_rebound_long",
        side="long",
        description="Price flush + funding depressed + OI falling; possible deleveraging rebound.",
    ),
    "global_longs_crowded_short": SignalDefinition(
        name="global_longs_crowded_short",
        side="short",
        description="Global long/short ratio high + weak price; crowded retail long setup.",
    ),
    "top_longs_crowded_short": SignalDefinition(
        name="top_longs_crowded_short",
        side="short",
        description="Top-trader position ratio high + funding positive + weak price; crowded smart-money long setup.",
    ),
    "taker_sell_pressure_short": SignalDefinition(
        name="taker_sell_pressure_short",
        side="short",
        description="Taker buy/sell ratio weak + OI rising; aggressive sellers dominate.",
    ),
    "taker_buy_pressure_long": SignalDefinition(
        name="taker_buy_pressure_long",
        side="long",
        description="Taker buy/sell ratio strong + OI rising; aggressive buyers dominate.",
    ),
}


def _swap_symbol(symbol: str) -> str:
    if ":" in symbol:
        return symbol
    return f"{symbol}:USDT" if symbol.endswith("/USDT") else symbol


def _linear_symbol(symbol: str) -> str:
    return symbol.replace(":USDT", "").replace("/", "")


def _parse_timeframe_hours(timeframe: str) -> int:
    if timeframe.endswith("h"):
        return int(timeframe[:-1])
    if timeframe.endswith("m"):
        minutes = int(timeframe[:-1])
        if minutes % 60 != 0:
            raise ValueError("Signal engine expects a timeframe divisible into hours.")
        return max(1, minutes // 60)
    if timeframe.endswith("d"):
        return int(timeframe[:-1]) * 24
    raise ValueError(f"Unsupported timeframe: {timeframe}")


class SignalData:
    def __init__(self, symbol: str, timeframe: str):
        self.symbol = symbol
        self.swap_symbol = _swap_symbol(symbol)
        self.linear_symbol = _linear_symbol(symbol)
        self.timeframe = timeframe
        self.ex = ccxt.binance({"enableRateLimit": True})

    def fetch_ohlcv(self, days: int) -> pd.DataFrame:
        now = self.ex.milliseconds()
        since = now - days * 24 * 60 * 60 * 1000
        rows = []
        while since < now:
            chunk = self.ex.fetch_ohlcv(self.symbol, self.timeframe, since=since, limit=1000)
            if not chunk:
                break
            rows.extend(chunk)
            since = chunk[-1][0] + 1
            if len(chunk) < 1000:
                break
        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        if df.empty:
            return df
        df = df.drop_duplicates(subset="timestamp")
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        return df[["open", "high", "low", "close", "volume"]].astype(float)

    def fetch_funding(self, days: int) -> pd.DataFrame:
        now = self.ex.milliseconds()
        since = now - days * 24 * 60 * 60 * 1000
        rows = []
        while since < now:
            chunk = self.ex.fetch_funding_rate_history(self.swap_symbol, since=since, limit=1000)
            if not chunk:
                break
            rows.extend(chunk)
            since = chunk[-1]["timestamp"] + 1
            if len(chunk) < 1000:
                break
        df = pd.DataFrame([
            {"timestamp": item["timestamp"], "funding": item["fundingRate"]}
            for item in rows
        ])
        if df.empty:
            return df
        df = df.drop_duplicates(subset="timestamp")
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        return df.astype(float)

    def fetch_open_interest(self, days: int) -> pd.DataFrame:
        # Binance openInterestHist is much more restrictive than OHLCV/funding.
        # Keep this recent so the signal scan stays reliable instead of failing
        # the whole research run on older startTime values.
        days = min(days, 29)
        now = self.ex.milliseconds()
        since = now - days * 24 * 60 * 60 * 1000
        rows = []
        while since < now:
            chunk = self.ex.fetch_open_interest_history(
                self.swap_symbol, "1h", since=since, limit=500
            )
            if not chunk:
                break
            rows.extend(chunk)
            since = chunk[-1]["timestamp"] + 1
            if len(chunk) < 500:
                break
        df = pd.DataFrame([
            {
                "timestamp": item["timestamp"],
                "oi_btc": item.get("openInterestAmount"),
                "oi_usd": item.get("openInterestValue"),
            }
            for item in rows
        ])
        if df.empty:
            return df
        df = df.drop_duplicates(subset="timestamp")
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        return df.astype(float)

    def fetch_futures_sentiment(self, days: int) -> pd.DataFrame:
        # These Binance futures market-data endpoints are free but only keep
        # recent history, similar to openInterestHist.
        days = min(days, 29)
        now = self.ex.milliseconds()
        since = now - days * 24 * 60 * 60 * 1000

        frames = [
            self._fetch_ratio_endpoint(
                self.ex.fapiDataGetGlobalLongShortAccountRatio,
                since,
                "global",
                {"symbol": self.linear_symbol, "period": self.timeframe},
            ),
            self._fetch_ratio_endpoint(
                self.ex.fapiDataGetTopLongShortPositionRatio,
                since,
                "top_position",
                {"symbol": self.linear_symbol, "period": self.timeframe},
            ),
            self._fetch_ratio_endpoint(
                self.ex.fapiDataGetTopLongShortAccountRatio,
                since,
                "top_account",
                {"symbol": self.linear_symbol, "period": self.timeframe},
            ),
            self._fetch_taker_ratio(since),
        ]
        frames = [frame for frame in frames if not frame.empty]
        if not frames:
            return pd.DataFrame()
        out = pd.concat(frames, axis=1).sort_index()
        out = out[~out.index.duplicated(keep="last")]
        return out.astype(float)

    def _fetch_ratio_endpoint(self, endpoint, since: int, prefix: str, params: dict) -> pd.DataFrame:
        rows = []
        cursor = since
        now = self.ex.milliseconds()
        while cursor < now:
            chunk = endpoint({**params, "startTime": cursor, "limit": 500})
            if not chunk:
                break
            rows.extend(chunk)
            cursor = int(chunk[-1]["timestamp"]) + 1
            if len(chunk) < 500:
                break
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype("int64"), unit="ms")
        df.set_index("timestamp", inplace=True)
        return df.rename(columns={
            "longAccount": f"{prefix}_long",
            "shortAccount": f"{prefix}_short",
            "longShortRatio": f"{prefix}_lsr",
        })[[f"{prefix}_long", f"{prefix}_short", f"{prefix}_lsr"]]

    def _fetch_taker_ratio(self, since: int) -> pd.DataFrame:
        rows = []
        cursor = since
        now = self.ex.milliseconds()
        while cursor < now:
            chunk = self.ex.fapiDataGetTakerlongshortRatio({
                "symbol": self.linear_symbol,
                "period": self.timeframe,
                "startTime": cursor,
                "limit": 500,
            })
            if not chunk:
                break
            rows.extend(chunk)
            cursor = int(chunk[-1]["timestamp"]) + 1
            if len(chunk) < 500:
                break
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype("int64"), unit="ms")
        df.set_index("timestamp", inplace=True)
        return df.rename(columns={
            "buySellRatio": "taker_buy_sell_ratio",
            "buyVol": "taker_buy_vol",
            "sellVol": "taker_sell_vol",
        })[["taker_buy_sell_ratio", "taker_buy_vol", "taker_sell_vol"]]


class FeatureBuilder:
    def __init__(self, timeframe: str):
        self.timeframe = timeframe
        self.tf_hours = _parse_timeframe_hours(timeframe)
        self.bars_24h = max(1, 24 // self.tf_hours)
        self.bars_48h = max(1, 48 // self.tf_hours)

    def align(
        self,
        ohlcv: pd.DataFrame,
        funding: pd.DataFrame,
        oi: pd.DataFrame,
        sentiment: pd.DataFrame,
    ) -> pd.DataFrame:
        if ohlcv.empty:
            raise ValueError("No OHLCV data fetched.")
        rule = self.timeframe
        df = ohlcv.copy()

        if not funding.empty:
            df["funding"] = funding["funding"].resample(rule).last().ffill()
        else:
            df["funding"] = np.nan

        if not oi.empty:
            oi_agg = oi.resample(rule).last().ffill()
            df["oi_btc"] = oi_agg["oi_btc"]
            df["oi_usd"] = oi_agg["oi_usd"]
        else:
            df["oi_btc"] = np.nan
            df["oi_usd"] = np.nan

        if not sentiment.empty:
            sentiment_agg = sentiment.resample(rule).last().ffill()
            for column in sentiment_agg.columns:
                df[column] = sentiment_agg[column]

        return df.dropna(subset=["close", "funding", "oi_btc"])

    def enrich(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        b24 = self.bars_24h
        b48 = self.bars_48h

        out["ret_1"] = out["close"].pct_change()
        out["ret_24h"] = out["close"].pct_change(b24)
        out["ret_48h"] = out["close"].pct_change(b48)
        out["range_24h"] = (
            out["high"].rolling(b24).max() / out["low"].rolling(b24).min() - 1
        )
        out["volatility_24h"] = out["ret_1"].rolling(b24).std()

        out["funding_ma_24h"] = out["funding"].rolling(b24, min_periods=max(2, b24 // 2)).mean()
        out["funding_z_30d"] = _zscore(out["funding"], window=max(b24 * 30, 30))
        out["funding_prev"] = out["funding"].shift(1)
        out["funding_prev2"] = out["funding"].shift(2)

        out["oi_change_24h"] = out["oi_btc"].pct_change(b24)
        out["oi_change_48h"] = out["oi_btc"].pct_change(b48)
        out["oi_z_30d"] = _zscore(out["oi_change_24h"], window=max(b24 * 30, 30))
        if "taker_buy_sell_ratio" in out:
            out["taker_buy_sell_z"] = _zscore(out["taker_buy_sell_ratio"], window=max(b24 * 30, 30))
        else:
            out["taker_buy_sell_z"] = np.nan

        out["ema_20"] = out["close"].ewm(span=20, adjust=False).mean()
        out["ema_50"] = out["close"].ewm(span=50, adjust=False).mean()
        out["ema_slope_24h"] = out["ema_20"].pct_change(b24)
        out["trend"] = np.select(
            [
                (out["close"] > out["ema_50"]) & (out["ema_slope_24h"] > 0),
                (out["close"] < out["ema_50"]) & (out["ema_slope_24h"] < 0),
            ],
            ["up", "down"],
            default="sideway",
        )

        return out

    def label_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        window = max(self.bars_24h * 30, 30)
        min_periods = min(30, max(10, self.bars_24h * 7))
        funding_hi = out["funding"] > out["funding"].rolling(window, min_periods=min_periods).quantile(0.85)
        funding_lo = out["funding"] < out["funding"].rolling(window, min_periods=min_periods).quantile(0.15)
        oi_rising = out["oi_change_24h"] > out["oi_change_24h"].rolling(window, min_periods=min_periods).quantile(0.75)
        oi_falling = out["oi_change_24h"] < out["oi_change_24h"].rolling(window, min_periods=min_periods).quantile(0.25)
        global_lsr_hi = _rolling_quantile_flag(out, "global_lsr", window, min_periods, 0.85, "gt")
        top_position_hi = _rolling_quantile_flag(out, "top_position_lsr", window, min_periods, 0.85, "gt")
        taker_ratio_hi = _rolling_quantile_flag(out, "taker_buy_sell_ratio", window, min_periods, 0.75, "gt")
        taker_ratio_lo = _rolling_quantile_flag(out, "taker_buy_sell_ratio", window, min_periods, 0.25, "lt")
        sideway = out["ret_24h"].abs() < 0.02
        weak_up = out["ret_24h"].between(0, 0.025)
        weak_down = out["ret_24h"].between(-0.025, 0)
        weak_or_down = out["ret_24h"] < 0.015
        weak_or_up = out["ret_24h"] > -0.015
        flush_down = out["ret_24h"] < -0.04

        out["h9_crowded_long_short"] = funding_hi & oi_rising & (sideway | weak_up)
        out["h9_crowded_short_long"] = funding_lo & oi_rising & (sideway | weak_down)
        out["funding_flip_short"] = (
            ((out["funding_prev"] > 0.0001) | (out["funding_prev2"] > 0.0001))
            & (out["funding"] < -0.00005)
        )
        out["funding_flip_long"] = (
            ((out["funding_prev"] < -0.0001) | (out["funding_prev2"] < -0.0001))
            & (out["funding"] > 0.00005)
        )
        out["flush_rebound_long"] = flush_down & funding_lo & oi_falling
        out["global_longs_crowded_short"] = global_lsr_hi & weak_or_down
        out["top_longs_crowded_short"] = top_position_hi & funding_hi & weak_or_down
        out["taker_sell_pressure_short"] = taker_ratio_lo & oi_rising & weak_or_down
        out["taker_buy_pressure_long"] = taker_ratio_hi & oi_rising & weak_or_up

        out["signal_score_short"] = (
            out["h9_crowded_long_short"].astype(int) * 3
            + out["funding_flip_short"].astype(int) * 2
            + out["global_longs_crowded_short"].astype(int) * 2
            + out["top_longs_crowded_short"].astype(int) * 3
            + out["taker_sell_pressure_short"].astype(int) * 2
            + ((out["funding_z_30d"] > 1.5) & (out["oi_z_30d"] > 1)).astype(int)
        )
        out["signal_score_long"] = (
            out["h9_crowded_short_long"].astype(int) * 3
            + out["funding_flip_long"].astype(int) * 2
            + out["flush_rebound_long"].astype(int) * 3
            + out["taker_buy_pressure_long"].astype(int) * 2
            + ((out["funding_z_30d"] < -1.5) & (out["oi_z_30d"] > 1)).astype(int)
        )
        out["signal"] = "hold"
        out.loc[out["signal_score_short"] >= 3, "signal"] = "short"
        out.loc[out["signal_score_long"] >= 3, "signal"] = "long"
        out.loc[
            (out["signal_score_short"] >= 3) & (out["signal_score_long"] >= 3),
            "signal",
        ] = "conflict"
        return out


class EventBacktester:
    def __init__(self, timeframe: str, hold_hours: int, fee_bps: float, slippage_bps: float):
        self.tf_hours = _parse_timeframe_hours(timeframe)
        self.hold_bars = max(1, hold_hours // self.tf_hours)
        self.cost = (fee_bps + slippage_bps) / 10_000

    def run(self, df: pd.DataFrame) -> pd.DataFrame:
        rows = []
        for signal_name, definition in SIGNALS.items():
            events = df[df[signal_name]].copy()
            for ts, row in events.iterrows():
                exit_idx = df.index.get_loc(ts) + self.hold_bars
                if exit_idx >= len(df):
                    continue
                exit_row = df.iloc[exit_idx]
                raw_ret = exit_row["close"] / row["close"] - 1
                pnl = raw_ret if definition.side == "long" else -raw_ret
                pnl -= self.cost * 2
                rows.append({
                    "signal": signal_name,
                    "side": definition.side,
                    "entry_time": ts,
                    "exit_time": exit_row.name,
                    "entry": row["close"],
                    "exit": exit_row["close"],
                    "pnl_pct": pnl * 100,
                    "funding": row["funding"],
                    "oi_change_24h": row["oi_change_24h"],
                    "ret_24h": row["ret_24h"],
                    "trend": row["trend"],
                })
        return pd.DataFrame(rows)

    @staticmethod
    def summarize(trades: pd.DataFrame) -> pd.DataFrame:
        if trades.empty:
            return pd.DataFrame()
        rows = []
        for signal, group in trades.groupby("signal"):
            wins = group[group["pnl_pct"] > 0]
            losses = group[group["pnl_pct"] <= 0]
            gross_win = wins["pnl_pct"].sum()
            gross_loss = losses["pnl_pct"].sum()
            rows.append({
                "signal": signal,
                "side": group["side"].iloc[0],
                "trades": len(group),
                "win_rate": len(wins) / len(group) * 100,
                "avg_pnl_pct": group["pnl_pct"].mean(),
                "median_pnl_pct": group["pnl_pct"].median(),
                "total_pnl_pct": group["pnl_pct"].sum(),
                "profit_factor": abs(gross_win / gross_loss) if gross_loss else np.inf,
                "best_pct": group["pnl_pct"].max(),
                "worst_pct": group["pnl_pct"].min(),
            })
        return pd.DataFrame(rows).sort_values(["profit_factor", "avg_pnl_pct"], ascending=False)


def _zscore(series: pd.Series, window: int) -> pd.Series:
    min_periods = min(30, max(10, window // 4))
    mean = series.rolling(window, min_periods=min_periods).mean()
    std = series.rolling(window, min_periods=min_periods).std()
    return (series - mean) / std.replace(0, np.nan)


def _rolling_quantile_flag(
    df: pd.DataFrame,
    column: str,
    window: int,
    min_periods: int,
    quantile: float,
    op: str,
) -> pd.Series:
    if column not in df:
        return pd.Series(False, index=df.index)
    threshold = df[column].rolling(window, min_periods=min_periods).quantile(quantile)
    if op == "gt":
        return df[column] > threshold
    if op == "lt":
        return df[column] < threshold
    raise ValueError(f"Unsupported op: {op}")


def build_dataset(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    fetcher = SignalData(symbol, timeframe)
    builder = FeatureBuilder(timeframe)

    logger.info("Fetching %s %s OHLCV (%sd)...", symbol, timeframe, days)
    ohlcv = fetcher.fetch_ohlcv(days)
    logger.info("Fetching funding (%sd)...", days)
    funding = fetcher.fetch_funding(days)
    logger.info("Fetching open interest (%sd)...", days)
    oi = fetcher.fetch_open_interest(days)
    logger.info("Fetching futures sentiment (%sd)...", days)
    sentiment = fetcher.fetch_futures_sentiment(days)

    df = builder.align(ohlcv, funding, oi, sentiment)
    df = builder.enrich(df)
    df = builder.label_signals(df)
    return df


def print_scan(df: pd.DataFrame, rows: int) -> None:
    latest = df.dropna(subset=["funding", "oi_change_24h"]).iloc[-1]
    print("\n=== SIGNAL SCAN ===")
    print(f"Time:        {latest.name}")
    print(f"Close:       {latest['close']:,.2f}")
    print(f"Trend:       {latest['trend']}")
    print(f"Funding:     {latest['funding'] * 100:+.4f}%")
    print(f"Funding z:   {latest['funding_z_30d']:+.2f}")
    print(f"OI 24h:      {latest['oi_change_24h'] * 100:+.2f}%")
    print(f"OI z:        {latest['oi_z_30d']:+.2f}")
    if "global_lsr" in latest:
        print(f"Global L/S:  {latest['global_lsr']:.2f}")
    if "top_position_lsr" in latest:
        print(f"Top Pos L/S: {latest['top_position_lsr']:.2f}")
    if "taker_buy_sell_ratio" in latest:
        print(f"Taker B/S:   {latest['taker_buy_sell_ratio']:.2f}")
    print(f"Return 24h:  {latest['ret_24h'] * 100:+.2f}%")
    print(f"Signal:      {latest['signal'].upper()}")
    print(f"Score L/S:   {int(latest['signal_score_long'])}/{int(latest['signal_score_short'])}")

    active = [name for name in SIGNALS if bool(latest.get(name, False))]
    if active:
        print("\nActive hypotheses:")
        for name in active:
            print(f"  - {name}: {SIGNALS[name].description}")
    else:
        print("\nActive hypotheses: none")

    recent_cols = [
        "close",
        "funding",
        "oi_change_24h",
        "global_lsr",
        "top_position_lsr",
        "taker_buy_sell_ratio",
        "ret_24h",
        "trend",
        "signal",
    ]
    recent_cols = [column for column in recent_cols if column in df.columns]
    recent = df[recent_cols].dropna().tail(rows).copy()
    recent["funding"] = recent["funding"] * 100
    recent["oi_change_24h"] = recent["oi_change_24h"] * 100
    recent["ret_24h"] = recent["ret_24h"] * 100
    print(f"\nLast {rows} bars:")
    print(recent.to_string(float_format=lambda x: f"{x:,.3f}"))


def print_backtest(summary: pd.DataFrame, trades: pd.DataFrame, hold_hours: int) -> None:
    print(f"\n=== EVENT BACKTEST ({hold_hours}h hold) ===")
    if summary.empty:
        print("No events found. Need more data or lower thresholds.")
        return
    view = summary.copy()
    for col in ["win_rate", "avg_pnl_pct", "median_pnl_pct", "total_pnl_pct", "best_pct", "worst_pct"]:
        view[col] = view[col].map(lambda x: f"{x:+.2f}" if "pnl" in col or "pct" in col else f"{x:.2f}")
    view["profit_factor"] = view["profit_factor"].map(lambda x: "inf" if np.isinf(x) else f"{x:.2f}")
    print(view.to_string(index=False))

    print("\nRecent events:")
    cols = ["signal", "side", "entry_time", "exit_time", "pnl_pct", "funding", "oi_change_24h", "trend"]
    recent = trades[cols].sort_values("entry_time").tail(12).copy()
    recent["pnl_pct"] = recent["pnl_pct"].map(lambda x: f"{x:+.2f}")
    recent["funding"] = recent["funding"].map(lambda x: f"{x * 100:+.4f}%")
    recent["oi_change_24h"] = recent["oi_change_24h"].map(lambda x: f"{x * 100:+.2f}%")
    print(recent.to_string(index=False))


def export_outputs(df: pd.DataFrame, trades: pd.DataFrame, summary: pd.DataFrame) -> None:
    os.makedirs(REPORT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    signal_path = os.path.join(REPORT_DIR, f"signal_dataset_{stamp}.csv")
    trades_path = os.path.join(REPORT_DIR, f"signal_trades_{stamp}.csv")
    summary_path = os.path.join(REPORT_DIR, f"signal_summary_{stamp}.csv")
    df.to_csv(signal_path)
    trades.to_csv(trades_path, index=False)
    summary.to_csv(summary_path, index=False)
    print("\nExported:")
    print(f"  {signal_path}")
    print(f"  {trades_path}")
    print(f"  {summary_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Funding/OI signal research engine")
    parser.add_argument("command", choices=["scan", "backtest", "export"])
    parser.add_argument("--symbol", default=os.getenv("SYMBOL", "BTC/USDT"))
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--hold", type=int, default=24, help="Backtest holding period in hours")
    parser.add_argument("--fee-bps", type=float, default=10, help="One-way fee in basis points")
    parser.add_argument("--slippage-bps", type=float, default=2, help="One-way slippage in basis points")
    parser.add_argument("--rows", type=int, default=8)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    df = build_dataset(args.symbol, args.timeframe, args.days)
    if df.empty:
        raise SystemExit("No aligned signal data available.")

    if args.command == "scan":
        print_scan(df, args.rows)
        return

    backtester = EventBacktester(args.timeframe, args.hold, args.fee_bps, args.slippage_bps)
    trades = backtester.run(df)
    summary = backtester.summarize(trades)
    print_backtest(summary, trades, args.hold)

    if args.command == "export":
        export_outputs(df, trades, summary)


if __name__ == "__main__":
    main()
