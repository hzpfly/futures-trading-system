"""
参数优化模块 - 网格搜索 + 综合评分排名

评分维度 (加权打分):
  - 年化收益率 (权重 30%)
  - 夏普比率     (权重 25%)
  - 最大回撤     (权重 20%，越低越好)
  - 胜率         (权重 15%)
  - 盈亏比       (权重 10%)

按照 Backtest Expert 最佳实践:
  - 寻找"高原"而非"尖峰"
  - 最优参数周围性能稳定才是真 edge
"""
import itertools
import logging
import numpy as np
import pandas as pd
from typing import List, Dict, Tuple
from backtest.engine import BacktestEngine

logger = logging.getLogger(__name__)


def _normalize(series: pd.Series, ascending: bool = True) -> pd.Series:
    """Min-max 归一化到 [0, 1]"""
    s = series.copy()
    lo, hi = s.min(), s.max()
    if hi - lo < 1e-9:
        return pd.Series(0.5, index=s.index)
    normed = (s - lo) / (hi - lo)
    return normed if ascending else (1 - normed)


def _composite_score(results: pd.DataFrame) -> pd.Series:
    """
    综合评分 (越高越好)

    权重:
      annual_return  0.25
      sharpe_ratio   0.25
      max_drawdown   0.20  (取反)
      win_rate       0.15
      profit_factor  0.15
    """
    w = {
        "annual_return": 0.25,
        "sharpe_ratio":  0.25,
        "max_drawdown":  0.20,
        "win_rate":      0.15,
        "profit_factor": 0.15,
    }
    score = pd.Series(0.0, index=results.index)
    for col, wt in w.items():
        asc = col == "max_drawdown"  # 回撤越小越好
        score += wt * _normalize(results[col], ascending=asc)
    return score


def grid_search(
    df: pd.DataFrame,
    strategy,
    symbol_cfg: dict,
    param_grid: dict,
    init_capital: float = 500_000,
    position_pct: float = 0.20,
    slippage_pct: float = 0.001,
    max_hold_days: int = 30,
    min_trades: int = 30,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    网格搜索最优参数组合。

    参数:
        df          : 行情 DataFrame
        strategy    : 策略类 (非实例)
        symbol_cfg  : 品种配置
        param_grid  : {参数名: [值列表]} 参数搜索空间
        min_trades  : 最少交易笔数过滤
        verbose     : 打印进度

    返回: DataFrame，按综合评分降序排列
    """
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    combos = list(itertools.product(*values))
    total = len(combos)

    if verbose:
        logger.info(f"参数组合数: {total}")

    rows = []
    for idx, combo in enumerate(combos):
        params = dict(zip(keys, combo))

        if verbose and (idx + 1) % 10 == 0:
            logger.info(f"[{idx+1}/{total}] 测试参数: {params}")

        try:
            strat = strategy(params)
            df_sig = strat.generate_signals(df)
            engine = BacktestEngine(
                df_sig, symbol_cfg,
                init_capital=init_capital,
                position_pct=position_pct,
                slippage_pct=slippage_pct,
                max_hold_days=max_hold_days,
            )
            perf = engine.run()
        except Exception as e:
            if verbose:
                logger.warning(f"  跳过: {e}")
            continue

        if perf.get("total_trades", 0) < min_trades:
            continue

        row = {**params, **perf}
        rows.append(row)

    if not rows:
        logger.warning("未找到满足条件的参数组合")
        return pd.DataFrame()

    results = pd.DataFrame(rows)
    results["score"] = _composite_score(results)
    results = results.sort_values("score", ascending=False).reset_index(drop=True)

    if verbose:
        best = results.iloc[0]
        logger.info(
            f"\n{'='*60}\n"
            f"  最优参数: {dict(results.iloc[0][keys])}\n"
            f"  综合评分: {best['score']:.4f}\n"
            f"  年化收益: {best['annual_return']}%\n"
            f"  夏普比率: {best['sharpe_ratio']}\n"
            f"  最大回撤: {best['max_drawdown']}%\n"
            f"  胜率:     {best['win_rate']}%\n"
            f"{'='*60}"
        )

    return results


def find_plateau(
    results: pd.DataFrame,
    param_name: str,
    top_pct: float = 0.20,
) -> dict:
    """
    寻找参数"高原区"：前 top_pct% 的最优组合中，参数值分布。

    返回: {参数名: (min, max, best, 涵盖比例)}
    """
    n = max(1, int(len(results) * top_pct))
    top = results.head(n)
    vals = top[param_name]
    return {
        "param": param_name,
        "min": vals.min(),
        "max": vals.max(),
        "best": results.iloc[0][param_name],
        "unique_values": sorted(vals.unique().tolist()),
        "top_coverage": round(n / len(results) * 100, 1),
    }
