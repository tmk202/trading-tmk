#!/usr/bin/env python3
"""
Hypothesis Lab — anomaly testing engine
Usage:
  python3 hypothesis_lab.py day_of_week    # BTC return by weekday
  python3 hypothesis_lab.py hour_of_day    # BTC return by hour
  python3 hypothesis_lab.py weekend        # Weekend effect
  python3 hypothesis_lab.py list           # List all tests
"""
import sys
import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timezone
import logging
logging.disable(logging.CRITICAL)

ex = ccxt.binance({"enableRateLimit": True})
now = ex.milliseconds()


def fetch(days=730):
    s = now - days * 24 * 60 * 60 * 1000
    all_c = []
    while s < now:
        o = ex.fetch_ohlcv("BTC/USDT", "1h", since=s, limit=500)
        if not o: break
        all_c.extend(o)
        s = o[-1][0] + 1
        if len(o) < 500: break
    df = pd.DataFrame(all_c, columns=["ts", "o", "h", "l", "c", "v"])
    df = df.drop_duplicates(subset="ts")
    df["date"] = pd.to_datetime(df["ts"], unit="ms")
    df.set_index("date", inplace=True)
    df.rename(columns={"c": "close", "v": "volume"}, inplace=True)
    return df[["close", "volume"]]


def test_day_of_week():
    """BTC return by day of week (Mon-Sun)"""
    print("=== DAY OF WEEK ANOMALY ===")
    df = fetch(730)
    df["return"] = df["close"].pct_change() * 100
    df["dow"] = df.index.dayofweek  # Mon=0, Sun=6
    df["hour"] = df.index.hour

    print(f"Data: {len(df)} 1h candles (2 years)")
    print()

    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    print(f"{'Day':<8} {'N':<8} {'Mean%':<10} {'Median%':<10} {'Std%':<10} {'Hit% (up)':<10} {'Hit% (>0.5%)':<12} {'PF':<8}")
    print("-" * 76)

    for d in range(7):
        vals = df[df["dow"] == d]["return"].dropna()
        n = len(vals)
        mean_r = vals.mean()
        med_r = vals.median()
        std_r = vals.std()
        hit_up = (vals > 0).sum() / n * 100
        hit_big = (vals > 0.5).sum() / n * 100
        pf = abs(vals[vals > 0].sum() / vals[vals < 0].sum()) if vals[vals < 0].sum() != 0 else float("inf")
        print(f"{days[d]:<8} {n:<8} {mean_r:<+8.3f}%  {med_r:<+8.3f}%  {std_r:<8.3f}%  {hit_up:<8.1f}%  {hit_big:<10.1f}%  {pf:<8.2f}")

    # Anova-style: best vs worst
    best = days[df.groupby("dow")["return"].mean().idxmax()]
    worst = days[df.groupby("dow")["return"].mean().idxmin()]
    print(f"\nBest: {best} | Worst: {worst}")


def test_hour_of_day():
    """BTC return by hour (UTC)"""
    print("=== HOUR OF DAY ANOMALY ===")
    df = fetch(730)
    df["return"] = df["close"].pct_change() * 100
    df["hour"] = df.index.hour

    print(f"Data: {len(df)} 1h candles (2 years)")
    print()

    print(f"{'Hour':<8} {'N':<8} {'Mean%':<10} {'Median%':<10} {'Std%':<10} {'Hit% (up)':<10}")
    print("-" * 56)

    for h in range(24):
        vals = df[df["hour"] == h]["return"].dropna()
        n = len(vals)
        mean_r = vals.mean()
        med_r = vals.median()
        std_r = vals.std()
        hit_up = (vals > 0).sum() / n * 100
        print(f"{h:02d}h  {n:<8} {mean_r:<+8.3f}%  {med_r:<+8.3f}%  {std_r:<8.3f}%  {hit_up:<8.1f}%")

    best_h = df.groupby("hour")["return"].mean().idxmax()
    worst_h = df.groupby("hour")["return"].mean().idxmin()
    print(f"\nBest: {best_h:02d}h | Worst: {worst_h:02d}h")


def test_weekend():
    """Weekend vs Weekday effect"""
    print("=== WEEKEND EFFECT ===")
    df = fetch(730)
    df["return"] = df["close"].pct_change() * 100
    df["dow"] = df.index.dayofweek
    df["is_weekend"] = df["dow"].isin([5, 6])  # Sat, Sun

    print(f"Data: {len(df)} 1h candles (2 years)")
    print()

    for label, mask in [("Weekday", False), ("Weekend", True)]:
        vals = df[df["is_weekend"] == mask]["return"].dropna()
        n = len(vals)
        print(f"\n  {label}:")
        print(f"    N={n} | Mean={vals.mean():+.3f}% | Median={vals.median():+.3f}%")
        print(f"    Hit (up)={(vals>0).sum()/n*100:.1f}% | PF={abs(vals[vals>0].sum()/vals[vals<0].sum()):.2f}")

    # Weekend overnight (Fri close → Mon open)
    print("\n  Weekend hold (Fri close → Mon open):")
    df_d = df.resample("1D").last()
    df_d["dow"] = df_d.index.dayofweek
    df_d["ret"] = df_d["close"].pct_change() * 100
    fri_close = df_d[df_d["dow"] == 4]["close"]
    mon_open = df_d[df_d["dow"] == 0]["close"]
    aligned = pd.concat([fri_close.shift(-1), mon_open], axis=1).dropna()
    if len(aligned) > 0:
        weekend_ret = (aligned.iloc[:, 1] / aligned.iloc[:, 0] - 1) * 100
        print(f"    N={len(weekend_ret)} | Mean={weekend_ret.mean():+.3f}% | Hit={(weekend_ret>0).sum()/len(weekend_ret)*100:.1f}%")


def test_list():
    print("Available tests:")
    tests = [k.replace("test_", "") for k in globals().keys() if k.startswith("test_")]
    for t in sorted(tests):
        print(f"  python3 hypothesis_lab.py {t}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        test_list()
        sys.exit(1)

    test_name = sys.argv[1]
    func_name = f"test_{test_name}"
    if func_name in globals():
        globals()[func_name]()
    else:
        print(f"Unknown test: {test_name}")
        test_list()
