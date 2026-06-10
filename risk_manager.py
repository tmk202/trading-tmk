import logging
from typing import Optional

from config import Config

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, balance_usdt: float):
        self.balance = balance_usdt

    def calculate_position_size(self, price: float, stop_loss_pct: float = 0.02) -> float:
        risk_capital = self.balance * Config.RISK_PER_TRADE

        risk_per_unit = price * stop_loss_pct
        if risk_per_unit <= 0:
            return 0

        quantity = risk_capital / risk_per_unit
        max_quantity = self.balance * 0.5 / price

        position = min(quantity, max_quantity, Config.QUOTE_SIZE / price)

        logger.info(
            "Position sizing: balance=%.2f, risk_cap=%.2f, sl_pct=%.2f%%, size=%.6f",
            self.balance, risk_capital, stop_loss_pct * 100, position,
        )
        return position

    def get_stop_loss_price(self, entry_price: float, side: str, atr: float = None) -> Optional[float]:
        if side == "buy":
            return entry_price * (1 - Config.RISK_PER_TRADE)
        elif side == "sell":
            return entry_price * (1 + Config.RISK_PER_TRADE)
        return None

    def get_take_profit_price(self, entry_price: float, side: str, risk_reward: float = 2.0) -> Optional[float]:
        risk_amount = entry_price * Config.RISK_PER_TRADE
        reward_amount = risk_amount * risk_reward

        if side == "buy":
            return entry_price + reward_amount
        elif side == "sell":
            return entry_price - reward_amount
        return None
