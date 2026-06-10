#!/usr/bin/env python3
import logging
import sys
import ccxt
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List

import pandas as pd
import numpy as np

from config import Config
from strategy import StrategyFactory, Signal, compute_adx, compute_atr, compute_ema

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger("backtest")

INITIAL_BALANCE = 10000
MAKER_FEE = 0.001
TAKER_FEE = 0.001


@dataclass
class Trade:
    side: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    pnl_pct: float
    exit_reason: str = "signal"
    vol_mult: float = 0
    ema_dist_pct: float = 0
    htf_trend: str = ""
    htf_adx: float = 0
    htf_atr: float = 0
    htf_ema_slope: float = 0


@dataclass
class BacktestResult:
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0
    total_pnl: float = 0
    total_pnl_pct: float = 0
    max_drawdown: float = 0
    avg_win: float = 0
    avg_loss: float = 0
    profit_factor: float = 0
    sharpe: float = 0
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)


def compute_sharpe(returns: List[float], rf: float = 0) -> float:
    if len(returns) < 2:
        return 0
    arr = np.array(returns)
    excess = arr - rf / len(returns)
    if excess.std() == 0:
        return 0
    return excess.mean() / excess.std() * np.sqrt(365 * 24 * 60 // Config.CHECK_INTERVAL_MINUTES)


def round_price(price: float) -> float:
    return round(price, 2)


class BacktestEngine:
    def __init__(self, strategy_name: str = None, timeframe: str = None):
        self.strategy = StrategyFactory.create(strategy_name)
        self.timeframe = timeframe or Config.TIMEFRAME
        self.symbol = Config.SYMBOL
        self.balance = INITIAL_BALANCE
        self.position = 0
        self.entry_price = 0
        self.trades: List[Trade] = []
        self.equity: List[float] = []
        self.higher_tf = Config.HIGHER_TF

    def run(self, df: pd.DataFrame, higher_tf_df: pd.DataFrame = None) -> BacktestResult:
        balance = INITIAL_BALANCE
        position = 0
        position_side = 0  # +1 = long, -1 = short
        entry_price = 0
        entry_time = None
        entry_vol_mult = 0
        entry_ema_dist = 0
        entry_htf_trend = ""
        entry_htf_adx = 0
        entry_htf_atr = 0
        entry_htf_ema_slope = 0
        trades = []
        equity = [INITIAL_BALANCE]
        cost = 0
        lowest_low = 0.0

        for i in range(50, len(df)):
            window = df.iloc[:i + 1]
            price = float(window["close"].iloc[-1])
            prev_price = float(window["close"].iloc[-2])

            htf_window = None
            htf_for_entry = None
            if higher_tf_df is not None:
                htf_time = window.index[-1]
                htf_for_entry = higher_tf_df[higher_tf_df.index <= htf_time]
                htf_window = htf_for_entry.iloc[:-1] if len(htf_for_entry) > 1 else htf_for_entry

            signal = self.strategy.analyze(window, higher_tf_df=htf_window)

            def capture_entry_metrics():
                nonlocal entry_ema_dist, entry_vol_mult, entry_htf_trend, entry_htf_adx, entry_htf_atr, entry_htf_ema_slope
                ema20_v = window["close"].ewm(span=20, adjust=False).mean()
                entry_ema_dist = abs(price - ema20_v.iloc[-1]) / ema20_v.iloc[-1] * 100
                vol_sma_v = window["volume"].rolling(20).mean()
                entry_vol_mult = window["volume"].iloc[-1] / vol_sma_v.iloc[-1] if not vol_sma_v.empty and vol_sma_v.iloc[-1] > 0 else 0
                entry_htf_trend = ""
                entry_htf_adx = 0
                entry_htf_atr = 0
                entry_htf_ema_slope = 0
                if htf_for_entry is not None and len(htf_for_entry) > 5:
                    htf_e = compute_ema(htf_for_entry["close"], 20)
                    htf_sl = (htf_e.iloc[-1] - htf_e.iloc[-5]) / htf_e.iloc[-5] * 100
                    entry_htf_ema_slope = round(htf_sl, 2)
                    entry_htf_trend = "up" if htf_sl > 0 else "down" if htf_sl < 0 else "flat"
                    hadx, _, _ = compute_adx(htf_for_entry, 14)
                    entry_htf_adx = round(hadx.iloc[-1], 1) if len(hadx) > 0 else 0
                    hatr = compute_atr(htf_for_entry, 14)
                    entry_htf_atr = round(hatr.iloc[-1], 1) if len(hatr) > 0 else 0

            # ── Long entry ──
            if signal == Signal.BUY and position == 0 and position_side == 0:
                size = Config.QUOTE_SIZE / price
                cost = size * price
                fee = cost * TAKER_FEE
                balance -= (cost + fee)
                position = size
                position_side = 1
                entry_price = price
                entry_time = window.index[-1]
                capture_entry_metrics()

            # ── Short entry (sell first) ──
            elif signal == Signal.SELL and position == 0 and position_side == 0:
                size = Config.QUOTE_SIZE / price
                # Sell short: receive cash now
                revenue = size * price
                fee = revenue * TAKER_FEE
                balance += (revenue - fee)
                position = size
                position_side = -1
                entry_price = price
                entry_time = window.index[-1]
                cost = revenue  # track for PnL calc
                lowest_low = price
                capture_entry_metrics()

            # ── Exit long ──
            elif signal == Signal.SELL and position_side == 1 and position > 0:
                revenue = position * price
                fee = revenue * TAKER_FEE
                balance += (revenue - fee)
                pnl = (price - entry_price) * position - fee * 2
                pnl_pct = (price - entry_price) / entry_price * 100
                trades.append(Trade(side="long", entry_time=entry_time, exit_time=window.index[-1],
                    entry_price=round_price(entry_price), exit_price=round_price(price),
                    size=position, pnl=round(pnl, 2), pnl_pct=round(pnl_pct, 2),
                    vol_mult=round(entry_vol_mult, 2), ema_dist_pct=round(entry_ema_dist, 2),
                    htf_trend=entry_htf_trend, htf_adx=entry_htf_adx, htf_atr=entry_htf_atr,
                    htf_ema_slope=entry_htf_ema_slope))
                position, position_side = 0, 0

            # ── Exit short (buy to cover) ──
            elif signal == Signal.BUY and position_side == -1 and position > 0:
                cost_close = position * price
                fee = cost_close * TAKER_FEE
                balance -= (cost_close + fee)
                pnl = (entry_price - price) * position - fee * 2
                pnl_pct = (entry_price - price) / entry_price * 100
                trades.append(Trade(side="short", entry_time=entry_time, exit_time=window.index[-1],
                    entry_price=round_price(entry_price), exit_price=round_price(price),
                    size=position, pnl=round(pnl, 2), pnl_pct=round(pnl_pct, 2),
                    vol_mult=round(entry_vol_mult, 2), ema_dist_pct=round(entry_ema_dist, 2),
                    htf_trend=entry_htf_trend, htf_adx=entry_htf_adx, htf_atr=entry_htf_atr,
                    htf_ema_slope=entry_htf_ema_slope))
                position, position_side = 0, 0

            # ── ATR Trailing Stop for shorts ──
            if position_side == -1 and position > 0:
                lowest_low = min(lowest_low, window['low'].iloc[-1])
                trail_atr = compute_atr(window, 14).iloc[-1]
                trail_stop = lowest_low + trail_atr * 2.5
                if price > trail_stop:
                    cost = position * price
                    fee = cost * TAKER_FEE
                    balance -= (cost + fee)
                    pnl = (entry_price - price) * position - fee * 2
                    pnl_pct = (entry_price - price) / entry_price * 100
                    trades.append(Trade(side="short", entry_time=entry_time, exit_time=window.index[-1],
                        entry_price=round_price(entry_price), exit_price=round_price(price),
                        size=position, pnl=round(pnl, 2), pnl_pct=round(pnl_pct, 2),
                        exit_reason="trail_stop", vol_mult=round(entry_vol_mult, 2),
                        ema_dist_pct=round(entry_ema_dist, 2), htf_trend=entry_htf_trend,
                        htf_adx=entry_htf_adx, htf_atr=entry_htf_atr, htf_ema_slope=entry_htf_ema_slope))
                    position, position_side = 0, 0

            current_equity = balance + (position * price * position_side) if position_side != 0 else balance
            equity.append(current_equity)

        # Close remaining position at end of data
        if position_side != 0 and position > 0:
            price = float(df["close"].iloc[-1])
            if position_side == 1:
                revenue = position * price
                fee = revenue * TAKER_FEE
                balance += (revenue - fee)
                pnl = (price - entry_price) * position - fee * 2
                pnl_pct = (price - entry_price) / entry_price * 100
                side_label = "long"
            else:
                cost = position * price
                fee = cost * TAKER_FEE
                balance -= (cost + fee)
                pnl = (entry_price - price) * position - fee * 2
                pnl_pct = (entry_price - price) / entry_price * 100
                side_label = "short"

            trades.append(Trade(
                side=side_label,
                entry_time=entry_time,
                exit_time=df.index[-1],
                entry_price=round_price(entry_price),
                exit_price=round_price(price),
                size=position,
                pnl=round(pnl, 2),
                pnl_pct=round(pnl_pct, 2),
                exit_reason="end_of_data",
                vol_mult=round(entry_vol_mult, 2),
                ema_dist_pct=round(entry_ema_dist, 2),
                htf_trend=entry_htf_trend,
                htf_adx=entry_htf_adx,
                htf_atr=entry_htf_atr,
                htf_ema_slope=entry_htf_ema_slope,
            ))

        result = BacktestResult()
        result.trades = trades
        result.total_trades = len(trades)
        result.equity_curve = equity

        if trades:
            wins = [t for t in trades if t.pnl > 0]
            losses = [t for t in trades if t.pnl <= 0]
            result.wins = len(wins)
            result.losses = len(losses)
            result.win_rate = len(wins) / len(trades) * 100 if trades else 0
            result.total_pnl = balance - INITIAL_BALANCE
            result.total_pnl_pct = (balance - INITIAL_BALANCE) / INITIAL_BALANCE * 100
            result.avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0
            result.avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0
            result.profit_factor = abs(sum(t.pnl for t in wins) / sum(t.pnl for t in losses)) if losses else float("inf")

            peak = pd.Series(equity).cummax()
            dd = ((pd.Series(equity) - peak) / peak * 100).min()
            result.max_drawdown = round(dd, 2)

            returns = []
            for t in trades:
                returns.append(t.pnl_pct / 100)
            result.sharpe = round(compute_sharpe(returns), 2)

        return result


def print_result(result: BacktestResult, strategy_name: str):
    sep = "─" * 50
    print(sep)
    print(f"  Backtest: {strategy_name} | {Config.SYMBOL}")
    print(f"  Timeframe: {Config.TIMEFRAME} | Higher TF: {Config.HIGHER_TF}")
    print(sep)
    print(f"  Trades:       {result.total_trades}")
    print(f"  Win rate:     {result.win_rate:.1f}%")
    print(f"  Total PnL:    ${result.total_pnl:+.2f} ({result.total_pnl_pct:+.2f}%)")
    print(f"  Max DD:       {result.max_drawdown:.2f}%")
    print(f"  Sharpe:       {result.sharpe}")
    print(f"  Profit fact:  {result.profit_factor:.2f}")
    print(f"  Avg win:      ${result.avg_win:+.2f}")
    print(f"  Avg loss:     ${result.avg_loss:+.2f}")
    print(sep)

    if result.trades:
        print(f"\n  Last {min(5, len(result.trades))} trades:")
        for t in result.trades[-5:]:
            emoji = "🟢" if t.pnl > 0 else "🔴"
            print(f"  {emoji} {t.side.upper():4s} | "
                  f"{t.entry_time.strftime('%m/%d %H:%M')} → {t.exit_time.strftime('%m/%d %H:%M')} | "
                  f"{t.entry_price:.0f} → {t.exit_price:.0f} | "
                  f"${t.pnl:+.1f} ({t.pnl_pct:+.1f}%)")


def parse_minutes(tf: str) -> int:
    if tf.endswith("m"):
        return int(tf[:-1])
    elif tf.endswith("h"):
        return int(tf[:-1]) * 60
    elif tf.endswith("d"):
        return int(tf[:-1]) * 1440
    return 60


def fetch_public(symbol: str, timeframe: str, limit: int = 1000,
                 since_days: int = 60) -> pd.DataFrame:
    ex = ccxt.binance({"enableRateLimit": True})
    tf_minutes = parse_minutes(timeframe)
    now = ex.milliseconds()
    since = now - since_days * 24 * 60 * 60 * 1000

    all_candles = []
    while since < now:
        ohlcv = ex.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
        if not ohlcv:
            break
        all_candles.extend(ohlcv)
        since = ohlcv[-1][0] + 1
        if len(ohlcv) < limit:
            break

    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates(subset="timestamp")
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    return df


def main():
    days = 60
    if len(sys.argv) > 1:
        days = int(sys.argv[1])

    strategy_name = Config.STRATEGY
    if len(sys.argv) > 2:
        strategy_name = sys.argv[2]

    print(f"Fetching {days} days of {Config.TIMEFRAME} data from Binance public API...")
    df = fetch_public(Config.SYMBOL, Config.TIMEFRAME, since_days=days)

    htf_tf = Config.HIGHER_TF
    if strategy_name in ("scalper_v5", "short_engine"):
        htf_tf = "1h"
    higher_tf_df = None
    if htf_tf:
        higher_tf_df = fetch_public(Config.SYMBOL, htf_tf, since_days=days)
        print(f"Higher TF data: {len(higher_tf_df)} candles ({htf_tf})")

    print(f"Data: {len(df)} candles ({Config.TIMEFRAME})")
    print(f"From: {df.index[0]} → {df.index[-1]}")

    engine = BacktestEngine(strategy_name)
    result = engine.run(df, higher_tf_df)
    print_result(result, strategy_name)

    if result.trades:
        rows = []
        for t in result.trades:
            rows.append({
                "Time": t.entry_time.strftime("%Y-%m-%d %H:%M"),
                "Side": "Long" if t.side == "buy" else "Short",
                "Entry": f"{t.entry_price:.2f}".rstrip("0").rstrip("."),
                "Exit": f"{t.exit_price:.2f}".rstrip("0").rstrip("."),
                "PnL": f"{t.pnl:.2f}".rstrip("0").rstrip("."),
                "PnL%": f"{t.pnl_pct:.2f}",
                "Vol_x": f"{t.vol_mult:.2f}",
                "EMA_dist%": f"{t.ema_dist_pct:.2f}",
                "HTF_trend": t.htf_trend,
                "HTF_ADX": t.htf_adx,
                "HTF_ATR": t.htf_atr,
                "HTF_EMA_slope%": t.htf_ema_slope,
                "Result": "WIN" if t.pnl > 0 else "LOSS",
            })

        out_df = pd.DataFrame(rows)
        csv_file = f"backtest_{strategy_name}_{Config.SYMBOL.replace('/', '')}_{Config.TIMEFRAME}.csv"
        out_df.to_csv(csv_file, index=False)
        print(f"\nCSV saved: {csv_file}")
        print(out_df.to_string(index=False))


if __name__ == "__main__":
    main()
