"""
实时信号监控模块

功能:
  - 对当前持仓品种生成交易信号
  - 支持定时检查（每日盘后/开盘前）
  - 信号日志记录
"""
import logging
import json
import os
from datetime import datetime
from typing import Dict, List, Optional
import pandas as pd

from strategies import get_strategy
from data.loader import get_continuous_daily, generate_mock_data

logger = logging.getLogger(__name__)


class SignalMonitor:
    """期货波段信号监控"""

    def __init__(self, symbol_configs: dict, strategy_name: str, strategy_params: dict = None):
        self.symbol_configs = symbol_configs
        self.strategy = get_strategy(strategy_name, strategy_params)
        self.signal_log: List[dict] = []
        self.last_signals: Dict[str, dict] = {}

    def scan(
        self,
        lookback_days: int = 120,
        use_mock: bool = False,
        start_date: str = None,
        end_date: str = None,
    ) -> Dict[str, dict]:
        """
        扫描所有品种，生成当前信号。

        返回: {品种名: {signal, price, atr, reasons, timestamp}}
        """
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")
        if start_date is None:
            from datetime import timedelta
            d = datetime.now() - timedelta(days=lookback_days)
            start_date = d.strftime("%Y%m%d")

        results = {}
        for name, cfg in self.symbol_configs.items():
            try:
                if use_mock:
                    df = generate_mock_data(name, start_date, end_date)
                else:
                    df = get_continuous_daily(
                        ts_code=cfg["code"],
                        start_date=start_date,
                        end_date=end_date,
                    )

                if df.empty or len(df) < 30:
                    continue

                df_sig = self.strategy.generate_signals(df)
                last = df_sig.iloc[-1]

                signal_val = int(last.get("signal", 0))
                result = {
                    "symbol":    name,
                    "signal":    signal_val,
                    "price":     round(last["close"], 2),
                    "atr":       round(last.get("atr", 0), 2),
                    "date":      str(df.index[-1].date()),
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }

                # 附加因子信息
                extra = {}
                for col in ["fast_ma", "slow_ma", "rsi", "adx", "macd_hist",
                            "boll_upper", "boll_mid", "boll_lower",
                            "momentum", "volume_ratio"]:
                    if col in df_sig.columns:
                        extra[col] = round(float(last[col]), 4)
                result["indicators"] = extra

                results[name] = result
                self.last_signals[name] = result

                # 非零信号记录
                if signal_val != 0:
                    self.signal_log.append(result)
                    direction = "做多" if signal_val == 1 else "做空"
                    logger.info(f"[信号] {name} {direction} @ {result['price']}")

            except Exception as e:
                logger.error(f"[监控] {name} 异常: {e}")
                results[name] = {"error": str(e)}

        return results

    def format_report(self) -> str:
        """生成人类可读的信号报告"""
        if not self.last_signals:
            return "暂无信号数据"

        lines = [f"{'='*55}"]
        lines.append(f"  期货波段信号报告  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"  策略: {self.strategy.name}")
        lines.append(f"{'='*55}")

        for name, info in self.last_signals.items():
            if "error" in info:
                lines.append(f"\n  {name}: ❌ {info['error']}")
                continue

            sig = info["signal"]
            if sig == 1:
                icon = "🔴"
                action = "做多"
            elif sig == -1:
                icon = "🟢"
                action = "做空"
            else:
                icon = "⚪"
                action = "观望"

            lines.append(f"\n  {icon} {name}  [{action}]  价格: {info['price']}")
            lines.append(f"     ATR: {info.get('atr', '-')}", )
            if "indicators" in info:
                ind = info["indicators"]
                if "rsi" in ind:
                    lines.append(f"     RSI: {ind['rsi']}")
                if "adx" in ind:
                    lines.append(f"     ADX: {ind['adx']}")
                if "momentum" in ind:
                    lines.append(f"     动量: {ind['momentum']:.2%}")

        lines.append(f"\n{'='*55}")
        return "\n".join(lines)

    def save_log(self, filepath: str):
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.signal_log, f, ensure_ascii=False, indent=2, default=str)
