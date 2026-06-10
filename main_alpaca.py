#!/usr/bin/env python3
import logging
import sys
import time
from datetime import datetime
from collections import deque

from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.align import Align
from rich.console import Console

from config import Config
from bot_alpaca import AlpacaBot
from strategy import Signal

logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main_alpaca")
console = Console()


class DashboardAlpaca(AlpacaBot):
    def __init__(self):
        super().__init__()
        self.log_lines = deque(maxlen=15)
        self.trade_log = deque(maxlen=8)
        self.cycle_count = 0

    def log(self, msg):
        self.log_lines.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def add_trade(self, msg):
        self.trade_log.appendleft(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def run_cycle(self):
        self.cycle_count += 1
        try:
            df = self.api.fetch_ohlcv(days=30)
            if df.empty:
                return
            price = self.api.get_price()
            balance = self.api.get_balance()

            signal = self.strategy.analyze(df)
            self.check_position()

            if signal == Signal.BUY and not self._in_position:
                self.log(f"BUY @ {price:.2f}")
                self.add_trade(f"BUY @ {price:.2f}")
                self._execute_buy(price)
            elif signal == Signal.SELL and self._in_position:
                self.log(f"SELL @ {price:.2f}")
                self.add_trade(f"SELL @ {price:.2f}")
                self._execute_sell(price)
            else:
                self.log(f"GLD={price:.2f} | {signal.value} | Pos={self._in_position}")

            self._last_signal = signal
        except Exception as e:
            self.log(f"Error: {e}")
        self._reset_daily()


def build_layout(bot):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        price = bot.api.get_price()
        bal = bot.api.get_balance()
    except:
        price = 0
        bal = 0

    header = Panel(Align.center(Text(f"  GOLD BOT (Alpaca)  ●  GLD  |  {Config.STRATEGY}  |  {ts}",
        style="bold white on yellow")), style="yellow")

    def section(title, rows, border):
        t = Table.grid(padding=(0, 2))
        t.add_column(style="bold", justify="right")
        t.add_column(style="white")
        for r in rows:
            t.add_row(*r)
        return Panel(t, title=title, border_style=border)

    market_rows = [
        ("Symbol", f"[bold]{Config.ALPACA_SYMBOL} (Gold ETF)"),
        ("Price", f"[bold]${price:.2f}"),
        ("Timeframe", f"{Config.ALPACA_TIMEFRAME}"),
    ]
    account_rows = [
        ("Balance", f"[bold]${bal:,.2f}"),
        ("In position", "YES" if bot._in_position else "NO"),
        ("Paper", "YES"),
    ]
    cd = str((bot._cooldown_until - datetime.now()).seconds // 60) + "m" if bot._cooldown_until else "None"
    strat_rows = [
        ("Strategy", f"[bold]{Config.STRATEGY}"),
        ("Signal", f"[bold]{bot._last_signal.value.upper()}"),
        ("Cooldown", cd),
        ("Losses", f"{bot._consecutive_losses}/{Config.COOLDOWN_LOSSES}"),
    ]

    market_p = section(" Gold", market_rows, "yellow")
    account_p = section(" Account", account_rows, "cyan")
    strat_p = section(" Strategy", strat_rows, "green")
    trades_p = Panel(
        "\n".join(bot.trade_log) if bot.trade_log else "No trades yet",
        title=" Trades", border_style="dim"
    )

    lo = Layout()
    lo.split(Layout(header, size=3), Layout(name="body"), Layout(Text(" Ctrl+C to stop ", style="dim"), size=1))
    lo["body"].split_row(Layout(name="left"), Layout(name="right"))
    lo["body"]["left"].split(Layout(market_p), Layout(account_p))
    lo["body"]["right"].split(Layout(strat_p), Layout(trades_p))
    return lo


def run_dashboard():
    bot = DashboardAlpaca()
    interval = Config.CHECK_INTERVAL_MINUTES * 60
    bot.log(f"Alpaca Gold Bot | {Config.ALPACA_SYMBOL} | {Config.ALPACA_TIMEFRAME} | {Config.STRATEGY}")

    with Live(console=console, refresh_per_second=1, screen=True) as live:
        last_cycle = 0
        try:
            while True:
                now_ts = time.time()
                if now_ts - last_cycle >= interval:
                    bot.run_cycle()
                    last_cycle = now_ts
                live.update(build_layout(bot))
                time.sleep(1)
        except KeyboardInterrupt:
            pass


def run_once():
    bot = AlpacaBot()
    bot.run_once()


def schedule():
    from apscheduler.schedulers.blocking import BlockingScheduler
    bot = AlpacaBot()
    scheduler = BlockingScheduler()
    @scheduler.scheduled_job("interval", minutes=Config.CHECK_INTERVAL_MINUTES)
    def job():
        bot.run_cycle()
    logger.info("Alpaca bot started")
    try:
        scheduler.start()
    except KeyboardInterrupt:
        scheduler.shutdown()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        run_once()
    elif len(sys.argv) > 1 and sys.argv[1] == "--dashboard":
        run_dashboard()
    else:
        schedule()
