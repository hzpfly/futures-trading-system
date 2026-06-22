"""
回测引擎 (Backtest Engine)

特性:
  - 逐根 K 线模拟，避免前视偏差
  - 真实手续费模型（固定/按比率）
  - ATR 动态止损 + 时间止损
  - 滑点模型（按 tick 或比例）
  - 完整逐笔交易记录
  - 净值曲线 + 绩效统计
"""
import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ── 数据结构 ─────────────────────────────────────────────────────────────

class Trade:
    __slots__ = [
        "entry_date", "exit_date", "direction",
        "entry_price", "exit_price",
        "stop_loss", "take_profit",
        "size",  # 手数
        "pnl", "pnl_pct", "hold_days", "exit_reason",
    ]

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def to_dict(self) -> dict:
        return {s: getattr(self, s, None) for s in self.__slots__}


# ── 回测引擎核心 ─────────────────────────────────────────────────────────

class BacktestEngine:
    """
    参数:
        df          : OHLCV + signal + stop_loss + take_profit
        symbol_cfg  : 品种配置 dict (unit, fee_per_lot, margin_rate)
        init_capital: 初始资金
        position_pct: 单次开仓占用资金比例
        slippage_pct: 滑点比例（相对价格）
        max_hold_days: 时间止损（超过X天强制平仓）
    """

    def __init__(
        self,
        df: pd.DataFrame,
        symbol_cfg: dict,
        init_capital: float = 500_000,
        position_pct: float = 0.20,
        slippage_pct: float = 0.001,
        max_hold_days: int = 30,
    ):
        self.df = df
        self.cfg = symbol_cfg
        self.init_capital = init_capital
        self.position_pct = position_pct
        self.slippage_pct = slippage_pct
        self.max_hold_days = max_hold_days

        self.trades: List[Trade] = []
        self.equity_curve: Optional[pd.Series] = None

    # ── 主回测循环 ────────────────────────────────────────────────────────

    def run(self) -> dict:
        df = self.df
        capital = self.init_capital
        equity = []
        position = None  # 当前持仓

        unit = self.cfg["unit"]
        fee_per_lot = self.cfg["fee_per_lot"]
        margin_rate = self.cfg["margin_rate"]

        for i in range(len(df)):
            row = df.iloc[i]
            date = df.index[i]
            close = row["close"]

            # ── 检查持仓止损/止盈/时间止损 ──────────────────────────────
            if position is not None:
                hold_days = (date - position["entry_date"]).days
                exit_price = None
                exit_reason = None

                # 动态更新止损（跟踪止损）
                if position["direction"] == 1:
                    # 多头: 若当前收盘价 - ATR * mult 高于原止损, 上移
                    if "atr" in row.index:
                        new_sl = close - self.cfg.get("atr_mult", 2.0) * row["atr"]
                        position["stop_loss"] = max(position["stop_loss"], new_sl)
                else:
                    if "atr" in row.index:
                        new_sl = close + self.cfg.get("atr_mult", 2.0) * row["atr"]
                        position["stop_loss"] = min(position["stop_loss"], new_sl)

                # 止损触发
                if position["direction"] == 1 and row["low"] <= position["stop_loss"]:
                    exit_price  = position["stop_loss"]
                    exit_reason = "stop_loss"
                elif position["direction"] == -1 and row["high"] >= position["stop_loss"]:
                    exit_price  = position["stop_loss"]
                    exit_reason = "stop_loss"

                # 止盈触发
                if exit_price is None and pd.notna(position.get("take_profit")):
                    if position["direction"] == 1 and row["high"] >= position["take_profit"]:
                        exit_price  = position["take_profit"]
                        exit_reason = "take_profit"
                    elif position["direction"] == -1 and row["low"] <= position["take_profit"]:
                        exit_price  = position["take_profit"]
                        exit_reason = "take_profit"

                # 时间止损
                if exit_price is None and hold_days >= self.max_hold_days:
                    exit_price  = close
                    exit_reason = "time_stop"

                if exit_price is not None:
                    exit_price_slip = exit_price * (
                        1 - self.slippage_pct * position["direction"]
                    )
                    pnl = (
                        (exit_price_slip - position["entry_price"])
                        * position["direction"]
                        * position["size"]
                        * unit
                    ) - fee_per_lot * position["size"]

                    capital += pnl
                    t = Trade(
                        entry_date=position["entry_date"],
                        exit_date=date,
                        direction=position["direction"],
                        entry_price=position["entry_price"],
                        exit_price=exit_price_slip,
                        stop_loss=position["stop_loss"],
                        take_profit=position.get("take_profit"),
                        size=position["size"],
                        pnl=round(pnl, 2),
                        pnl_pct=round(pnl / self.init_capital * 100, 4),
                        hold_days=hold_days,
                        exit_reason=exit_reason,
                    )
                    self.trades.append(t)
                    position = None

            # ── 开仓信号 ──────────────────────────────────────────────────
            if position is None:
                sig = int(row.get("signal", 0))
                if sig in (1, -1):
                    entry_price = close * (1 + self.slippage_pct * sig)
                    # 计算手数
                    alloc = capital * self.position_pct
                    margin_per_lot = entry_price * unit * margin_rate
                    size = max(1, int(alloc / margin_per_lot))

                    # 资金不足跳过
                    if margin_per_lot * size > capital * 0.9:
                        equity.append((date, capital))
                        continue

                    position = {
                        "entry_date": date,
                        "entry_price": entry_price,
                        "direction": sig,
                        "stop_loss": row.get("stop_loss", entry_price * (1 - 0.02 * sig)),
                        "take_profit": row.get("take_profit"),
                        "size": size,
                    }

            equity.append((date, capital))

        # 强制平仓（最后一天）
        if position is not None and len(df) > 0:
            last = df.iloc[-1]
            exit_p = last["close"]
            pnl = (
                (exit_p - position["entry_price"])
                * position["direction"]
                * position["size"]
                * unit
            ) - fee_per_lot * position["size"]
            capital += pnl
            t = Trade(
                entry_date=position["entry_date"],
                exit_date=df.index[-1],
                direction=position["direction"],
                entry_price=position["entry_price"],
                exit_price=exit_p,
                stop_loss=position["stop_loss"],
                take_profit=position.get("take_profit"),
                size=position["size"],
                pnl=round(pnl, 2),
                pnl_pct=round(pnl / self.init_capital * 100, 4),
                hold_days=(df.index[-1] - position["entry_date"]).days,
                exit_reason="end_of_data",
            )
            self.trades.append(t)
            equity[-1] = (equity[-1][0], capital)

        dates, values = zip(*equity) if equity else ([], [])
        self.equity_curve = pd.Series(values, index=dates, name="equity")
        self.final_capital = capital

        return self.get_performance()

    # ── 绩效统计 ──────────────────────────────────────────────────────────

    def get_performance(self) -> dict:
        trades = self.trades
        eq = self.equity_curve

        if not trades:
            return {"error": "无交易记录"}

        pnls = [t.pnl for t in trades]
        wins  = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        total_return = (self.final_capital - self.init_capital) / self.init_capital
        n_days = (eq.index[-1] - eq.index[0]).days
        annual_return = (1 + total_return) ** (365 / max(n_days, 1)) - 1

        # 最大回撤
        running_max = eq.cummax()
        drawdown = (eq - running_max) / running_max
        max_dd = drawdown.min()

        # 夏普比率（日收益）
        daily_ret = eq.pct_change().dropna()
        sharpe = (daily_ret.mean() / (daily_ret.std() + 1e-9)) * np.sqrt(252)

        # 卡尔马比率
        calmar = annual_return / (abs(max_dd) + 1e-9)

        # Profit Factor
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = gross_profit / (gross_loss + 1e-9)

        return {
            "total_trades":    len(trades),
            "win_trades":      len(wins),
            "loss_trades":     len(losses),
            "win_rate":        round(len(wins) / len(trades) * 100, 2),
            "total_return":    round(total_return * 100, 2),
            "annual_return":   round(annual_return * 100, 2),
            "max_drawdown":    round(max_dd * 100, 2),
            "sharpe_ratio":    round(float(sharpe), 3),
            "calmar_ratio":    round(calmar, 3),
            "profit_factor":   round(profit_factor, 3),
            "avg_win":         round(np.mean(wins), 2) if wins else 0,
            "avg_loss":        round(np.mean(losses), 2) if losses else 0,
            "avg_hold_days":   round(np.mean([t.hold_days for t in trades]), 1),
            "init_capital":    self.init_capital,
            "final_capital":   round(self.final_capital, 2),
        }

    def get_trades_df(self) -> pd.DataFrame:
        return pd.DataFrame([t.to_dict() for t in self.trades])

    def get_equity_curve(self) -> pd.Series:
        return self.equity_curve


# ── 便捷封装 ─────────────────────────────────────────────────────────────

def run_backtest(
    df: pd.DataFrame,
    strategy,
    symbol_cfg: dict,
    init_capital: float = 500_000,
    position_pct: float = 0.20,
    slippage_pct: float = 0.001,
    max_hold_days: int = 30,
) -> tuple:
    """
    一次性完成：生成信号 → 回测 → 返回 (绩效dict, 交易df, 净值曲线)
    """
    df_sig = strategy.generate_signals(df)
    engine = BacktestEngine(
        df_sig, symbol_cfg,
        init_capital=init_capital,
        position_pct=position_pct,
        slippage_pct=slippage_pct,
        max_hold_days=max_hold_days,
    )
    perf = engine.run()
    return perf, engine.get_trades_df(), engine.get_equity_curve()
