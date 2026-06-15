#!/usr/bin/env python3
"""
Build promising wallet universe — combine OKX Web3 + Hyperliquid leaderboards,
apply conservative filters, output a tiered promising_wallets.csv.

Tier rules (Conservative):
  hot      — ROI>=150%, N>=50 (or vol>=100M for HL), DD<=30%, WR>=50%      → size 2.0x
  warm     — ROI>=80%,  N>=30 (or vol>=50M  for HL), DD<=40%, WR>=40%      → size 1.0x
  explore  — ROI>=50%,  N>=20 (or vol>=20M  for HL), DD<=50%              → size 0.5x
  dropped  — fails minimum (ROI<50% or N<20 or DD>50%)                     → excluded

Usage:
  python3 tier_wallet_universe.py
  python3 tier_wallet_universe.py --data-dir data/copy_trade --output promising_wallets.csv
"""
from __future__ import annotations

import argparse
import csv
import os
from dataclasses import dataclass
from typing import Iterable

# Tier rules
# OKX dùng: tx (số trades), win_rate_pct, pnl_history_max_drawdown
# Hyperliquid dùng: volume, account_value (AUM), không có win_rate/drawdown → bỏ qua 2 check đó
TIERS = {
    "hot":     {"min_roi": 150.0, "min_trades": 50,  "min_volume": 100_000_000, "max_dd": 30.0, "min_wr": 50.0, "min_pnl": 10000.0, "size_mult": 2.0},
    "warm":    {"min_roi": 80.0,  "min_trades": 30,  "min_volume":  50_000_000, "max_dd": 40.0, "min_wr": 40.0, "min_pnl":  5000.0, "size_mult": 1.0},
    "explore": {"min_roi": 50.0,  "min_trades": 20,  "min_volume":  20_000_000, "max_dd": 50.0, "min_wr": 0.0,  "min_pnl":  1000.0, "size_mult": 0.5},
}
BASE_SIZE_USD = 100.0


@dataclass
class Candidate:
    platform: str
    wallet: str
    nickname: str
    pnl_30d: float
    roi_pct: float
    sample: float          # tx for OKX, volume for HL
    max_drawdown: float    # negative number or positive percent
    win_rate_pct: float    # 0-100
    extra: dict[str, str]


def _to_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").replace("%", "").replace("$", ""))
    except ValueError:
        return None


def _tier(c: Candidate) -> str | None:
    """Return tier name or None if dropped."""
    if c.roi_pct is None or c.roi_pct <= 0:
        return None
    if c.pnl_30d < TIERS["explore"]["min_pnl"]:
        return None
    if c.platform == "okx_web3" and c.max_drawdown is not None and c.max_drawdown > 50.0:
        return None
    if c.platform == "okx_web3" and (c.sample is None or c.sample < 20):
        return None
    if c.platform == "hyperliquid" and (c.sample is None or c.sample < 20_000_000):
        return None

    for tier_name, rules in TIERS.items():
        if c.roi_pct < rules["min_roi"]:
            continue
        if c.pnl_30d < rules["min_pnl"]:
            continue
        if c.platform == "okx_web3":
            if c.sample < rules["min_trades"]:
                continue
            if c.max_drawdown is not None and c.max_drawdown > rules["max_dd"]:
                continue
            if c.win_rate_pct < rules["min_wr"]:
                continue
        else:  # hyperliquid — skip win_rate/drawdown checks (data not available)
            if c.sample < rules["min_volume"]:
                continue
        return tier_name
    return None


def _read_okx(perf_csv: str) -> Iterable[Candidate]:
    if not os.path.exists(perf_csv):
        return []
    out: list[Candidate] = []
    with open(perf_csv, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            wallet = row.get("wallet") or ""
            if not wallet:
                continue
            out.append(Candidate(
                platform="okx_web3",
                wallet=wallet,
                nickname=row.get("nickname", "")[:32],
                pnl_30d=_to_float(row.get("pnl")) or 0.0,
                roi_pct=_to_float(row.get("roi_pct")) or 0.0,
                sample=_to_float(row.get("tx")) or 0.0,
                max_drawdown=abs(_to_float(row.get("pnl_history_max_drawdown")) or 0.0),
                win_rate_pct=_to_float(row.get("win_rate_pct")) or 0.0,
                extra={
                    "score": row.get("score", ""),
                    "top_tokens": (row.get("top_tokens") or "")[:64],
                },
            ))
    return out


def _read_hyperliquid(leaderboard_csv: str) -> Iterable[Candidate]:
    if not os.path.exists(leaderboard_csv):
        return []
    out: list[Candidate] = []
    with open(leaderboard_csv, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            wallet = row.get("wallet", "")
            if not wallet:
                continue
            if (row.get("is_hft") or "").lower() == "true":
                continue
            if (row.get("active_24h") or "").lower() != "true":
                continue
            account_value = _to_float(row.get("account_value")) or 0.0
            if account_value < 50_000:
                continue
            out.append(Candidate(
                platform="hyperliquid",
                wallet=wallet,
                nickname=(row.get("display_name") or "")[:32],
                pnl_30d=_to_float(row.get("pnl")) or 0.0,
                roi_pct=_to_float(row.get("roi_pct")) or 0.0,
                sample=_to_float(row.get("volume")) or 0.0,
                max_drawdown=0.0,
                win_rate_pct=0.0,
                extra={
                    "account_value": str(account_value),
                    "fills_per_min": row.get("fills_per_min", ""),
                    "source": row.get("source", ""),
                },
            ))
    return out


def build_universe(data_dir: str) -> list[Candidate]:
    candidates: list[Candidate] = []
    candidates.extend(_read_okx(os.path.join(data_dir, "wallet_performance.csv")))
    candidates.extend(_read_hyperliquid(os.path.join(data_dir, "hyperliquid_leaderboard.csv")))

    out: list[Candidate] = []
    for c in candidates:
        t = _tier(c)
        if t is None:
            continue
        c.extra["tier"] = t
        c.extra["size_mult"] = str(TIERS[t]["size_mult"])
        c.extra["position_size_usd"] = str(int(BASE_SIZE_USD * TIERS[t]["size_mult"]))
        out.append(c)

    out.sort(key=lambda x: (TIERS[x.extra["tier"]]["size_mult"], x.roi_pct), reverse=True)
    return out


def write_universe(rows: list[Candidate], output: str) -> None:
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    fieldnames = [
        "platform", "tier", "wallet", "nickname",
        "pnl_30d", "roi_pct", "sample", "max_drawdown", "win_rate_pct",
        "position_size_usd", "size_mult",
    ]
    with open(output, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for c in rows:
            writer.writerow({
                "platform": c.platform,
                "tier": c.extra.get("tier", ""),
                "wallet": c.wallet,
                "nickname": c.nickname,
                "pnl_30d": round(c.pnl_30d, 2),
                "roi_pct": round(c.roi_pct, 2),
                "sample": round(c.sample, 2),
                "max_drawdown": round(c.max_drawdown, 2),
                "win_rate_pct": round(c.win_rate_pct, 2),
                "position_size_usd": c.extra.get("position_size_usd", ""),
                "size_mult": c.extra.get("size_mult", ""),
            })


def main() -> int:
    parser = argparse.ArgumentParser(description="Build promising wallet universe (OKX + Hyperliquid)")
    parser.add_argument("--data-dir", default=os.path.join(os.path.dirname(__file__), "data", "copy_trade"))
    parser.add_argument("--output", default=os.path.join(os.path.dirname(__file__), "data", "copy_trade", "promising_wallets.csv"))
    args = parser.parse_args()

    rows = build_universe(args.data_dir)
    write_universe(rows, args.output)

    by_tier: dict[str, int] = {}
    for c in rows:
        t = c.extra.get("tier", "?")
        by_tier[t] = by_tier.get(t, 0) + 1

    print(f"=== PROMISING WALLET UNIVERSE ===")
    print(f"Output:  {args.output}")
    print(f"Total:   {len(rows)} wallets")
    for tier_name, count in by_tier.items():
        rules = TIERS[tier_name]
        print(f"  {tier_name:<8s} {count:>3d}  (size x{rules['size_mult']}, ROI>={rules['min_roi']:.0f}%, DD<={rules['max_dd']:.0f}%, WR>={rules['min_wr']:.0f}%)")
    print()
    print("Top 10 by tier+ROI:")
    for c in rows[:10]:
        print(f"  [{c.extra.get('tier','?'):<8s}] {c.platform:<11s} {c.nickname[:20]:<20s} "
              f"ROI={c.roi_pct:>8.1f}%  PnL=${c.pnl_30d:>10,.0f}  size=${c.extra.get('position_size_usd','?')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
