#!/usr/bin/env python3
"""
H3: Funding Sign Flip → Trend Reversal

Funding changes from positive to negative (or vice versa) 
sharply within 8-12h = sentiment flip = trend reversal signal.
"""
import ccxt
import pandas as pd
import numpy as np
from datetime import datetime
import logging
logging.disable(logging.CRITICAL)

ex = ccxt.binance({"enableRateLimit": True})
now = ex.milliseconds()

print("=== H3: FUNDING SIGN FLIP → TREND REVERSAL ===\n")

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
    return pd.DataFrame(rows).set_index("date")

print("Fetching data...")
ohlcv = fetch_ohlcv(180)
funding = fetch_funding(90)
print(f"  OHLCV: {len(ohlcv)} 4h candles")
print(f"  Funding: {len(funding)} entries")

# ── 2. Align: funding → 4h ──
funding_4h = funding.resample("4h").last().ffill()
df = ohlcv[["close"]].copy()
df["funding"] = funding_4h["funding"]
df = df.dropna()

# ── 3. Detect funding flip events ──
# A flip: consecutive funding entries change sign within 8h (2 x 4h)
df["funding_prev"] = df["funding"].shift(1)
df["funding_prev2"] = df["funding"].shift(2)

# Positive→Negative flip (bearish sentiment shift)
df["flip_p2n"] = (
    ((df["funding_prev"] > 0.0001) | (df["funding_prev2"] > 0.0001)) &
    (df["funding"] < -0.00005)
)

# Negative→Positive flip (bullish sentiment shift)
df["flip_n2p"] = (
    ((df["funding_prev"] < -0.0001) | (df["funding_prev2"] < -0.0001)) &
    (df["funding"] > 0.00005)
)

# Magnitude of flip
df["flip_magnitude"] = abs(df["funding"] - df["funding_prev"])

# Compute forward returns
for h in [4, 8, 24, 48, 72]:
    df[f"fwd_{h}h"] = df["close"].pct_change(-h//4) * 100

# ── 4. Analyze P2N (pos→neg) events ──
events_p2n = df[df["flip_p2n"]].copy()
events_n2p = df[df["flip_n2p"]].copy()

print(f"\nPos→Neg flips: {len(events_p2n)}")
print(f"Neg→Pos flips: {len(events_n2p)}")

def analyze_events(events, label, short_side=True):
    if len(events) < 3:
        print(f"\n  {label}: too few events ({len(events)})")
        return
    
    print(f"\n{'='*60}")
    print(f"  {label} ({len(events)} events)")
    print(f"{'='*60}")
    
    for h in [4, 8, 24, 48, 72]:
        vals = events[f"fwd_{h}h"].dropna()
        if len(vals) < 2: continue
        if short_side:
            hit = (vals < 0).sum() / len(vals) * 100
        else:
            hit = (vals > 0).sum() / len(vals) * 100
        
        print(f"  {h:2d}h forward: mean={vals.mean():+.2f}%  median={vals.median():+.2f}%  "
              f"hit={hit:.0f}%  n={len(vals)}")

    # Return distribution
    print(f"\n  Return distribution (24h):")
    vals = events["fwd_24h"].dropna()
    if len(vals) > 0:
        for p in [10, 25, 50, 75, 90]:
            print(f"    P{p}: {np.percentile(vals, p):+.2f}%")


print(f"\n{'='*60}")
print("  EVENT DETAILS")
print(f"{'='*60}")
if len(events_p2n) > 0:
    print(f"\n  Pos→Neg flips:")
    print(f"  {'Time':<20} {'Funding':<10} {'Flip Mag':<10} {'24h fwd':<10}")
    print(f"  {'-'*50}")
    for idx, row in events_p2n.iterrows():
        print(f"  {str(idx)[:16]:<20} {row['funding']*100:<+.4f}% {row['flip_magnitude']*100:<+.4f}% {row['fwd_24h']:<+.2f}%")

if len(events_n2p) > 0:
    print(f"\n  Neg→Pos flips:")
    print(f"  {'Time':<20} {'Funding':<10} {'Flip Mag':<10} {'24h fwd':<10}")
    print(f"  {'-'*50}")
    for idx, row in events_n2p.iterrows():
        print(f"  {str(idx)[:16]:<20} {row['funding']*100:<+.4f}% {row['flip_magnitude']*100:<+.4f}% {row['fwd_24h']:<+.2f}%")

# ── 5. Counterfactual ──
print(f"\n{'='*60}")
print("  COUNTERFACTUAL: Random vs Events")
print(f"{'='*60}")

np.random.seed(42)
for label, events, short in [("Pos→Neg (short)", events_p2n, True),
                              ("Neg→Pos (long)", events_n2p, False)]:
    if len(events) < 3: continue
    n = max(len(events)*5, 30)
    rand_idx = np.random.choice(df.index[min(30, len(df)):], min(n, len(df)-30), replace=False)
    rand_sample = df.loc[rand_idx]
    
    for h in [24, 48]:
        ev = events[f"fwd_{h}h"].dropna()
        rv = rand_sample[f"fwd_{h}h"].dropna()
        if len(ev) < 2 or len(rv) < 2: continue
        
        print(f"\n  {label} — {h}h forward:")
        hit_r = (rv < 0).sum()/len(rv)*100 if short else (rv > 0).sum()/len(rv)*100
        print(f"    Random:   mean={rv.mean():+.2f}%  hit={'down' if short else 'up'}={hit_r:.0f}%")
        hit_e = (ev < 0).sum()/len(ev)*100 if short else (ev > 0).sum()/len(ev)*100
        print(f"    Events:   mean={ev.mean():+.2f}%  hit={hit_e:.0f}%")
        print(f"    Diff:     {ev.mean()-rv.mean():+.2f}%")

print(f"\n{'='*60}")
print("  RAW DATA SAMPLE")
print(f"{'='*60}")
print(f"\n  Last 10 funding entries:")
print(funding.tail(10).to_string())
