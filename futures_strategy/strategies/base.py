"""
策略基类
"""
import pandas as pd
from abc import ABC, abstractmethod


class BaseStrategy(ABC):
    """所有策略的抽象基类"""

    name: str = "base"

    def __init__(self, params: dict = None):
        self.params = params or {}

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        输入: OHLCV DataFrame（索引为 DatetimeIndex）
        输出: 原始 df + 新增列:
            signal  : 1=做多, -1=做空, 0=无信号
            stop_loss: 建议止损价
            take_profit: 建议止盈价（可选）
        """
        pass

    def __repr__(self):
        return f"{self.__class__.__name__}({self.params})"
