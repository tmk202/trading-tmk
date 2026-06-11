# Paper Trade Portfolio — 60 ngày
# So sánh: V3 Strategy vs Buy & Hold vs DCA
import ccxt
import pandas as pd
import numpy as np
import logging
import os
from datetime import datetime, timedelta
from collections import deque
from strategy import compute_ema, compute_adx, compute_atr
from config import Config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("papertrade")
DATA_DIR = os.path.join(os.path.dirname(__file__), "papertrade_data")
os.makedirs(DATA_DIR, exist_ok=True)


class PaperTrade:
    def __init__(self, initial=10000):
        self.initial = initial
        self.cash = initial
        self.positions = {}  # symbol -> shares
        self.trades = []
        self.daily_snapshots = []

        # Benchmarks
        self.bnh_cash = initial
        self.bnh_shares = 0
        self.dca_cash = initial
        self.dca_shares = 0
        self.dca_daily_amount = 50
        self.last_dca_date = None

    def buy(self, symbol, dollars, price, strategy="v3"):
        shares = dollars / price
        fee = dollars * 0.001
        self.cash -= (dollars + fee)
        self.positions[symbol] = self.positions.get(symbol, 0) + shares
        self.trades.append({
            "time": datetime.now(), "side": "buy", "symbol": symbol,
            "price": round(price, 2), "dollars": round(dollars, 2),
            "shares": round(shares, 6), "strategy": strategy,
        })
        logger.info("BUY  %.2f$ %s @ %.2f (%s)", dollars, symbol, price, strategy)

    def sell(self, symbol, dollars, price, strategy="v3"):
        shares_to_sell = dollars / price
        actual_shares = self.positions.get(symbol, 0)
        shares = min(shares_to_sell, actual_shares)
        if shares <= 0:
            return
        revenue = shares * price
        fee = revenue * 0.001
        self.cash += (revenue - fee)
        self.positions[symbol] = actual_shares - shares
        self.trades.append({
            "time": datetime.now(), "side": "sell", "symbol": symbol,
            "price": round(price, 2), "dollars": round(revenue, 2),
            "shares": round(shares, 6), "strategy": strategy,
        })
        logger.info("SELL %.2f$ %s @ %.2f (%s)", revenue, symbol, price, strategy)

    def equity(self, price):
        pos_value = self.positions.get("BTC", 0) * price
        return self.cash + pos_value

    def snapshot(self, date, price):
        eq = self.equity(price)
        bnh_eq = self.bnh_cash + self.bnh_shares * price
        dca_eq = self.dca_cash + self.dca_shares * price
        self.daily_snapshots.append({
            "date": date, "price": price,
            "portfolio": round(eq, 2),
            "v3_pnl": round(eq - self.initial, 2),
            "bnh": round(bnh_eq, 2),
            "dca": round(dca_eq, 2),
            "v3_vs_bnh": round(eq - bnh_eq, 2),
        })


def main():
    ex = ccxt.binance({"enableRateLimit": True})
    pt = PaperTrade(10000)

    # BTC Buy & Hold initial
    price = ex.fetch_ticker("BTC/USDT")["last"]
    pt.bnh_shares = pt.bnh_cash * 0.5 / price
    pt.bnh_cash -= pt.bnh_cash * 0.5
    pt.dca_cash = 10000

    logger.info("Paper Trade started: $10,000")
    logger.info("B&H bought %.6f BTC @ %.2f", pt.bnh_shares, price)

    interval_hours = 4
    last_check = datetime.now() - timedelta(hours=interval_hours + 1)

    while True:
        now = datetime.now()

        # V3 strategy cycle
        if (now - last_check).total_seconds() >= interval_hours * 3600:
            last_check = now
            try:
                df = _fetch_ohlcv(ex, 60)
                price = ex.fetch_ticker("BTC/USDT")["last"]
                signal = _v3_signal(df)
                in_pos = pt.positions.get("BTC", 0) > 0

                if signal == "buy" and not in_pos:
                    pt.buy("BTC", min(Config.QUOTE_SIZE, pt.cash * 0.5), price)
                elif signal == "sell" and in_pos:
                    pos_value = pt.positions["BTC"] * price
                    pt.sell("BTC", pos_value * 0.5, price)

                # DCA
                today = now.date()
                if pt.last_dca_date != today and pt.dca_cash >= pt.dca_daily_amount:
                    pt.dca_shares += pt.dca_daily_amount / price
                    pt.dca_cash -= pt.dca_daily_amount
                    pt.last_dca_date = today

                pt.snapshot(now, price)
                _save_csv(pt)

            except Exception as e:
                logger.error("Cycle error: %s", e)

        # Sleep
        import time
        time.sleep(60)


def _fetch_ohlcv(ex, limit=60):
    ohlcv = ex.fetch_ohlcv("BTC/USDT", "1h", limit=limit)
    df = pd.DataFrame(ohlcv, columns=["ts", "o", "h", "l", "c", "v"])
    df["date"] = pd.to_datetime(df["ts"], unit="ms")
    df.set_index("date", inplace=True)
    return df


def _v3_signal(df):
    if len(df) < 30:
        return "hold"
    c, v = df["c"], df["v"]
    ema20 = compute_ema(c, 20)
    vsma = v.rolling(20).mean()
    adx, _, _ = compute_adx(df)
    rh = c.iloc[-12:-1].max()
    p, pp = c.iloc[-1], c.iloc[-2]
    vs = v.iloc[-1] > vsma.iloc[-1] * 1.5
    vwap = ((df["h"]+df["l"]+c)/3 * v).cumsum() / v.cumsum()
    sl = (ema20.iloc[-1] - ema20.iloc[-5]) / ema20.iloc[-5] * 100
    ed = abs(p - ema20.iloc[-1]) / ema20.iloc[-1] * 100

    if p > rh and pp <= rh and vs and sl > 0.05 and p > vwap.iloc[-1] and ed < 1.5 and adx.iloc[-1] > 20:
        return "buy"
    if p < ema20.iloc[-1] or adx.iloc[-1] < 18:
        return "sell"
    return "hold"


def _save_csv(pt):
    path = os.path.join(DATA_DIR, "daily.csv")
    df = pd.DataFrame(pt.daily_snapshots)
    df.to_csv(path, index=False)

    # Summary
    last = pt.daily_snapshots[-1] if pt.daily_snapshots else {}
    if last:
        print(f"\n{'='*50}")
        print(f"Paper Trade | {last['date'].strftime('%Y-%m-%d %H:%M')}")
        print(f"{'='*50}")
        print(f"Portfolio: ${last['portfolio']:.2f}")
        print(f"V3 PnL:    ${last['v3_pnl']:+.2f}")
        print(f"B&H:       ${last['bnh']:.2f}")
        print(f"DCA:       ${last['dca']:.2f}")
        print(f"V3 vs B&H: ${last['v3_vs_bnh']:+.2f}")
        print(f"Trades:    {len([t for t in pt.trades if t['strategy']=='v3'])}")
        wins = [t for t in pt.trades if t['strategy']=='v3' and t['side']=='sell' and t['dollars'] > 0]
        print()


if __name__ == "__main__":
    main()
