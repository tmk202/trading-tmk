import logging
from datetime import datetime, timedelta
from collections import deque

from config import Config
from ib_connector import IBConnector
from strategy import StrategyFactory, Signal, compute_ema, compute_atr, compute_adx
from notify import Notifier

logger = logging.getLogger(__name__)


class IBBot:
    def __init__(self):
        self.ib = IBConnector()
        self.strategy = StrategyFactory.create()
        self.notifier = Notifier()
        self.contract = None

        self._last_signal = Signal.HOLD
        self._in_position = False
        self._entry_price = 0

        self._consecutive_losses = 0
        self._daily_losses = 0
        self._cooldown_until: datetime = None

    def connect(self):
        if not self.ib.connect():
            raise ConnectionError("Cannot connect to IB Gateway/TWS")
        self.contract = self.ib._get_gold_contract()
        logger.info("IB Bot ready: %s", Config.IB_SYMBOL)

    def check_position(self):
        self._in_position = self.ib.has_position(self.contract)
        return self._in_position

    def _is_on_cooldown(self) -> bool:
        if self._cooldown_until and datetime.now() < self._cooldown_until:
            return True
        return False

    def _check_daily_limit(self) -> bool:
        return self._daily_losses >= Config.DAILY_LOSS_LIMIT

    def _record_loss(self):
        self._consecutive_losses += 1
        self._daily_losses += 1
        logger.warning("Loss: %d consecutive, %d today", self._consecutive_losses, self._daily_losses)
        if self._consecutive_losses >= Config.COOLDOWN_LOSSES:
            self._cooldown_until = datetime.now() + timedelta(minutes=Config.COOLDOWN_MINUTES)
            self.notifier.send(f"<b>IB Gold Cooldown</b> — {self._consecutive_losses} losses, pause {Config.COOLDOWN_MINUTES}m")

    def _record_win(self):
        self._consecutive_losses = 0

    def run_cycle(self):
        logger.info("=== IB Gold cycle ===")

        if self._is_on_cooldown() or self._check_daily_limit():
            return

        try:
            df = self.ib.fetch_ohlcv(self.contract, days=7)
            if df.empty or len(df) < 30:
                logger.warning("Not enough data")
                return

            price = self.ib.get_price(self.contract)
            balance = self.ib.get_balance()

            logger.info("XAU=%.2f | Balance=%.2f | TF=%s", price, balance, Config.IB_TIMEFRAME)

            signal = self.strategy.analyze(df)
            self.check_position()

            if signal == Signal.BUY and not self._in_position:
                self._execute_buy(price)
            elif signal == Signal.SELL and self._in_position:
                self._execute_sell(price)
            else:
                logger.info("Signal: %s | Pos: %s", signal.value, self._in_position)

            self._last_signal = signal

        except Exception as e:
            logger.exception("Cycle error: %s", e)
            self.notifier.send_error(str(e))

        self._reset_daily_if_new_day()

    def _execute_buy(self, price: float):
        qty = self.ib.calculate_quantity()
        if qty <= 0:
            return
        trade = self.ib.create_market_order("buy", qty, self.contract)
        self._in_position = True
        self._entry_price = price
        logger.info("BUY %d MGC @ %.2f", qty, price)

    def _execute_sell(self, price: float):
        self.ib.close_position(self.contract)
        self._in_position = False
        self._entry_price = 0
        logger.info("SELL @ %.2f", price)

    def _reset_daily_if_new_day(self):
        today = datetime.now().date()
        if not hasattr(self, "_day_date"):
            self._day_date = today
        if today != self._day_date:
            self._daily_losses = 0
            self._consecutive_losses = 0
            self._cooldown_until = None
            self._day_date = today

    def run_once(self):
        self.connect()
        self.run_cycle()
