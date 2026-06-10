#!/usr/bin/env python3
"""
Research: H9 — Funding dương + OI tăng + giá sideway = Short setup?

Event study approach (not backtest):
1. Find H9 events in available data
2. Measure forward returns
3. Compare vs random (counterfactual)

Hypothesis: When funding is high, OI is rising, but price is flat,
the market is overcrowded long → downside expected.
"""
import ccxt
import pandas as pd
import numpy as np
from datetime import datetime
import logging
logging.disable(logging.CRITICAL)

ex = ccxt.binance({"enableRateLimit": True})
now = ex.milliseconds()

print("=== H9 RESEARCH: Funding + OI + Price Sideway ===\n")

# ── 1. Fetch data ──
def fetch_ohlcv(days=180):
    a = []; s = now - days*24*60*60*1000
    while s < now:
        o = ex.fetch_ohlcv("BTC/USDT", "4h", since=s, limit=500)
        if not o: break
        a.extend(o); s = o[-1][0]+1
        if len(o) < 500: break
    df = pd.DataFrame(a, columns=["ts","o","h","l","c","v"])
    df = df.drop_duplicates(subset="ts")
    df["date"] = pd.to_datetime(df["ts"], unit="ms")
    df.set_index("date", inplace=True)
    df.rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"}, inplace=True)
    df.drop(columns=["ts"], inplace=True)
    return df

def fetch_funding(days=90):
    s = now - days*24*60*60*1000
    fr = ex.fetch_funding_rate_history("BTC/USDT:USDT", since=s, limit=500)
    rows = [{"date": pd.to_datetime(f["timestamp"], unit="ms"), "funding": f["fundingRate"]} for f in fr]
    df = pd.DataFrame(rows).set_index("date")
    return df

def fetch_oi(hours=168):
    s = now - hours*60*60*1000
    oi = ex.fetch_open_interest_history("BTC/USDT:USDT", "1h", since=s, limit=500)
    rows = [{
        "date": pd.to_datetime(o["timestamp"], unit="ms"),
        "oi_btc": o["openInterestAmount"],
        "oi_usd": o["openInterestValue"],
    } for o in oi]
    df = pd.DataFrame(rows).set_index("date")
    return df

print("Fetching OHLCV (4h, 180 days)...")
ohlcv = fetch_ohlcv(180)
print(f"  {len(ohlcv)} candles")

print("Fetching funding rate (90 days)...")
funding = fetch_funding(90)
print(f"  {len(funding)} entries")

print("Fetching Open Interest (7 days)...")
oi = fetch_oi(168)
print(f"  {len(oi)} entries")

# ── 2. Align data to 4h candles ──
# Funding: resample to 4h (last value in window)
funding_4h = funding.resample("4h").last().ffill()

# OI: resample to 4h (last value in window, forward fill gaps)
oi_4h = oi.resample("4h").last().ffill()

# Merge all into one dataframe
aligned = ohlcv[["close","volume"]].copy()
aligned["funding"] = funding_4h["funding"]
aligned["oi_btc"] = oi_4h["oi_btc"]

# Drop rows without data
aligned = aligned.dropna()
print(f"\nAligned data: {len(aligned)} 4h candles")
print(f"Range: {aligned.index[0]} → {aligned.index[-1]}")

# ── 3. Compute features ──
aligned["return_4h"] = aligned["close"].pct_change()
aligned["return_24h"] = aligned["close"].pct_change(6)  # 6 * 4h = 24h
aligned["return_48h"] = aligned["close"].pct_change(12)

aligned["funding_ma"] = aligned["funding"].rolling(6).mean()
aligned["oi_change_24h"] = aligned["oi_btc"].pct_change(6) * 100  # % change in 24h

# Forward returns
aligned["fwd_4h"] = aligned["close"].pct_change(-1)  # next 4h
aligned["fwd_8h"] = aligned["close"].pct_change(-2)
aligned["fwd_24h"] = aligned["close"].pct_change(-6)
aligned["fwd_48h"] = aligned["close"].pct_change(-12)

# ── 4. Detect H9 events ──
funding_p90 = aligned["funding"].quantile(0.90)
oi_p90 = aligned["oi_change_24h"].quantile(0.90)

# H9: funding high + OI rising + price flat 24h
sideway_thresh = 0.02  # 2% range = sideway
events = aligned[
    (aligned["funding"] > funding_p90) &
    (aligned["oi_change_24h"] > oi_p90) &
    (aligned["return_24h"].abs() < sideway_thresh)
].copy()

print(f"\nFunding 90th percentile: {funding_p90*100:.4f}%")
print(f"OI change 90th percentile: {oi_p90:.1f}%")
print(f"Sideway threshold: ±{sideway_thresh*100:.1f}%\n")
print(f"H9 events found: {len(events)}")

if len(events) >= 3:
    print(f"\nEvent details:")
    print(f"{'Time':<20} {'Funding':<10} {'OI 24h':<10} {'24h ret':<10} {'Fwd 4h':<10} {'Fwd 24h':<10} {'Fwd 48h':<10}")
    print("-"*80)
    for idx, row in events.iterrows():
        print(f"{str(idx)[:16]:<20} {row['funding']*100:<+8.4f}% {row['oi_change_24h']:<+8.1f}% "
              f"{row['return_24h']*100:<+8.2f}% {row['fwd_4h']*100:<+8.2f}% "
              f"{row['fwd_24h']*100:<+8.2f}% {row['fwd_48h']*100:<+8.2f}%")

    # ── 5. Statistics ──
    print(f"\n{'='*60}")
    print(f"FORWARD RETURNS AFTER H9 EVENTS")
    print(f"{'='*60}")
    for period, col, label in [
        (4, "fwd_4h", "4h"), (8, "fwd_8h", "8h"),
        (24, "fwd_24h", "24h"), (48, "fwd_48h", "48h")
    ]:
        vals = events[col].dropna() * 100
        print(f"\n  {label}:")
        print(f"    Count: {len(vals)}")
        print(f"    Mean:  {vals.mean():+.2f}%")
        print(f"    Median:{vals.median():+.2f}%")
        print(f"    Std:   {vals.std():.2f}%")
        print(f"    Hit%:  {(vals < 0).sum()/len(vals)*100:.0f}% (short wins)")

    # ── 6. Counterfactual: random times ──
    print(f"\n{'='*60}")
    print(f"COUNTERFACTUAL: RANDOM vs H9")
    print(f"{'='*60}")
    np.random.seed(42)
    random_idx = np.random.choice(aligned.index, min(len(events)*5, len(aligned)), replace=False)
    random_sample = aligned.loc[random_idx]

    for period, col, label in [
        (24, "fwd_24h", "24h"), (48, "fwd_48h", "48h")
    ]:
        h9_vals = events[col].dropna() * 100
        rnd_vals = random_sample[col].dropna() * 100
        print(f"\n  {label} forward return:")
        print(f"    Random mean: {rnd_vals.mean():+.2f}%  hit% (down): {(rnd_vals < 0).sum()/len(rnd_vals)*100:.0f}%")
        print(f"    H9 mean:     {h9_vals.mean():+.2f}%  hit% (down): {(h9_vals < 0).sum()/len(h9_vals)*100:.0f}%")
        diff = h9_vals.mean() - rnd_vals.mean()
        print(f"    Difference:  {diff:+.2f}%")

else:
    print(f"\nNot enough events to analyze ({len(events)} found)")
    print(f"\nTry lower thresholds:")
    print(f"  Funding threshold: {funding_p90*100:.4f}%")
    print(f"  OI change threshold: {oi_p90:.1f}%")
