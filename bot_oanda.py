import logging
from datetime import datetime, timedelta

from config import Config
from oanda import Oanda
from strategy import StrategyFactory, Signal, Scalper
from notify import Notifier

logger = logging.getLogger(__name__)


class OandaBot:
    def __init__(self):
        self.oanda = Oanda()
        self.strategy = StrategyFactory.create()
        self.notifier = Notifier()
        self.symbol = Config.OANDA_SYMBOL

        self._last_signal = Signal.HOLD
        self._in_position = False
        self._entry_price = 0

        self._consecutive_losses = 0
        self._daily_losses = 0
        self._cooldown_until: datetime = None

    def check_position(self):
        self._in_position = self.oanda.has_open_position(self.symbol)
        return self._in_position

    def _is_on_cooldown(self) -> bool:
        if self._cooldown_until and datetime.now() < self._cooldown_until:
            rem = (self._cooldown_until - datetime.now()).seconds // 60
            logger.info("Cooldown: %d min", rem)
            return True
        return False

    def _check_daily_limit(self) -> bool:
        return self._daily_losses >= Config.DAILY_LOSS_LIMIT

    def _record_loss(self):
        self._consecutive_losses += 1
        self._daily_losses += 1
        logger.warning("Loss: %d consecutive, %d today",
                       self._consecutive_losses, self._daily_losses)
        if self._consecutive_losses >= Config.COOLDOWN_LOSSES:
            self._cooldown_until = datetime.now() + timedelta(minutes=Config.COOLDOWN_MINUTES)
            self.notifier.send(
                f"<b>OANDA Cooldown</b> — {self._consecutive_losses} consecutive losses. "
                f"Pausing {Config.COOLDOWN_MINUTES} min."
            )

    def _record_win(self):
        self._consecutive_losses = 0

    def run_cycle(self):
        logger.info("=== OANDA cycle ===")

        if self._is_on_cooldown() or self._check_daily_limit():
            return

        try:
            df = self.oanda.fetch_ohlcv(count=50)
            price = self.oanda.get_mid_price()
            balance = self.oanda.get_balance()

            logger.info("XAU/USD=%.2f | Balance=%.2f | %s",
                        price, balance, Config.OANDA_TIMEFRAME)

            higher_tf_df = None
            if Config.HIGHER_TF:
                higher_tf_df = self.oanda.fetch_ohlcv(
                    timeframe=Config.HIGHER_TF, count=50
                )

            signal = self.strategy.analyze(df, higher_tf_df)
            self.check_position()

            if signal == Signal.BUY and not self._in_position:
                self._execute_buy(price)
            elif signal == Signal.SELL and self._in_position:
                self._execute_sell(price)
            else:
                logger.info("Signal: %s | In pos: %s", signal.value, self._in_position)

            self._last_signal = signal

        except Exception as e:
            logger.exception("Cycle error: %s", e)
            self.notifier.send_error(str(e))

        self._reset_daily_if_new_day()

    def _execute_buy(self, price: float):
        units = self.oanda.calculate_units()
        if units <= 0:
            return

        order = self.oanda.create_market_order("buy", units)
        self._in_position = True
        self._entry_price = price
        logger.info("BUY %s units @ %.2f", units, price)

    def _execute_sell(self, price: float):
        trades = self.oanda.fetch_open_trades()
        for t in trades:
            if t["instrument"] == self.symbol:
                self.oanda.close_trade(t["id"])
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
        logger.info("=== OANDA one-shot ===")
        self.run_cycle()
