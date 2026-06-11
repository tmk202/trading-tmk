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
from bot_mt5 import MT5Bot
from strategy import Signal

logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main_mt5")
console = Console()


class DashboardBot(MT5Bot):
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
            df = self.api.fetch_ohlcv(count=200)
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
                self.log(f"XAU={price:.2f} | {signal.value} | Pos={self._in_position}")

            self._last_signal = signal
        except Exception as e:
            self.log(f"Error: {e}")
        self._reset_daily()


def build_layout(bot, live_price, live_balance):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    header = Panel(Align.center(Text(f"  GOLD BOT (MT5)  ●  {Config.MT5_SYMBOL}  |  {Config.STRATEGY}  |  {ts}",
        style="bold white on green")), style="green")

    def section(title, rows, border):
        t = Table.grid(padding=(0, 2))
        t.add_column(style="bold", justify="right")
        t.add_column(style="white")
        for r in rows:
            t.add_row(*r)
        return Panel(t, title=title, border_style=border)

    m_rows = [("Symbol", f"[bold]{Config.MT5_SYMBOL} (Gold)"), ("Price", f"[bold]${live_price:.2f}"),
              ("Timeframe", Config.MT5_TIMEFRAME)]
    a_rows = [("Balance", f"[bold]${live_balance:,.2f}"), ("Server", Config.MT5_SERVER or "local"),
              ("In pos", "YES" if bot._in_position else "NO")]
    cd = str((bot._cooldown_until - datetime.now()).seconds // 60) + "m" if bot._cooldown_until else "None"
    s_rows = [("Strategy", f"[bold]{Config.STRATEGY}"), ("Signal", f"[bold]{bot._last_signal.value.upper()}"),
              ("Cooldown", cd), ("Losses", f"{bot._consecutive_losses}/{Config.COOLDOWN_LOSSES}")]

    lo = Layout()
    lo.split(Layout(header, size=3), Layout(name="body"), Layout(Text(" Ctrl+C to stop ", style="dim"), size=1))
    lo["body"].split_row(Layout(name="left"), Layout(name="right"))
    lo["body"]["left"].split(Layout(section(" Gold", m_rows, "yellow")), Layout(section(" Account", a_rows, "cyan")))
    lo["body"]["right"].split(Layout(section(" Strategy", s_rows, "green")),
                              Layout(Panel("\n".join(bot.trade_log) if bot.trade_log else "No trades yet",
                                  title=" Trades", border_style="dim")))
    return lo


def run_dashboard():
    bot = DashboardBot()
    bot.connect()
    interval = Config.CHECK_INTERVAL_MINUTES * 60
    bot.log(f"MT5 Gold Bot | {Config.MT5_SYMBOL} | {Config.MT5_TIMEFRAME} | {Config.STRATEGY}")

    with Live(console=console, refresh_per_second=1, screen=True) as live:
        last_cycle = 0
        try:
            while True:
                now_ts = time.time()
                if now_ts - last_cycle >= interval:
                    bot.run_cycle()
                    last_cycle = now_ts
                live.update(build_layout(bot, bot.api.get_price(), bot.api.get_balance()))
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    bot.api.shutdown()


def run_once():
    bot = MT5Bot()
    bot.connect()
    bot.run_once()
    bot.api.shutdown()


def schedule():
    from apscheduler.schedulers.blocking import BlockingScheduler
    bot = MT5Bot()
    bot.connect()
    scheduler = BlockingScheduler()
    @scheduler.scheduled_job("interval", minutes=Config.CHECK_INTERVAL_MINUTES)
    def job():
        bot.run_cycle()
    logger.info("MT5 gold bot started")
    try:
        scheduler.start()
    except KeyboardInterrupt:
        scheduler.shutdown()
    bot.api.shutdown()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        run_once()
    elif len(sys.argv) > 1 and sys.argv[1] == "--dashboard":
        run_dashboard()
    else:
        schedule()
