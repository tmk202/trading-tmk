import logging
from datetime import datetime, timedelta

from config import Config
from mt5_connector import MT5Connector
from strategy import StrategyFactory, Signal
from notify import Notifier

logger = logging.getLogger(__name__)


class MT5Bot:
    def __init__(self):
        self.api = MT5Connector()
        self.strategy = StrategyFactory.create()
        self.notifier = Notifier()

        self._last_signal = Signal.HOLD
        self._in_position = False
        self._entry_price = 0
        self._consecutive_losses = 0
        self._daily_losses = 0
        self._cooldown_until: datetime = None

    def connect(self):
        if not self.api.initialize():
            raise ConnectionError("MT5 init failed")

    def check_position(self):
        self._in_position = self.api.has_position()
        return self._in_position

    def _is_on_cooldown(self):
        return self._cooldown_until and datetime.now() < self._cooldown_until

    def _check_daily_limit(self):
        return self._daily_losses >= Config.DAILY_LOSS_LIMIT

    def _record_loss(self):
        self._consecutive_losses += 1
        self._daily_losses += 1
        if self._consecutive_losses >= Config.COOLDOWN_LOSSES:
            self._cooldown_until = datetime.now() + timedelta(minutes=Config.COOLDOWN_MINUTES)

    def _record_win(self):
        self._consecutive_losses = 0

    def run_cycle(self):
        if self._is_on_cooldown() or self._check_daily_limit():
            return
        try:
            df = self.api.fetch_ohlcv(count=200)
            if df.empty or len(df) < 20:
                return
            price = self.api.get_price()
            balance = self.api.get_balance()
            logger.info("XAUUSD=%.2f | Balance=%.2f", price, balance)

            signal = self.strategy.analyze(df)
            self.check_position()

            if signal == Signal.BUY and not self._in_position:
                self._execute_buy(price)
            elif signal == Signal.SELL and self._in_position:
                self._execute_sell(price)

            self._last_signal = signal
        except Exception as e:
            logger.exception("Cycle error: %s", e)
        self._reset_daily()

    def _execute_buy(self, price):
        vol = self.api.calculate_volume()
        if vol <= 0:
            return
        self.api.create_market_order("buy", vol)
        self._in_position = True
        self._entry_price = price
        logger.info("BUY %.2f lot XAUUSD @ %.2f", vol, price)

    def _execute_sell(self, price):
        self.api.close_position()
        self._in_position = False
        self._entry_price = 0

    def _reset_daily(self):
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
        self.api.shutdown()
