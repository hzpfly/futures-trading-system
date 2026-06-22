"""
策略1: 均线交叉趋势跟踪策略 (MA Cross Trend Following)

核心逻辑:
  - 快线上穿慢线 → 做多信号
  - 快线下穿慢线 → 做空信号
  - ATR 动态止损
  - MACD 柱状图辅助确认趋势方向
"""
import pandas as pd
import numpy as np
from .base import BaseStrategy
from utils.indicators import sma, atr, macd


class MACrossStrategy(BaseStrategy):
    """均线交叉 + ATR 止损趋势跟踪策略"""

    name = "ma_cross"

    DEFAULT_PARAMS = {
        "fast_period": 10,
        "slow_period": 30,
        "signal_period": 5,
        "atr_period": 14,
        "atr_multiplier": 2.0,
    }

    def __init__(self, params: dict = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        df = df.copy()

        fast = sma(df["close"], p["fast_period"])
        slow = sma(df["close"], p["slow_period"])
        atr_val = atr(df["high"], df["low"], df["close"], p["atr_period"])
        dif, dea, hist = macd(df["close"])

        # 金叉/死叉信号
        cross_up   = (fast > slow) & (fast.shift(1) <= slow.shift(1))
        cross_down = (fast < slow) & (fast.shift(1) >= slow.shift(1))

        # 趋势过滤: MACD 柱状图方向确认（可略微延迟信号但更可靠）
        trend_up   = hist > 0
        trend_down = hist < 0

        signal = pd.Series(0, index=df.index)
        signal[cross_up   & trend_up]   = 1
        signal[cross_down & trend_down] = -1

        df["signal"]      = signal
        df["fast_ma"]     = fast
        df["slow_ma"]     = slow
        df["atr"]         = atr_val
        df["macd_hist"]   = hist

        # 止损: 入场价 ± atr_multiplier * ATR
        df["stop_loss"]    = df["close"] - signal * p["atr_multiplier"] * atr_val
        # 止盈: 1.5倍止损距离
        df["take_profit"]  = df["close"] + signal * p["atr_multiplier"] * atr_val * 1.5

        return df
