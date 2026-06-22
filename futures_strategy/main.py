"""
主入口 - 期货波段交易策略系统

用法:
  # 启动 Web 看板
  python main.py

  # 命令行快速回测
  python main.py --backtest --symbol 玉米 --strategy ma_cross

  # 命令行参数优化
  python main.py --optimize --symbol 豆粕 --strategy multi_factor

  # 命令行信号扫描
  python main.py --monitor --strategy multi_factor
"""
import argparse
import sys
import os

# 确保项目根目录在 Python 路径中
sys.path.insert(0, os.path.dirname(__file__))

from config.settings import (
    AGRI_FUTURES, DEFAULT_SYMBOLS, BACKTEST_DEFAULT,
    STRATEGY_DEFAULTS, OPTIMIZE_SPACE, WEB_HOST, WEB_PORT,
)
from data.loader import load_all_symbols, generate_mock_data
from strategies import get_strategy, STRATEGY_REGISTRY
from backtest.engine import run_backtest
from optimizer.grid_search import grid_search, find_plateau
from monitor.signal_monitor import SignalMonitor


def _load_data(symbol: str, use_mock: bool = False):
    has_token = bool(os.environ.get("TUSHARE_TOKEN", ""))
    if use_mock or not has_token:
        print(f"[Mock] 生成 {symbol} 模拟数据...")
        return generate_mock_data(symbol, BACKTEST_DEFAULT["start_date"], BACKTEST_DEFAULT["end_date"])
    print(f"[Tushare] 拉取 {symbol} 行情数据...")
    configs = {symbol: AGRI_FUTURES[symbol]}
    data = load_all_symbols(configs, BACKTEST_DEFAULT["start_date"], BACKTEST_DEFAULT["end_date"])
    return data.get(symbol)


def cmd_backtest(args):
    """命令行回测"""
    df = _load_data(args.symbol, use_mock=True)
    if df is None or df.empty:
        print(f"❌ {args.symbol} 无数据"); return

    strat = get_strategy(args.strategy)
    cfg = AGRI_FUTURES[args.symbol]

    perf, trades_df, equity = run_backtest(df, strat, cfg)

    print(f"\n{'='*55}")
    print(f"  回测结果: {args.symbol} | {args.strategy}")
    print(f"{'='*55}")
    for k, v in perf.items():
        print(f"  {k:20s}: {v}")
    print(f"{'='*55}")
    if not trades_df.empty:
        print(f"\n最近5笔交易:")
        print(trades_df.tail(5).to_string(index=False))


def cmd_optimize(args):
    """命令行参数优化"""
    df = _load_data(args.symbol, use_mock=True)
    if df is None or df.empty:
        print(f"❌ {args.symbol} 无数据"); return

    space = OPTIMIZE_SPACE.get(args.strategy, {})
    if not space:
        print(f"❌ 策略 {args.strategy} 无优化空间"); return

    strat_cls = STRATEGY_REGISTRY[args.strategy]
    cfg = AGRI_FUTURES[args.symbol]

    results = grid_search(df, strat_cls, cfg, space, min_trades=30, verbose=True)

    if results.empty:
        print("❌ 未找到有效参数组合"); return

    best = results.iloc[0]
    print(f"\n{'='*55}")
    print(f"  🏆 最优参数组合")
    print(f"{'='*55}")
    for k in space:
        print(f"  {k}: {best[k]}")
    print(f"\n  综合评分: {best['score']:.4f}")
    print(f"  年化收益: {best['annual_return']}%")
    print(f"  夏普比率: {best['sharpe_ratio']}")
    print(f"  最大回撤: {best['max_drawdown']}%")
    print(f"  胜率: {best['win_rate']}%")
    print(f"{'='*55}")

    # 高原分析
    print("\n📐 参数高原区:")
    for k in space:
        p = find_plateau(results, k)
        print(f"  {k}: 最优={p['best']}, 高原=[{p['min']}, {p['max']}], 值={p['unique_values']}")


def cmd_monitor(args):
    """命令行信号扫描"""
    configs = {k: AGRI_FUTURES[k] for k in DEFAULT_SYMBOLS if k in AGRI_FUTURES}
    monitor = SignalMonitor(configs, args.strategy)
    results = monitor.scan(use_mock=True)
    print(monitor.format_report())


def main():
    parser = argparse.ArgumentParser(description="期货波段交易策略系统")
    sub = parser.add_subparsers(dest="cmd")

    bt = sub.add_parser("backtest", help="运行回测")
    bt.add_argument("--symbol", default="玉米", choices=list(AGRI_FUTURES.keys()))
    bt.add_argument("--strategy", default="ma_cross", choices=list(STRATEGY_REGISTRY.keys()))

    opt = sub.add_parser("optimize", help="参数优化")
    opt.add_argument("--symbol", default="玉米", choices=list(AGRI_FUTURES.keys()))
    opt.add_argument("--strategy", default="ma_cross", choices=list(STRATEGY_REGISTRY.keys()))

    mon = sub.add_parser("monitor", help="信号扫描")
    mon.add_argument("--strategy", default="multi_factor", choices=list(STRATEGY_REGISTRY.keys()))

    args = parser.parse_args()

    if args.cmd == "backtest":
        cmd_backtest(args)
    elif args.cmd == "optimize":
        cmd_optimize(args)
    elif args.cmd == "monitor":
        cmd_monitor(args)
    else:
        # 默认启动 Web 看板
        from web.app import main as web_main
        web_main()


if __name__ == "__main__":
    main()
