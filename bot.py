import logging
from datetime import datetime, timedelta

from config import Config
from exchange import Exchange
from strategy import StrategyFactory, Signal, Scalper
from risk_manager import RiskManager
from notify import Notifier

logger = logging.getLogger(__name__)


class TradingBot:
    def __init__(self):
        self.exchange = Exchange()
        self.strategy = StrategyFactory.create()
        self.notifier = Notifier()
        self.symbol = Config.SYMBOL
        self._last_signal = Signal.HOLD
        self._in_position = False

        # Cooldown state
        self._consecutive_losses = 0
        self._daily_losses = 0
        self._cooldown_until: datetime = None
        self._last_signal_time: datetime = None
        self._current_trade_side: str = None

    def check_position(self):
        balance = self.exchange.fetch_balance()
        base_currency = self.symbol.split("/")[0]
        free = float(balance.get(base_currency, {}).get("free", 0))
        used = float(balance.get(base_currency, {}).get("used", 0))
        total = free + used

        threshold = self.exchange.get_position_size(0.5 * Config.QUOTE_SIZE)
        self._in_position = total >= threshold

        if self._in_position:
            logger.info("In position: %.6f %s (free=%.6f, used=%.6f)", total, base_currency, free, used)
        return self._in_position

    def _is_on_cooldown(self) -> bool:
        if self._cooldown_until and datetime.now() < self._cooldown_until:
            remaining = (self._cooldown_until - datetime.now()).seconds // 60
            logger.info("Cooldown active: %d min remaining", remaining)
            return True
        return False

    def _check_daily_limit(self) -> bool:
        if self._daily_losses >= Config.DAILY_LOSS_LIMIT:
            logger.warning("Daily loss limit hit (%d), stopping bot", self._daily_losses)
            self.notifier.send(
                f"<b>Bot stopped</b> — hit daily loss limit ({self._daily_losses}/{Config.DAILY_LOSS_LIMIT})"
            )
            return True
        return False

    def _record_loss(self):
        self._consecutive_losses += 1
        self._daily_losses += 1
        logger.warning("Loss recorded: %d consecutive, %d today",
                       self._consecutive_losses, self._daily_losses)

        if self._consecutive_losses >= Config.COOLDOWN_LOSSES:
            cooldown_min = Config.COOLDOWN_MINUTES
            self._cooldown_until = datetime.now() + timedelta(minutes=cooldown_min)
            logger.warning("Cooldown triggered: %d min after %d consecutive losses",
                          cooldown_min, self._consecutive_losses)
            self.notifier.send(
                f"<b>Cooldown</b> — {self._consecutive_losses} consecutive losses. "
                f"Pausing {cooldown_min} min."
            )

    def _record_win(self):
        self._consecutive_losses = 0
        logger.debug("Win recorded, losses reset")

    def run_cycle(self):
        logger.info("=== Cycle start ===")

        if self._is_on_cooldown() or self._check_daily_limit():
            logger.info("Bot paused (cooldown or daily limit)")
            return

        try:
            df = self.exchange.fetch_ohlcv(self.symbol, Config.TIMEFRAME, limit=50)
            ticker = self.exchange.get_ticker(self.symbol)
            current_price = ticker["last"]
            balance = self.exchange.fetch_balance()
            usdt_balance = float(balance.get("USDT", {}).get("free", 0))

            logger.info("Symbol=%s | Price=%.2f | USDT=%.2f | %s",
                        self.symbol, current_price, usdt_balance, Config.TIMEFRAME)

            # Multi-timeframe: fetch higher TF data for scalper
            higher_tf_df = None
            if isinstance(self.strategy, Scalper):
                higher_tf_df = self.exchange.fetch_ohlcv(
                    self.symbol, Config.HIGHER_TF, limit=50
                )
                logger.debug("Fetched HTF data: %s", Config.HIGHER_TF)

            signal = self.strategy.analyze(df, higher_tf_df)
            self.check_position()

            if signal == Signal.BUY and not self._in_position:
                self._execute_buy(current_price, usdt_balance)
            elif signal == Signal.SELL and self._in_position:
                self._execute_sell(current_price)
            else:
                logger.info("Signal: %s | In position: %s", signal.value, self._in_position)

            self._last_signal = signal

        except Exception as e:
            logger.exception("Cycle error: %s", e)
            self.notifier.send_error(str(e))

        finally:
            self._reset_daily_limit_if_new_day()

        logger.info("=== Cycle end ===\n")

    def _execute_buy(self, price: float, balance: float):
        amount = self.exchange.get_position_size(Config.QUOTE_SIZE)
        if amount <= 0:
            logger.warning("Position size too small, skip buy")
            return

        # Structure-based SL/TP cho scalper
        sl, tp = None, None
        if isinstance(self.strategy, Scalper):
            df = self.exchange.fetch_ohlcv(self.symbol, Config.TIMEFRAME, limit=50)
            sl, tp = self.strategy.calculate_sl_tp(df, "buy", price)
            logger.info("Scalper SL=%.2f TP=%.2f (RR=%.1f)", sl, tp,
                        (tp - price) / (price - sl) if sl else 0)

        order = self.exchange.create_market_order("buy", amount)
        self._in_position = True
        self._current_trade_side = "buy"

        self.notifier.send_signal(self.symbol, "buy", price, self.strategy.name)
        self.notifier.send_order("buy", self.symbol, amount, price,
                                 order.get("id", "") if order else "")
        if sl:
            logger.info("Stop-loss: %.2f | Take-profit: %.2f", sl, tp)

    def _execute_sell(self, price: float):
        base_currency = self.symbol.split("/")[0]
        balance = self.exchange.fetch_balance()
        amount = float(balance.get(base_currency, {}).get("free", 0))

        if amount <= 0:
            logger.warning("No %s balance to sell", base_currency)
            self._in_position = False
            return

        order = self.exchange.create_market_order("sell", amount)
        self._in_position = False

        self.notifier.send_signal(self.symbol, "sell", price, self.strategy.name)
        self.notifier.send_order("sell", self.symbol, amount, price,
                                 order.get("id", "") if order else "")

    def _reset_daily_limit_if_new_day(self):
        today = datetime.now().date()
        if not hasattr(self, "_day_date"):
            self._day_date = today
        if today != self._day_date:
            self._daily_losses = 0
            self._consecutive_losses = 0
            self._cooldown_until = None
            self._day_date = today
            logger.info("New day: reset loss counters")

    def run_once(self):
        logger.info("=== One-shot run ===")
        self.run_cycle()
