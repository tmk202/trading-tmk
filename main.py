#!/usr/bin/env python3
import logging
import sys
import time
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler

from config import Config
from bot import TradingBot
from notify import Notifier

logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("main")


def run_once():
    bot = TradingBot()
    bot.run_once()


def schedule():
    bot = TradingBot()
    notifier = Notifier()

    interval = Config.CHECK_INTERVAL_MINUTES
    scheduler = BlockingScheduler()

    @scheduler.scheduled_job("interval", minutes=interval, id="trade_cycle")
    def job():
        logger.info("Scheduled cycle at %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        bot.run_cycle()

    logger.info("Starting scheduler: every %d minutes", interval)
    notifier.send(
        f"<b>Bot started</b>\n"
        f"Symbol: {Config.SYMBOL}\n"
        f"Strategy: {Config.STRATEGY}\n"
        f"Timeframe: {Config.TIMEFRAME}\n"
        f"Interval: {interval}m\n"
        f"Testnet: {Config.BINANCE_TESTNET}"
    )

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        scheduler.shutdown()
        notifier.send("Bot stopped")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        run_once()
    elif len(sys.argv) > 1 and sys.argv[1] == "--dashboard":
        from dashboard import run_dashboard
        run_dashboard()
    else:
        schedule()
