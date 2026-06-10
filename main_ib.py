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
from bot_ib import IBBot
from strategy import Signal

logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("main_ib")
console = Console()


class DashboardIB(IBBot):
    def __init__(self):
        super().__init__()
        self.log_lines = deque(maxlen=15)
        self.trade_log = deque(maxlen=8)
        self.cycle_count = 0
        self._prev_balance = 0

    def log(self, msg: str):
        self.log_lines.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def add_trade(self, msg: str):
        self.trade_log.appendleft(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def run_cycle(self):
        self.cycle_count += 1
        try:
            df = self.ib.fetch_ohlcv(self.contract, days=7)
            if df.empty:
                return
            price = self.ib.get_price(self.contract)
            balance = self.ib.get_balance()

            if self.cycle_count == 1:
                self._prev_balance = balance

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
                self.log(f"XAU={price:.2f} | Signal={signal.value} | Pos={self._in_position}")

            self._last_signal = signal

        except Exception as e:
            self.log(f"Error: {e}")
            logger.exception("Cycle error")

        self._reset_daily_if_new_day()


def make_layout(bot: DashboardIB) -> Layout:
    layout = Layout()
    layout.split(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=1),
    )
    layout["body"].split_row(Layout(name="left"), Layout(name="right"))
    layout["left"].split(Layout(name="market"), Layout(name="account"))
    layout["right"].split(Layout(name="strategy"), Layout(name="trades"))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    layout["header"].update(Panel(
        Align.center(Text(f"  GOLD BOT (IB)  ●  MGC  |  {Config.STRATEGY}  |  {now}", style="bold white on dark_goldenrod")),
        style="dark_goldenrod"
    ))

    m = Table.grid(padding=(0, 2))
    m.add_column(style="bold yellow", justify="right")
    m.add_column(style="white")
    try:
        price = bot.ib.get_price(bot.contract) if bot.ib.connected else 0
    except:
        price = 0
    m.add_row("Symbol", "[bold]Micro Gold Futures (MGC)")
    m.add_row("Price", f"[bold]${price:.2f}")
    m.add_row("Timeframe", f"{Config.IB_TIMEFRAME}")
    layout["market"].update(Panel(m, title=" Gold", border_style="yellow"))

    a = Table.grid(padding=(0, 2))
    a.add_column(style="bold cyan", justify="right")
    a.add_column(style="white")
    try:
        bal = bot.ib.get_balance() if bot.ib.connected else 0
    except:
        bal = 0
    a.add_row("Balance", f"[bold]${bal:,.2f}")
    a.add_row("In position", "YES" if bot._in_position else "NO")
    a.add_row("Symbol", Config.IB_SYMBOL)
    layout["account"].update(Panel(a, title=" Account", border_style="cyan"))

    s = Table.grid(padding=(0, 2))
    s.add_column(style="bold green", justify="right")
    s.add_column(style="white")
    cooldown = str((bot._cooldown_until - datetime.now()).seconds // 60) + "m" if bot._cooldown_until else "None"
    s.add_row("Strategy", f"[bold]{Config.STRATEGY}")
    s.add_row("Signal", f"[bold]{bot._last_signal.value.upper()}")
    s.add_row("Cooldown", cooldown)
    s.add_row("Losses", f"{bot._consecutive_losses}/{Config.COOLDOWN_LOSSES}")
    layout["strategy"].update(Panel(s, title=" Strategy", border_style="green"))

    layout["trades"].update(Panel(
        "\n".join(bot.trade_log) if bot.trade_log else "No trades yet",
        title=" Trades", border_style="dim"
    ))

    layout["footer"].update(Text(" Ctrl+C to stop ", style="dim"))
    return layout


def run_dashboard():
    bot = DashboardIB()
    bot.connect()
    interval = Config.CHECK_INTERVAL_MINUTES * 60

    bot.log(f"IB Gold Bot | {Config.IB_TIMEFRAME} | {Config.IB_SYMBOL}")

    with Live(console=console, refresh_per_second=1, screen=True) as live:
        last_cycle = 0
        try:
            while True:
                now_ts = time.time()
                if now_ts - last_cycle >= interval:
                    bot.run_cycle()
                    last_cycle = now_ts
                live.update(make_layout(bot))
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    bot.ib.disconnect()


def run_once():
    bot = IBBot()
    bot.connect()
    bot.run_once()
    bot.ib.disconnect()


def schedule():
    from apscheduler.schedulers.blocking import BlockingScheduler
    bot = IBBot()
    bot.connect()
    scheduler = BlockingScheduler()

    @scheduler.scheduled_job("interval", minutes=Config.CHECK_INTERVAL_MINUTES)
    def job():
        bot.run_cycle()

    logger.info("IB Gold bot started")
    try:
        scheduler.start()
    except KeyboardInterrupt:
        scheduler.shutdown()
    bot.ib.disconnect()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        run_once()
    elif len(sys.argv) > 1 and sys.argv[1] == "--dashboard":
        run_dashboard()
    else:
        schedule()
