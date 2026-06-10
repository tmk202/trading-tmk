#!/usr/bin/env python3
"""
Background data collector: Binance Funding Rate + Open Interest
Chạy nền, mỗi giờ lưu 1 snapshot vào CSV.

Usage:
  python3 collector.py          # chạy nền
  python3 collector.py --once   # chạy 1 lần
"""
import ccxt
import pandas as pd
import os
import sys
import time
import logging
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

FUNDING_FILE = os.path.join(DATA_DIR, "funding_rate.csv")
OI_FILE = os.path.join(DATA_DIR, "open_interest.csv")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("collector")

ex = ccxt.binance({"enableRateLimit": True})


def collect():
    ts = datetime.now()
    logger.info("Collecting data...")

    # Funding rate
    try:
        fr = ex.fetch_funding_rate("BTC/USDT:USDT")
        funding = fr["fundingRate"] if fr else None
        row = {"timestamp": ts, "funding_rate": funding}
        _append_csv(FUNDING_FILE, row)
        logger.info("  Funding: %.6f%%", funding * 100 if funding else 0)
    except Exception as e:
        logger.error("  Funding error: %s", e)

    # Open Interest
    try:
        oi = ex.fetch_open_interest("BTC/USDT:USDT")
        if oi:
            row = {
                "timestamp": ts,
                "oi_btc": oi["openInterestAmount"],
                "oi_usd": oi["openInterestValue"],
            }
            _append_csv(OI_FILE, row)
            logger.info("  OI: %.0f BTC ($%.0f)", row["oi_btc"], row["oi_usd"])
    except Exception as e:
        logger.error("  OI error: %s", e)


def _append_csv(filepath, row):
    df = pd.DataFrame([row])
    if not os.path.exists(filepath):
        df.to_csv(filepath, index=False)
    else:
        df.to_csv(filepath, mode="a", header=False, index=False)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        collect()
    else:
        logger.info("Starting data collector (every 1 hour)")
        collect()
        scheduler = BlockingScheduler()
        scheduler.add_job(collect, "interval", hours=1)
        try:
            scheduler.start()
        except KeyboardInterrupt:
            scheduler.shutdown()
            logger.info("Collector stopped")
