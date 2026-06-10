import logging
from enum import Enum
from typing import Optional

import pandas as pd
import numpy as np

from config import Config

logger = logging.getLogger(__name__)


class Signal(Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class BaseStrategy:
    def __init__(self):
        self.name = "base"

    def analyze(self, df: pd.DataFrame, *args, **kwargs) -> Signal:
        raise NotImplementedError

    def get_params(self) -> dict:
        return {}


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = (-delta.where(delta < 0, 0))
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = compute_ema(series, fast)
    ema_slow = compute_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = compute_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def compute_bb(series: pd.Series, period: int = 20, std_mult: float = 2.0):
    sma = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = sma + std_mult * std
    lower = sma - std_mult * std
    return upper, sma, lower


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    down_move = low.diff()

    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move.abs(), 0), index=df.index)

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)

    atr_ = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.rolling(period).mean() / atr_)
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr_)
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx = dx.rolling(period).mean()
    return adx, plus_di, minus_di


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ─────────────────────────────────────────────
# 1. EMA CROSSOVER + TREND FILTER
# ─────────────────────────────────────────────
class EmaCross(BaseStrategy):
    def __init__(self, fast: int = 9, slow: int = 21, trend: int = 50):
        super().__init__()
        self.name = "ema_cross"
        self.fast = fast
        self.slow = slow
        self.trend = trend

    def analyze(self, df: pd.DataFrame, **kwargs) -> Signal:
        if len(df) < self.trend + 1:
            return Signal.HOLD

        ema_fast = compute_ema(df["close"], self.fast)
        ema_slow = compute_ema(df["close"], self.slow)
        ema_trend = compute_ema(df["close"], self.trend)

        price = df["close"].iloc[-1]
        uptrend = price > ema_trend.iloc[-1]
        downtrend = price < ema_trend.iloc[-1]

        prev_fast = ema_fast.iloc[-2]
        prev_slow = ema_slow.iloc[-2]
        curr_fast = ema_fast.iloc[-1]
        curr_slow = ema_slow.iloc[-1]

        if uptrend and prev_fast <= prev_slow and curr_fast > curr_slow:
            logger.info("[%s] BUY: EMA%d cross above EMA%d, trend up", self.name, self.fast, self.slow)
            return Signal.BUY

        if downtrend and prev_fast >= prev_slow and curr_fast < curr_slow:
            logger.info("[%s] SELL: EMA%d cross below EMA%d, trend down", self.name, self.fast, self.slow)
            return Signal.SELL

        return Signal.HOLD

    def get_params(self) -> dict:
        return {"fast": self.fast, "slow": self.slow, "trend": self.trend}


# ─────────────────────────────────────────────
# 2. RSI + EMA TREND FILTER
# ─────────────────────────────────────────────
class RsiEma(BaseStrategy):
    def __init__(self, rsi_period: int = 14, oversold: int = 35, overbought: int = 65, ema_period: int = 50):
        super().__init__()
        self.name = "rsi_ema"
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought
        self.ema_period = ema_period

    def analyze(self, df: pd.DataFrame, **kwargs) -> Signal:
        if len(df) < self.ema_period + self.rsi_period:
            return Signal.HOLD

        rsi = compute_rsi(df["close"], self.rsi_period)
        ema_trend = compute_ema(df["close"], self.ema_period)

        price = df["close"].iloc[-1]
        curr_rsi = rsi.iloc[-1]
        prev_rsi = rsi.iloc[-2]
        uptrend = price > ema_trend.iloc[-1]
        downtrend = price < ema_trend.iloc[-1]

        if uptrend and prev_rsi <= self.oversold and curr_rsi > self.oversold:
            logger.info("[%s] BUY: RSI %.1f oversold + uptrend", self.name, curr_rsi)
            return Signal.BUY

        if downtrend and prev_rsi >= self.overbought and curr_rsi < self.overbought:
            logger.info("[%s] SELL: RSI %.1f overbought + downtrend", self.name, curr_rsi)
            return Signal.SELL

        return Signal.HOLD

    def get_params(self) -> dict:
        return {"rsi_period": self.rsi_period, "oversold": self.oversold, "overbought": self.overbought, "ema": self.ema_period}


# ─────────────────────────────────────────────
# 3. MACD CROSSOVER
# ─────────────────────────────────────────────
class MacdCross(BaseStrategy):
    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        super().__init__()
        self.name = "macd_cross"
        self.fast = fast
        self.slow = slow
        self.signal = signal

    def analyze(self, df: pd.DataFrame, **kwargs) -> Signal:
        if len(df) < self.slow + self.signal + 1:
            return Signal.HOLD

        macd_line, signal_line, hist = compute_macd(df["close"], self.fast, self.slow, self.signal)

        prev_macd = macd_line.iloc[-2]
        prev_sig = signal_line.iloc[-2]
        curr_macd = macd_line.iloc[-1]
        curr_sig = signal_line.iloc[-1]

        if prev_macd <= prev_sig and curr_macd > curr_sig:
            logger.info("[%s] BUY: MACD cross above signal", self.name)
            return Signal.BUY

        if prev_macd >= prev_sig and curr_macd < curr_sig:
            logger.info("[%s] SELL: MACD cross below signal", self.name)
            return Signal.SELL

        return Signal.HOLD

    def get_params(self) -> dict:
        return {"fast": self.fast, "slow": self.slow, "signal": self.signal}


# ─────────────────────────────────────────────
# 4. BOLLINGER BANDS + RSI (Mean Reversion)
# ─────────────────────────────────────────────
class BbRsi(BaseStrategy):
    def __init__(self, bb_period: int = 20, bb_std: float = 2.0, rsi_period: int = 14, rsi_threshold: int = 30):
        super().__init__()
        self.name = "bb_rsi"
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_period = rsi_period
        self.rsi_threshold = rsi_threshold

    def analyze(self, df: pd.DataFrame, **kwargs) -> Signal:
        if len(df) < self.bb_period + self.rsi_period:
            return Signal.HOLD

        upper, mid, lower = compute_bb(df["close"], self.bb_period, self.bb_std)
        rsi = compute_rsi(df["close"], self.rsi_period)

        price = df["close"].iloc[-1]
        curr_rsi = rsi.iloc[-1]
        prev_rsi = rsi.iloc[-2]

        above_upper = price >= upper.iloc[-1]
        below_lower = price <= lower.iloc[-1]

        if below_lower and curr_rsi < self.rsi_threshold and prev_rsi <= curr_rsi:
            logger.info("[%s] BUY: touch lower BB (%.2f) + RSI %.1f", self.name, lower.iloc[-1], curr_rsi)
            return Signal.BUY

        if above_upper and curr_rsi > (100 - self.rsi_threshold) and prev_rsi >= curr_rsi:
            logger.info("[%s] SELL: touch upper BB (%.2f) + RSI %.1f", self.name, upper.iloc[-1], curr_rsi)
            return Signal.SELL

        return Signal.HOLD

    def get_params(self) -> dict:
        return {"bb_period": self.bb_period, "bb_std": self.bb_std, "rsi_period": self.rsi_period}


# ─────────────────────────────────────────────
# 5. SUPERTREND (ATR-based)
# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# 6. EMA50/EMA200 TREND FILTER (The trend is your friend)
# ─────────────────────────────────────────────
class TrendFilter(BaseStrategy):
    def __init__(self, fast_ema: int = 50, slow_ema: int = 200):
        super().__init__()
        self.name = "trend_filter"
        self.fast_ema = fast_ema
        self.slow_ema = slow_ema

    def analyze(self, df: pd.DataFrame, **kwargs) -> Signal:
        if len(df) < self.slow_ema + 1:
            return Signal.HOLD

        fast = compute_ema(df["close"], self.fast_ema)
        slow = compute_ema(df["close"], self.slow_ema)
        price = df["close"].iloc[-1]

        uptrend = fast.iloc[-1] > slow.iloc[-1]
        downtrend = fast.iloc[-1] < slow.iloc[-1]

        # Crossover detection
        prev_fast = fast.iloc[-2]
        prev_slow = slow.iloc[-2]

        if prev_fast <= prev_slow and fast.iloc[-1] > slow.iloc[-1]:
            logger.info("[%s] BUY: EMA50 (%.0f) crossed above EMA200 (%.0f), trend up",
                        self.name, fast.iloc[-1], slow.iloc[-1])
            return Signal.BUY

        if prev_fast >= prev_slow and fast.iloc[-1] < slow.iloc[-1]:
            logger.info("[%s] SELL: EMA50 (%.0f) crossed below EMA200 (%.0f), trend down",
                        self.name, fast.iloc[-1], slow.iloc[-1])
            return Signal.SELL

        return Signal.HOLD

    def get_params(self) -> dict:
        return {"fast_ema": self.fast_ema, "slow_ema": self.slow_ema}


# ─────────────────────────────────────────────
# 7. SCALPER (Price Action + Volume + MTF Trend + Distance Filter)
# ─────────────────────────────────────────────
class Scalper(BaseStrategy):
    def __init__(self):
        super().__init__()
        self.name = "scalper"

    def _compute_vwap(self, df: pd.DataFrame) -> pd.Series:
        typical = (df["high"] + df["low"] + df["close"]) / 3
        cum = (typical * df["volume"]).cumsum()
        vol_cum = df["volume"].cumsum()
        return cum / vol_cum.replace(0, pd.NA)

    def analyze(self, df: pd.DataFrame, higher_tf_df: pd.DataFrame = None) -> Signal:
        min_bars = Config.SCALPER_LOOKBACK_BARS + 25
        if len(df) < min_bars:
            return Signal.HOLD

        close = df["close"]
        ema20 = compute_ema(close, 20)
        vwap = self._compute_vwap(df)
        vol_sma = df["volume"].rolling(20).mean()
        price = close.iloc[-1]
        prev_price = close.iloc[-2]

        recent_high = close.iloc[-Config.SCALPER_LOOKBACK_BARS:-1].max()
        recent_low = close.iloc[-Config.SCALPER_LOOKBACK_BARS:-1].min()

        # Volume spike
        vol_spike = df["volume"].iloc[-1] > vol_sma.iloc[-1] * Config.SCALPER_VOL_MULT

        # EMA20 trend: compare current slope
        ema_slope = ema20.iloc[-1] - ema20.iloc[-5]
        uptrend = ema_slope > 0
        downtrend = ema_slope < 0

        # VWAP filter
        above_vwap = price > vwap.iloc[-1]
        below_vwap = price < vwap.iloc[-1]

        # ── MTF filter: check higher timeframe EMA20 ──
        htf_ok = True
        if higher_tf_df is not None and len(higher_tf_df) > 25:
            htf_close = higher_tf_df["close"]
            htf_ema20 = compute_ema(htf_close, 20)
            htf_slope = htf_ema20.iloc[-1] - htf_ema20.iloc[-5]
            if htf_slope > 0:
                htf_trend = "up"
            elif htf_slope < 0:
                htf_trend = "down"
            else:
                htf_trend = "flat"

            if uptrend and htf_trend == "down":
                htf_ok = False
                logger.debug("[scalper] MTF reject BUY: HTF trend is down")
            elif downtrend and htf_trend == "up":
                htf_ok = False
                logger.debug("[scalper] MTF reject SELL: HTF trend is up")

        if not htf_ok:
            return Signal.HOLD

        # ── EMA distance filter: không vào nếu giá quá xa EMA ──
        ema_dist_pct = abs(price - ema20.iloc[-1]) / ema20.iloc[-1] * 100
        if ema_dist_pct > Config.SCALPER_MAX_EMA_DIST_PCT:
            logger.debug("[scalper] EMA distance %.2f%% > %.2f%%, skip",
                         ema_dist_pct, Config.SCALPER_MAX_EMA_DIST_PCT)
            return Signal.HOLD

        # ── BUY ──
        if (price > recent_high and prev_price <= recent_high
                and vol_spike and uptrend and above_vwap):
            logger.info("[scalper] BUY: breakout %.2f + volume + uptrend + HTF ok",
                        recent_high)
            return Signal.BUY

        # ── SELL ──
        if (price < recent_low and prev_price >= recent_low
                and vol_spike and downtrend and below_vwap):
            logger.info("[scalper] SELL: breakdown %.2f + volume + downtrend + HTF ok",
                        recent_low)
            return Signal.SELL

        return Signal.HOLD

    def calculate_sl_tp(self, df: pd.DataFrame, side: str, entry: float) -> tuple:
        atr = compute_atr(df, 14).iloc[-1]
        lookback = Config.SCALPER_LOOKBACK_BARS

        if side == "buy":
            breakout_level = df["close"].iloc[-lookback:-1].max()
            sl = breakout_level - atr * 0.3
            if entry - sl <= 0:
                sl = entry - atr * 0.5
            tp = entry + (entry - sl) * Config.SCALPER_TP_RR
        else:
            breakout_level = df["close"].iloc[-lookback:-1].min()
            sl = breakout_level + atr * 0.3
            if sl - entry <= 0:
                sl = entry + atr * 0.5
            tp = entry - (sl - entry) * Config.SCALPER_TP_RR

        return round(sl, 2), round(tp, 2)

    def get_params(self) -> dict:
        return {"vol_mult": Config.SCALPER_VOL_MULT,
                "max_ema_dist": Config.SCALPER_MAX_EMA_DIST_PCT,
                "lookback": Config.SCALPER_LOOKBACK_BARS,
                "higher_tf": Config.HIGHER_TF}


# ─────────────────────────────────────────────
# V2: Breakout + MTF + EMA Slope (bỏ volume spike)
# ─────────────────────────────────────────────
class ScalperV2(BaseStrategy):
    def __init__(self):
        super().__init__()
        self.name = "scalper_v2"

    def _compute_vwap(self, df: pd.DataFrame) -> pd.Series:
        typical = (df["high"] + df["low"] + df["close"]) / 3
        cum = (typical * df["volume"]).cumsum()
        vol_cum = df["volume"].cumsum()
        return cum / vol_cum.replace(0, pd.NA)

    def _ema_slope(self, series: pd.Series, lookback: int = 5) -> float:
        return (series.iloc[-1] - series.iloc[-lookback]) / series.iloc[-lookback] * 100

    def analyze(self, df: pd.DataFrame, higher_tf_df: pd.DataFrame = None) -> Signal:
        min_bars = Config.SCALPER_LOOKBACK_BARS + 30
        if len(df) < min_bars:
            return Signal.HOLD

        close = df["close"]
        ema20 = compute_ema(close, 20)
        vwap = self._compute_vwap(df)
        price = close.iloc[-1]
        prev_price = close.iloc[-2]

        recent_high = close.iloc[-Config.SCALPER_LOOKBACK_BARS:-1].max()
        recent_low = close.iloc[-Config.SCALPER_LOOKBACK_BARS:-1].min()

        above_vwap = price > vwap.iloc[-1]
        below_vwap = price < vwap.iloc[-1]

        # EMA slope thay vì chỉ > 0 (dương = lên, âm = xuống)
        tf_slope = self._ema_slope(ema20, 5)
        strong_uptrend = tf_slope > 0.03
        strong_downtrend = tf_slope < -0.03

        # ── MTF ──
        htf_ok = True
        short_ok = True
        if higher_tf_df is not None and len(higher_tf_df) > 25:
            htf_ema20 = compute_ema(higher_tf_df["close"], 20)
            htf_slope = self._ema_slope(htf_ema20, 5)

            htf_long = strong_uptrend and htf_slope > 0.02
            htf_short = strong_downtrend and htf_slope < -0.02

            if not htf_long:
                htf_ok = False
            if not htf_short:
                short_ok = False

        if not htf_ok and not short_ok:
            return Signal.HOLD

        # EMA distance filter
        ema_dist_pct = abs(price - ema20.iloc[-1]) / ema20.iloc[-1] * 100
        if ema_dist_pct > Config.SCALPER_MAX_EMA_DIST_PCT:
            return Signal.HOLD

        # BUY — không cần volume spike
        if (price > recent_high and prev_price <= recent_high
                and strong_uptrend and above_vwap and htf_ok):
            logger.info("[scalper_v2] BUY: breakout %.2f + slope %.3f%% + HTF ok", recent_high, tf_slope)
            return Signal.BUY

        # SELL
        if (price < recent_low and prev_price >= recent_low
                and strong_downtrend and below_vwap and short_ok):
            logger.info("[scalper_v2] SELL: breakdown %.2f + slope %.3f%% + HTF ok", recent_low, tf_slope)
            return Signal.SELL

        return Signal.HOLD

    def calculate_sl_tp(self, df: pd.DataFrame, side: str, entry: float) -> tuple:
        atr = compute_atr(df, 14).iloc[-1]
        lookback = Config.SCALPER_LOOKBACK_BARS
        if side == "buy":
            bl = df["close"].iloc[-lookback:-1].max()
            sl = bl - atr * 0.3
            if entry - sl <= 0:
                sl = entry - atr * 0.5
            tp = entry + (entry - sl) * Config.SCALPER_TP_RR
        else:
            bl = df["close"].iloc[-lookback:-1].min()
            sl = bl + atr * 0.3
            if sl - entry <= 0:
                sl = entry + atr * 0.5
            tp = entry - (sl - entry) * Config.SCALPER_TP_RR
        return round(sl, 2), round(tp, 2)

    def get_params(self) -> dict:
        return {"ema_slope_min": "0.03%", "lookback": Config.SCALPER_LOOKBACK_BARS}


# ─────────────────────────────────────────────
# V3: Breakout + MTF + EMA Slope + ADX > 20
# ─────────────────────────────────────────────
class ScalperV3(BaseStrategy):
    def __init__(self):
        super().__init__()
        self.name = "scalper_v3"

    def _compute_vwap(self, df: pd.DataFrame) -> pd.Series:
        typical = (df["high"] + df["low"] + df["close"]) / 3
        cum = (typical * df["volume"]).cumsum()
        vol_cum = df["volume"].cumsum()
        return cum / vol_cum.replace(0, pd.NA)

    def _ema_slope(self, series: pd.Series, lookback: int = 5) -> float:
        return (series.iloc[-1] - series.iloc[-lookback]) / series.iloc[-lookback] * 100

    def analyze(self, df: pd.DataFrame, higher_tf_df: pd.DataFrame = None) -> Signal:
        min_bars = Config.SCALPER_LOOKBACK_BARS + 40
        if len(df) < min_bars:
            return Signal.HOLD

        close = df["close"]
        ema20 = compute_ema(close, 20)
        vwap = self._compute_vwap(df)
        price = close.iloc[-1]
        prev_price = close.iloc[-2]

        recent_high = close.iloc[-Config.SCALPER_LOOKBACK_BARS:-1].max()
        recent_low = close.iloc[-Config.SCALPER_LOOKBACK_BARS:-1].min()

        above_vwap = price > vwap.iloc[-1]
        below_vwap = price < vwap.iloc[-1]

        # ── ADX filter ──
        adx, plus_di, minus_di = compute_adx(df, 14)
        adx_val = adx.iloc[-1]
        if adx_val < 20:
            logger.debug("[scalper_v3] ADX=%.1f < 20, skip", adx_val)
            return Signal.HOLD

        # EMA slope (dương = lên, âm = xuống)
        tf_slope = self._ema_slope(ema20, 5)
        strong_uptrend = tf_slope > 0.03
        strong_downtrend = tf_slope < -0.03

        # ── MTF ──
        htf_ok = True
        short_ok = True
        if higher_tf_df is not None and len(higher_tf_df) > 25:
            htf_ema20 = compute_ema(higher_tf_df["close"], 20)
            htf_adx, _, _ = compute_adx(higher_tf_df, 14)
            htf_slope = self._ema_slope(htf_ema20, 5)
            htf_adx_ok = htf_adx.iloc[-1] > 20 if len(htf_adx) > 0 else True

            htf_long = strong_uptrend and htf_slope > 0.02 and htf_adx_ok
            htf_short = strong_downtrend and htf_slope < -0.02 and htf_adx_ok

            if not htf_long:
                htf_ok = False
            if not htf_short:
                short_ok = False

        if not htf_ok and not short_ok:
            return Signal.HOLD

        # EMA distance
        ema_dist = abs(price - ema20.iloc[-1]) / ema20.iloc[-1] * 100
        if ema_dist > Config.SCALPER_MAX_EMA_DIST_PCT:
            return Signal.HOLD

        # BUY
        if (price > recent_high and prev_price <= recent_high
                and strong_uptrend and above_vwap and htf_ok):
            logger.info("[scalper_v3] BUY: breakout %.2f + ADX %.1f + slope %.3f%%", recent_high, adx_val, tf_slope)
            return Signal.BUY

        # SELL
        if (price < recent_low and prev_price >= recent_low
                and strong_downtrend and below_vwap and short_ok):
            logger.info("[scalper_v3] SELL: breakdown %.2f + ADX %.1f + slope %.3f%%", recent_low, adx_val, tf_slope)
            return Signal.SELL

        return Signal.HOLD

    def calculate_sl_tp(self, df: pd.DataFrame, side: str, entry: float) -> tuple:
        atr = compute_atr(df, 14).iloc[-1]
        lookback = Config.SCALPER_LOOKBACK_BARS
        if side == "buy":
            bl = df["close"].iloc[-lookback:-1].max()
            sl = bl - atr * 0.3
            if entry - sl <= 0:
                sl = entry - atr * 0.5
            tp = entry + (entry - sl) * Config.SCALPER_TP_RR
        else:
            bl = df["close"].iloc[-lookback:-1].min()
            sl = bl + atr * 0.3
            if sl - entry <= 0:
                sl = entry + atr * 0.5
            tp = entry - (sl - entry) * Config.SCALPER_TP_RR
        return round(sl, 2), round(tp, 2)

    def get_params(self) -> dict:
        return {"adx_min": 20, "ema_slope_min": "0.03%"}


# ─────────────────────────────────────────────
# V4: Retest Confirmation (thay vì buy breakout ngay)
# ─────────────────────────────────────────────
class ScalperV4(BaseStrategy):
    def __init__(self):
        super().__init__()
        self.name = "scalper_v4"

    def _compute_vwap(self, df: pd.DataFrame) -> pd.Series:
        typical = (df["high"] + df["low"] + df["close"]) / 3
        cum = (typical * df["volume"]).cumsum()
        vol_cum = df["volume"].cumsum()
        return cum / vol_cum.replace(0, pd.NA)

    def _ema_slope(self, series: pd.Series, lookback: int = 5) -> float:
        return (series.iloc[-1] - series.iloc[-lookback]) / series.iloc[-lookback] * 100

    def analyze(self, df: pd.DataFrame, higher_tf_df: pd.DataFrame = None) -> Signal:
        if len(df) < 60:
            return Signal.HOLD

        close = df["close"]
        high = df["high"]
        low = df["low"]
        ema20 = compute_ema(close, 20)
        vwap = self._compute_vwap(df)
        price = close.iloc[-1]
        atr = compute_atr(df, 14)
        atr_val = atr.iloc[-1]

        # ── 1. Donchian range filter ──
        range_48_high = high.iloc[-48:].max()
        range_48_low = low.iloc[-48:].min()
        range_width = range_48_high - range_48_low
        if range_width < atr_val * 3:
            logger.debug("[scalper_v4] range=%.1f < ATR*3 (%.1f), skip chop", range_width, atr_val * 3)
            return Signal.HOLD

        # ── 2. EMA slope + VWAP filters ──
        tf_slope = self._ema_slope(ema20, 5)
        strong_uptrend = tf_slope > 0.03
        strong_downtrend = tf_slope < -0.03
        above_vwap = price > vwap.iloc[-1]
        below_vwap = price < vwap.iloc[-1]

        # ── 3. MTF filter ──
        htf_long_ok = True
        htf_short_ok = True
        if higher_tf_df is not None and len(higher_tf_df) > 25:
            htf_ema20 = compute_ema(higher_tf_df["close"], 20)
            htf_slope = self._ema_slope(htf_ema20, 5)
            htf_long_ok = strong_uptrend and htf_slope > 0.02
            htf_short_ok = strong_downtrend and htf_slope < -0.02

        # ── 4. EMA distance ──
        ema_dist = abs(price - ema20.iloc[-1]) / ema20.iloc[-1] * 100
        if ema_dist > Config.SCALPER_MAX_EMA_DIST_PCT:
            return Signal.HOLD

        # ── 5. LONG: Retest pattern ──
        lookback = Config.SCALPER_LOOKBACK_BARS
        recent_high = close.iloc[-lookback:-1].max()

        if strong_uptrend and above_vwap and htf_long_ok and price > recent_high * 0.99:
            # Tìm breakout trong quá khứ (2-10 bars trước)
            for i in range(2, min(10, len(df) - 2)):
                breakout_level = close.iloc[-(lookback + i):-i].max()
                broke_out = (close.iloc[-i] > breakout_level
                             and close.iloc[-i - 1] <= breakout_level)

                if not broke_out:
                    continue

                # Đã có breakout tại bar -i
                # Kiểm tra pullback giữ vùng + volume giảm
                prices_after = close.iloc[-(i - 1):]
                vol_after = df["volume"].iloc[-(i - 1):]
                lowest_after = prices_after.min()
                vol_sma = df["volume"].rolling(20).mean()

                pullback_held = lowest_after >= breakout_level * 0.995
                vol_fading = vol_after.max() < vol_sma.iloc[-1] * 0.9 if len(vol_after) > 1 else True

                if pullback_held and vol_fading:
                    logger.info("[scalper_v4] BUY: breakout %d bars ago + retest held @ %.2f",
                                i, price)
                    return Signal.BUY

        # ── 6. SHORT: Retest breakdown ──
        recent_low = close.iloc[-lookback:-1].min()

        if strong_downtrend and below_vwap and htf_short_ok and price < recent_low * 1.01:
            for i in range(2, min(10, len(df) - 2)):
                breakdown_level = close.iloc[-(lookback + i):-i].min()
                broke_down = (close.iloc[-i] < breakdown_level
                              and close.iloc[-i - 1] >= breakdown_level)

                if not broke_down:
                    continue

                prices_after = close.iloc[-(i - 1):]
                highest_after = prices_after.max()
                vol_sma = df["volume"].rolling(20).mean()
                vol_after = df["volume"].iloc[-(i - 1):]

                pullback_held = highest_after <= breakdown_level * 1.005
                vol_fading = vol_after.max() < vol_sma.iloc[-1] * 0.9 if len(vol_after) > 1 else True

                if pullback_held and vol_fading:
                    logger.info("[scalper_v4] SELL: breakdown %d bars ago + retest held @ %.2f",
                                i, price)
                    return Signal.SELL

        return Signal.HOLD

    def calculate_sl_tp(self, df: pd.DataFrame, side: str, entry: float) -> tuple:
        atr = compute_atr(df, 14).iloc[-1]
        lookback = Config.SCALPER_LOOKBACK_BARS
        if side == "buy":
            bl = df["close"].iloc[-lookback:-1].max()
            sl = bl - atr * 0.3
            if entry - sl <= 0:
                sl = entry - atr * 0.5
            tp = entry + (entry - sl) * Config.SCALPER_TP_RR
        else:
            bl = df["close"].iloc[-lookback:-1].min()
            sl = bl + atr * 0.3
            if sl - entry <= 0:
                sl = entry + atr * 0.5
            tp = entry - (sl - entry) * Config.SCALPER_TP_RR
        return round(sl, 2), round(tp, 2)

    def get_params(self) -> dict:
        return {"type": "retest", "range_filter": "ATR*3", "lookback": Config.SCALPER_LOOKBACK_BARS}


# ─────────────────────────────────────────────
# V5: Regime-aware Long + Short
# Dùng 1h EMA50/EMA200 → chọn chiều giao dịch
# ─────────────────────────────────────────────
class ScalperV5(BaseStrategy):
    def __init__(self):
        super().__init__()
        self.name = "scalper_v5"

    def _compute_vwap(self, df: pd.DataFrame) -> pd.Series:
        typical = (df["high"] + df["low"] + df["close"]) / 3
        cum = (typical * df["volume"]).cumsum()
        vol_cum = df["volume"].cumsum()
        return cum / vol_cum.replace(0, pd.NA)

    def _classify_regime(self, htf_df: pd.DataFrame) -> str:
        c = htf_df["close"]
        ema20 = compute_ema(c, 20)
        slope = (ema20.iloc[-1] - ema20.iloc[-24]) / ema20.iloc[-24] * 100
        adx, plus_di, minus_di = compute_adx(htf_df, 14)
        adx_val = adx.iloc[-1] if len(adx) > 0 else 0

        if slope > 0.05 and adx_val > 20 and plus_di.iloc[-1] > minus_di.iloc[-1]:
            return "UPTREND"
        elif slope < -0.05 and adx_val > 20 and minus_di.iloc[-1] > plus_di.iloc[-1]:
            return "DOWNTREND"
        return "RANGE"

    def analyze(self, df: pd.DataFrame, higher_tf_df: pd.DataFrame = None) -> Signal:
        if higher_tf_df is None or len(higher_tf_df) < 50:
            return Signal.HOLD

        regime = self._classify_regime(higher_tf_df)
        if regime == "RANGE":
            return Signal.HOLD

        close = df["close"]
        ema20 = compute_ema(close, 20)
        vwap = self._compute_vwap(df)
        price = close.iloc[-1]

        tf_slope = (ema20.iloc[-1] - ema20.iloc[-5]) / ema20.iloc[-5] * 100
        above_vwap = price > vwap.iloc[-1]
        below_vwap = price < vwap.iloc[-1]
        ema_dist = abs(price - ema20.iloc[-1]) / ema20.iloc[-1] * 100
        if ema_dist > Config.SCALPER_MAX_EMA_DIST_PCT:
            return Signal.HOLD

        adx, plus_di, minus_di = compute_adx(df, 14)
        adx_val = adx.iloc[-1]

        lookback = Config.SCALPER_LOOKBACK_BARS
        recent_high = close.iloc[-lookback:-1].max()
        recent_low = close.iloc[-lookback:-1].min()

        if regime == "UPTREND":
            if (price > recent_high
                    and close.iloc[-2] <= recent_high
                    and tf_slope > 0.03
                    and above_vwap
                    and adx_val > 20):
                logger.info("[scalper_v5] BUY: %s ADX=%.1f slope=%.2f%%",
                            regime, adx_val, tf_slope)
                return Signal.BUY

        elif regime == "DOWNTREND":
            if (price < recent_low
                    and close.iloc[-2] >= recent_low
                    and tf_slope < -0.03
                    and below_vwap
                    and adx_val > 20):
                logger.info("[scalper_v5] SELL: %s ADX=%.1f slope=%.2f%%",
                            regime, adx_val, tf_slope)
                return Signal.SELL

        return Signal.HOLD

    def calculate_sl_tp(self, df: pd.DataFrame, side: str, entry: float) -> tuple:
        atr = compute_atr(df, 14).iloc[-1]
        lookback = Config.SCALPER_LOOKBACK_BARS
        if side == "buy":
            bl = df["close"].iloc[-lookback:-1].max()
            sl = bl - atr * 0.3
            if entry - sl <= 0:
                sl = entry - atr * 0.5
            tp = entry + (entry - sl) * Config.SCALPER_TP_RR
        else:
            bl = df["close"].iloc[-lookback:-1].min()
            sl = bl + atr * 0.3
            if sl - entry <= 0:
                sl = entry + atr * 0.5
            tp = entry - (sl - entry) * Config.SCALPER_TP_RR
        return round(sl, 2), round(tp, 2)

    def get_params(self) -> dict:
        return {"regime_tf": "1h", "type": "long+short"}


# ─────────────────────────────────────────────
# V6: Short Engine (pullback-to-EMA)
# + Combined Long/Short với Funding + OI filter
# ─────────────────────────────────────────────
class ShortEngine(BaseStrategy):
    def __init__(self):
        super().__init__()
        self.name = "short_engine"

    def _ema_slope(self, series, lookback=5):
        return (series.iloc[-1] - series.iloc[-lookback]) / series.iloc[-lookback] * 100

    def analyze(self, df: pd.DataFrame, higher_tf_df: pd.DataFrame = None) -> Signal:
        if higher_tf_df is None or len(higher_tf_df) < 50:
            return Signal.HOLD

        # 1H regime check
        htf_close = higher_tf_df["close"]
        htf_ema20 = compute_ema(htf_close, 20)
        htf_slope = self._ema_slope(htf_ema20, 24)

        if htf_slope > -0.005:
            return Signal.HOLD  # chỉ short khi 1h EMA20 dốc xuống rõ

        close = df["close"]
        open_p = df["open"]
        high, low = df["high"], df["low"]
        ema20 = compute_ema(close, 20)
        ema50 = compute_ema(close, 50) if len(close) > 50 else ema20
        price = close.iloc[-1]
        atr = compute_atr(df, 14).iloc[-1]

        # VWAP
        vwap = ((high + low + close) / 3 * df["volume"]).cumsum() / df["volume"].cumsum()

        # VWAP
        vwap_val = vwap.iloc[-1]
        below_vwap = price < vwap_val

        # Giá hồi từ dưới lên test EMA20
        ema_dist_pct = (price - ema20.iloc[-1]) / ema20.iloc[-1] * 100
        was_below_ema = close.iloc[-3] < ema20.iloc[-3] if len(close) > 3 else True
        touches_ema = abs(ema_dist_pct) < 0.3
        not_too_far = abs(ema_dist_pct) < 0.8

        if not (touches_ema and not_too_far and was_below_ema):
            return Signal.HOLD

        # Reject signal: nến có bóng trên dài hoặc giảm từ EMA
        upper_wick_ratio = (high.iloc[-1] - max(close.iloc[-1], open_p.iloc[-1])) / (high.iloc[-1] - low.iloc[-1] + 0.01)
        rejected = upper_wick_ratio > 0.5 or (close.iloc[-1] < open_p.iloc[-1] and high.iloc[-1] > ema20.iloc[-1])

        if not (rejected and below_vwap):
            return Signal.HOLD

        logger.info("[short_engine] SHORT: pullback EMA=%.2f, reject @ %.2f (wick=%.0f)",
                    ema20.iloc[-1], price, high.iloc[-1] - price)
        return Signal.SELL

        return Signal.HOLD

    def calculate_sl_tp(self, df: pd.DataFrame, side: str, entry: float) -> tuple:
        atr = compute_atr(df, 14).iloc[-1]
        recent_high = df["high"].iloc[-12:].max()
        sl = max(recent_high, entry + atr * 0.5) + atr * 0.1
        tp = entry - (sl - entry) * 1.5
        return round(sl, 2), round(tp, 2)

    def get_params(self) -> dict:
        return {"type": "pullback_short", "htf_trend_required": "EMAslope_down"}


class SuperTrend(BaseStrategy):
    def __init__(self, atr_period: int = 10, multiplier: float = 3.0):
        super().__init__()
        self.name = "supertrend"
        self.atr_period = atr_period
        self.multiplier = multiplier

    def analyze(self, df: pd.DataFrame, **kwargs) -> Signal:
        if len(df) < self.atr_period + 2:
            return Signal.HOLD

        high, low, close = df["high"], df["low"], df["close"]
        atr = compute_atr(df, self.atr_period)

        hl2 = (high + low) / 2
        upper_band = hl2 + self.multiplier * atr
        lower_band = hl2 - self.multiplier * atr

        upper = upper_band.copy()
        lower = lower_band.copy()
        trend = pd.Series(index=df.index, dtype=float)
        signal = pd.Series(index=df.index, dtype=float)

        for i in range(1, len(df)):
            if close.iloc[i] > upper.iloc[i - 1]:
                trend.iloc[i] = 1
            elif close.iloc[i] < lower.iloc[i - 1]:
                trend.iloc[i] = -1
            else:
                trend.iloc[i] = trend.iloc[i - 1]

            if trend.iloc[i] == 1 and lower.iloc[i - 1] < lower.iloc[i]:
                lower.iloc[i] = lower.iloc[i - 1]
            if trend.iloc[i] == -1 and upper.iloc[i - 1] > upper.iloc[i]:
                upper.iloc[i] = upper.iloc[i - 1]

        prev_trend = trend.iloc[-2]
        curr_trend = trend.iloc[-1]

        if prev_trend == -1 and curr_trend == 1:
            logger.info("[%s] BUY: trend flipped up", self.name)
            return Signal.BUY

        if prev_trend == 1 and curr_trend == -1:
            logger.info("[%s] SELL: trend flipped down", self.name)
            return Signal.SELL

        return Signal.HOLD

    def get_params(self) -> dict:
        return {"atr_period": self.atr_period, "multiplier": self.multiplier}


# ─────────────────────────────────────────────
# FACTORY
# ─────────────────────────────────────────────
class StrategyFactory:
    _strategies = {
        "ema_cross": EmaCross,
        "rsi_ema": RsiEma,
        "macd_cross": MacdCross,
        "bb_rsi": BbRsi,
        "supertrend": SuperTrend,
        "trend_filter": TrendFilter,
        "scalper": Scalper,
        "scalper_v2": ScalperV2,
        "scalper_v3": ScalperV3,
        "scalper_v4": ScalperV4,
        "scalper_v5": ScalperV5,
        "short_engine": ShortEngine,
    }

    @classmethod
    def create(cls, name: str = None) -> BaseStrategy:
        name = name or Config.STRATEGY
        if name not in cls._strategies:
            logger.warning("Unknown strategy '%s', falling back to ema_cross", name)
            return EmaCross()
        logger.info("Using strategy: %s", name)
        return cls._strategies[name]()
