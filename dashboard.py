import logging
import time
from datetime import datetime, timedelta
from collections import deque

from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.align import Align
from rich.console import Console
from rich import box

from config import Config
from bot import TradingBot
from strategy import Signal

logger = logging.getLogger(__name__)
console = Console()


class DashboardBot(TradingBot):
    def __init__(self):
        super().__init__()
        self.log_lines = deque(maxlen=15)
        self.trade_log = deque(maxlen=8)
        self.cycle_count = 0
        self._last_price = 0
        self._entry_price = 0
        self._pnl = 0
        self._prev_balance = 0

    def log(self, msg: str, level: str = "info"):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_lines.append(f"[{ts}] {msg}")

    def add_trade(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.trade_log.appendleft(f"[{ts}] {msg}")

    def run_cycle(self):
        self.cycle_count += 1
        try:
            df = self.exchange.fetch_ohlcv(self.symbol, Config.TIMEFRAME, limit=50)
            ticker = self.exchange.get_ticker(self.symbol)
            current_price = ticker["last"]
            balance = self.exchange.fetch_balance()
            usdt_balance = float(balance.get("USDT", {}).get("free", 0))

            if self.cycle_count == 1:
                self._prev_balance = usdt_balance

            self._last_price = current_price
            self._pnl = usdt_balance - self._prev_balance if self._prev_balance else 0

            higher_tf_df = None
            if Config.HIGHER_TF:
                higher_tf_df = self.exchange.fetch_ohlcv(
                    self.symbol, Config.HIGHER_TF, limit=50
                )

            signal = self.strategy.analyze(df, higher_tf_df)
            self.check_position()

            price_str = f"{current_price:.2f}"
            signal_str = signal.value.upper()
            if signal == Signal.HOLD:
                self.log(f"{self.symbol} | {price_str} | Signal: {signal_str} | Pos: {self._in_position}")
            elif signal == Signal.BUY:
                self.log(f"🔥 BUY signal @ {price_str}")
                self.add_trade(f"🔥 BUY @ {price_str} | {self.strategy.name}")
                self._execute_buy(current_price, usdt_balance)
            elif signal == Signal.SELL:
                self.log(f"🔻 SELL signal @ {price_str}")
                self.add_trade(f"🔻 SELL @ {price_str} | {self.strategy.name}")
                self._execute_sell(current_price)

            self._last_signal = signal

        except Exception as e:
            self.log(f"Error: {e}", "error")
            logger.exception("Cycle error")

    def _execute_buy(self, price: float, balance: float):
        amount = self.exchange.get_position_size(Config.QUOTE_SIZE)
        if amount <= 0:
            return
        order = self.exchange.create_market_order("buy", amount)
        self._in_position = True
        self._entry_price = price
        self.add_trade(f"✅ BUY filled {amount:.6f} @ {price:.2f}")

    def _execute_sell(self, price: float):
        base_currency = self.symbol.split("/")[0]
        balance = self.exchange.fetch_balance()
        amount = float(balance.get(base_currency, {}).get("free", 0))
        if amount <= 0:
            self._in_position = False
            return
        order = self.exchange.create_market_order("sell", amount)
        self._in_position = False
        self._entry_price = 0
        self.add_trade(f"✅ SELL filled {amount:.6f} @ {price:.2f}")


def make_market_panel(bot: DashboardBot) -> Panel:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", justify="right")
    table.add_column(style="white")

    price = bot._last_price
    price_color = "green" if price >= bot._entry_price else "red" if bot._entry_price else "white"

    table.add_row("Symbol", f"[bold]{Config.SYMBOL}")
    table.add_row("Price", f"[{price_color}]${price:,.2f}")
    table.add_row("Timeframe", f"{Config.TIMEFRAME} / HTF: {Config.HIGHER_TF}")
    return Panel(table, title="📊 Market", border_style="cyan")


def make_account_panel(bot: DashboardBot) -> Panel:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold yellow", justify="right")
    table.add_column(style="white")

    balance = 0
    try:
        bal = bot.exchange.fetch_balance()
        balance = float(bal.get("USDT", {}).get("free", 0))
    except Exception:
        pass

    pnl_color = "green" if bot._pnl >= 0 else "red"
    pnl_pct = (bot._pnl / bot._prev_balance * 100) if bot._prev_balance else 0

    base = Config.SYMBOL.split("/")[0]
    in_pos_str = f"[green]YES ({bot._in_position})[/green]" if bot._in_position else "[red]NO[/red]"

    table.add_row("Free USDT", f"[bold]${balance:,.2f}")
    table.add_row("In position", in_pos_str)
    table.add_row("PnL", f"[{pnl_color}]{bot._pnl:+.2f} ({pnl_pct:+.2f}%)")
    return Panel(table, title="💰 Account", border_style="yellow")


def make_strategy_panel(bot: DashboardBot) -> Panel:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold magenta", justify="right")
    table.add_column(style="white")

    status = "RUNNING" if not bot._is_on_cooldown() else "🕐 COOLDOWN"
    status_color = "green" if not bot._is_on_cooldown() else "red"

    cooldown_str = "None"
    if bot._cooldown_until:
        rem = (bot._cooldown_until - datetime.now()).seconds // 60
        cooldown_str = f"{rem} min"

    table.add_row("Strategy", f"[bold]{Config.STRATEGY}")
    table.add_row("Status", f"[{status_color}]{status}")
    table.add_row("Signal", f"[bold]{bot._last_signal.value.upper()}")
    table.add_row("Cooldown", cooldown_str)
    table.add_row("Loss streak", f"{bot._consecutive_losses}/{Config.COOLDOWN_LOSSES}")
    table.add_row("Daily losses", f"{bot._daily_losses}/{Config.DAILY_LOSS_LIMIT}")
    return Panel(table, title="🎯 Strategy", border_style="magenta")


def make_log_panel(bot: DashboardBot) -> Panel:
    text = "\n".join(bot.log_lines) if bot.log_lines else "Waiting..."
    return Panel(text, title="📋 Log", border_style="dim")


def make_trade_panel(bot: DashboardBot) -> Panel:
    if not bot.trade_log:
        return Panel("No trades yet", title="📜 Trades", border_style="green")
    text = "\n".join(bot.trade_log)
    return Panel(text, title="📜 Trades", border_style="green")


def run_dashboard():
    bot = DashboardBot()
    interval = Config.CHECK_INTERVAL_MINUTES * 60

    bot.log(f"Bot starting on {Config.SYMBOL} | {Config.STRATEGY} | {Config.TIMEFRAME}")
    bot.log(f"Testnet: {Config.BINANCE_TESTNET}")

    def make_layout() -> Layout:
        layout = Layout()
        layout.split(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="footer", size=1),
        )
        layout["body"].split_row(
            Layout(name="left"),
            Layout(name="right"),
        )
        layout["body"]["left"].split(
            Layout(name="market"),
            Layout(name="account"),
        )
        layout["body"]["right"].split(
            Layout(name="strategy"),
            Layout(name="trades"),
        )
        return layout

    layout = make_layout()

    def render():
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        header_text = Text(
            f"  TRADING BOT  ●  {Config.SYMBOL}  |  {Config.STRATEGY}  |  {now}",
            style="bold white on dark_blue"
        )
        layout["header"].update(Panel(Align.center(header_text), style="dark_blue"))

        layout["market"].update(make_market_panel(bot))
        layout["account"].update(make_account_panel(bot))
        layout["strategy"].update(make_strategy_panel(bot))
        layout["trades"].update(make_trade_panel(bot))
        layout["footer"].update(Text(" Ctrl+C to stop ", style="dim"))

        return layout

    try:
        with Live(render(), refresh_per_second=1, screen=True) as live:
            last_cycle = 0
            while True:
                now_ts = time.time()
                if now_ts - last_cycle >= interval:
                    bot.run_cycle()
                    last_cycle = now_ts
                live.update(render())
                time.sleep(1)
    except KeyboardInterrupt:
        console.print("\n[bold red]Bot stopped[/bold red]")
