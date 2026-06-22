"""
策略3: 多因子综合策略 (Multi-Factor Strategy)

因子体系:
  1. 趋势因子: 均线方向 (fast/slow MA)
  2. 动量因子: 价格动量 ROC
  3. 波动率因子: ATR 相对值（低波动更安全）
  4. 成交量因子: 量比放量确认
  5. ADX 趋势强度过滤（避免震荡市）

做多: 因子综合得分 >= score_threshold
做空: 因子综合得分 <= -score_threshold
"""
import pandas as pd
import numpy as np
from .base import BaseStrategy
from utils.indicators import sma, atr, momentum, volume_ratio, adx


class MultiFactorStrategy(BaseStrategy):
    """多因子综合打分波段策略"""

    name = "multi_factor"

    DEFAULT_PARAMS = {
        "trend_fast": 10,
        "trend_slow": 30,
        "momentum_period": 20,
        "volatility_period": 20,
        "volume_period": 20,
        "atr_period": 14,
        "atr_multiplier": 2.5,
        "score_threshold": 2,     # 至少 2 个因子共振才入场
        "adx_min": 20,            # ADX 最低要求（过滤震荡）
    }

    def __init__(self, params: dict = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        df = df.copy()

        # ── 因子计算 ────────────────────────────────────
        fast_ma = sma(df["close"], p["trend_fast"])
        slow_ma = sma(df["close"], p["trend_slow"])
        atr_val = atr(df["high"], df["low"], df["close"], p["atr_period"])

        # 趋势因子: +1 / -1
        f_trend = np.sign(fast_ma - slow_ma)

        # 动量因子: +1 / -1
        mom = momentum(df["close"], p["momentum_period"])
        f_momentum = np.sign(mom)

        # 波动率因子: ATR < 历史中位数 → 低波动 → 更安全
        atr_rel = atr_val / df["close"]
        atr_med = atr_rel.rolling(p["volatility_period"] * 3).median()
        # 低波动放行: +1 / 0
        f_volatility = (atr_rel < atr_med).astype(float)

        # 成交量因子: 放量 > 1.2x 均量 → +1，否则 0
        vr = volume_ratio(df["vol"], p["volume_period"])
        f_volume = (vr > 1.2).astype(float)

        # ADX 趋势强度（只在趋势市交易）
        adx_val = adx(df["high"], df["low"], df["close"], p["atr_period"])
        trend_filter = adx_val > p["adx_min"]

        # ── 综合评分 ────────────────────────────────────
        # 趋势 + 动量: 方向因子
        dir_score = f_trend + f_momentum  # 范围 -2 ~ +2

        # 成交量 + 低波动: 质量因子
        quality_score = f_volatility + f_volume  # 范围 0 ~ 2

        # 最终评分: 方向 × 质量加成
        # 做多: dir_score >= threshold 且 quality >= 1
        # 做空: dir_score <= -threshold 且 quality >= 1

        long_cond  = (dir_score >= p["score_threshold"]) & (quality_score >= 1) & trend_filter
        short_cond = (dir_score <= -p["score_threshold"]) & (quality_score >= 1) & trend_filter

        signal = pd.Series(0, index=df.index)
        signal[long_cond]  = 1
        signal[short_cond] = -1

        df["signal"]        = signal
        df["fast_ma"]       = fast_ma
        df["slow_ma"]       = slow_ma
        df["atr"]           = atr_val
        df["momentum"]      = mom
        df["volume_ratio"]  = vr
        df["adx"]           = adx_val
        df["dir_score"]     = dir_score
        df["quality_score"] = quality_score

        # 止损: ATR 动态
        df["stop_loss"]   = df["close"] - signal * p["atr_multiplier"] * atr_val
        df["take_profit"] = df["close"] + signal * p["atr_multiplier"] * atr_val * 2.0

        return df


# ── 策略注册表 ────────────────────────────────────────────────────────────
from .ma_cross import MACrossStrategy
from .boll_reversion import BollReversionStrategy

STRATEGY_REGISTRY = {
    "ma_cross":      MACrossStrategy,
    "boll_reversion": BollReversionStrategy,
    "multi_factor":  MultiFactorStrategy,
}


def get_strategy(name: str, params: dict = None):
    """工厂函数：根据名称创建策略实例"""
    cls = STRATEGY_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"未知策略: {name}，可用: {list(STRATEGY_REGISTRY.keys())}")
    return cls(params)
