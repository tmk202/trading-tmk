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
from bot_oanda import OandaBot
from strategy import Signal

logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("main_oanda")
console = Console()


class DashboardOanda(OandaBot):
    def __init__(self):
        super().__init__()
        self.log_lines = deque(maxlen=15)
        self.trade_log = deque(maxlen=8)
        self.cycle_count = 0
        self._prev_balance = 0

    def log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_lines.append(f"[{ts}] {msg}")

    def add_trade(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.trade_log.appendleft(f"[{ts}] {msg}")

    def run_cycle(self):
        self.cycle_count += 1
        try:
            df = self.oanda.fetch_ohlcv(count=50)
            price = self.oanda.get_mid_price()
            balance = self.oanda.get_balance()

            if self.cycle_count == 1:
                self._prev_balance = balance

            higher_tf_df = None
            if Config.HIGHER_TF:
                higher_tf_df = self.oanda.fetch_ohlcv(timeframe=Config.HIGHER_TF, count=50)

            signal = self.strategy.analyze(df, higher_tf_df)
            self.check_position()

            price_str = f"{price:.2f}"
            if signal == Signal.HOLD:
                self.log(f"XAU/USD | {price_str} | {signal.value} | Pos: {self._in_position}")
            elif signal == Signal.BUY:
                self.log(f"🔥 BUY @ {price_str}")
                self.add_trade(f"🔥 BUY @ {price_str}")
                self._execute_buy(price)
            elif signal == Signal.SELL:
                self.log(f"🔻 SELL @ {price_str}")
                self.add_trade(f"🔻 SELL @ {price_str}")
                self._execute_sell(price)

            self._last_signal = signal

        except Exception as e:
            self.log(f"Error: {e}")
            logger.exception("Cycle error")

        self._reset_daily_if_new_day()


def make_panels(bot: DashboardOanda) -> Layout:
    layout = Layout()
    layout.split(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=1),
    )
    body = layout["body"]
    body.split_row(
        Layout(name="left"),
        Layout(name="right"),
    )
    body["left"].split(
        Layout(name="market"),
        Layout(name="account"),
    )
    body["right"].split(
        Layout(name="strategy"),
        Layout(name="trades"),
    )

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header_text = Text(
        f"  GOLD BOT  ●  XAU/USD  |  {Config.STRATEGY}  |  {now}",
        style="bold white on dark_goldenrod"
    )
    layout["header"].update(Panel(Align.center(header_text), style="dark_goldenrod"))

    # Market
    m = Table.grid(padding=(0, 2))
    m.add_column(style="bold yellow", justify="right")
    m.add_column(style="white")
    price = bot.oanda.get_mid_price()
    m.add_row("Symbol", "[bold]XAU/USD (Gold)")
    m.add_row("Price", f"[bold]${price:,.2f}")
    m.add_row("Timeframe", f"{Config.OANDA_TIMEFRAME} / HTF: {Config.HIGHER_TF}")
    layout["market"].update(Panel(m, title="🥇 Gold", border_style="yellow"))

    # Account
    a = Table.grid(padding=(0, 2))
    a.add_column(style="bold cyan", justify="right")
    a.add_column(style="white")
    try:
        bal = bot.oanda.get_balance()
    except Exception:
        bal = 0
    a.add_row("Balance", f"[bold]${bal:,.2f}")
    a.add_row("In position", "YES" if bot._in_position else "NO")
    a.add_row("Instrument", Config.OANDA_SYMBOL)
    layout["account"].update(Panel(a, title="💰 Account", border_style="cyan"))

    # Strategy
    s = Table.grid(padding=(0, 2))
    s.add_column(style="bold green", justify="right")
    s.add_column(style="white")
    cooldown_str = str((bot._cooldown_until - datetime.now()).seconds // 60) + "m" if bot._cooldown_until else "None"
    s.add_row("Strategy", f"[bold]{Config.STRATEGY}")
    s.add_row("Signal", f"[bold]{bot._last_signal.value.upper()}")
    s.add_row("Cooldown", cooldown_str)
    s.add_row("Losses", f"{bot._consecutive_losses}/{Config.COOLDOWN_LOSSES}")
    layout["strategy"].update(Panel(s, title="🎯 Strategy", border_style="green"))

    # Trades
    if bot.trade_log:
        layout["trades"].update(Panel("\n".join(bot.trade_log), title="📜 Trades", border_style="dim"))
    else:
        layout["trades"].update(Panel("No trades yet", title="📜 Trades", border_style="dim"))

    # Footer
    layout["footer"].update(Text(" Ctrl+C to stop ", style="dim"))

    return layout


def run_dashboard():
    bot = DashboardOanda()
    interval = Config.CHECK_INTERVAL_MINUTES * 60

    bot.log(f"OANDA Gold Bot | {Config.OANDA_TIMEFRAME} | DEMO={Config.OANDA_DEMO}")

    with Live(console=console, refresh_per_second=1, screen=True) as live:
        last_cycle = 0
        try:
            while True:
                now_ts = time.time()
                if now_ts - last_cycle >= interval:
                    bot.run_cycle()
                    last_cycle = now_ts
                live.update(make_panels(bot))
                time.sleep(1)
        except KeyboardInterrupt:
            pass


def run_once():
    bot = OandaBot()
    bot.run_once()


def schedule():
    from apscheduler.schedulers.blocking import BlockingScheduler
    bot = OandaBot()
    scheduler = BlockingScheduler()

    @scheduler.scheduled_job("interval", minutes=Config.CHECK_INTERVAL_MINUTES)
    def job():
        bot.run_cycle()

    logger.info("OANDA bot started (every %d min)", Config.CHECK_INTERVAL_MINUTES)
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
