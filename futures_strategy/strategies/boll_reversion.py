"""
策略2: 布林带均值回归策略 (Bollinger Band Mean Reversion)

核心逻辑:
  - 价格跌破布林下轨 + RSI 超卖 → 做多信号
  - 价格突破布林上轨 + RSI 超买 → 做空信号
  - 中轨作为止盈目标
  - ATR 动态止损
"""
import pandas as pd
import numpy as np
from .base import BaseStrategy
from utils.indicators import bollinger_bands, rsi, atr


class BollReversionStrategy(BaseStrategy):
    """布林带均值回归 + RSI 双重确认策略"""

    name = "boll_reversion"

    DEFAULT_PARAMS = {
        "period": 20,
        "std_dev": 2.0,
        "rsi_period": 14,
        "rsi_oversold": 30,
        "rsi_overbought": 70,
        "atr_period": 14,
        "atr_multiplier": 1.5,
    }

    def __init__(self, params: dict = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        df = df.copy()

        upper, mid, lower = bollinger_bands(df["close"], p["period"], p["std_dev"])
        rsi_val = rsi(df["close"], p["rsi_period"])
        atr_val = atr(df["high"], df["low"], df["close"], p["atr_period"])

        # 布林带宽度（过滤震荡市）
        bw = (upper - lower) / (mid + 1e-9)
        min_bw = bw.rolling(50).mean() * 0.5  # 过窄时不交易

        # 信号条件
        long_cond  = (df["close"] < lower) & (rsi_val < p["rsi_oversold"])  & (bw > min_bw)
        short_cond = (df["close"] > upper) & (rsi_val > p["rsi_overbought"]) & (bw > min_bw)

        # 只在突破后次日触发（避免同日反转被套）
        long_cond  = long_cond.shift(1).fillna(False)
        short_cond = short_cond.shift(1).fillna(False)

        signal = pd.Series(0, index=df.index)
        signal[long_cond]  = 1
        signal[short_cond] = -1

        df["signal"]       = signal
        df["boll_upper"]   = upper
        df["boll_mid"]     = mid
        df["boll_lower"]   = lower
        df["rsi"]          = rsi_val
        df["atr"]          = atr_val

        # 止损：突破方向 + ATR
        df["stop_loss"]   = df["close"] - signal * p["atr_multiplier"] * atr_val
        # 止盈：目标为中轨（均值回归）
        df["take_profit"] = mid

        return df
