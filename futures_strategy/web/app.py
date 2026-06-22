"""
Web 看板 - Flask 后端

页面:
  /               : 仪表盘总览（品种、策略、绩效概要）
  /backtest       : 回测页面（选品种+策略，查看绩效+K线图）
  /optimize       : 参数优化页面
  /monitor        : 实时信号监控页面
  /api/backtest   : 回测 API
  /api/optimize   : 优化 API
  /api/monitor    : 信号扫描 API
  /api/chart/<symbol> : K线+指标图表数据
"""
import sys
import os
import json
import logging
import pandas as pd
from flask import Flask, render_template, request, jsonify, send_from_directory

# 让各模块可 import
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config.settings import (
    AGRI_FUTURES, DEFAULT_SYMBOLS, BACKTEST_DEFAULT,
    STRATEGY_DEFAULTS, OPTIMIZE_SPACE,
    WEB_HOST, WEB_PORT, DEBUG,
)
from data.loader import load_all_symbols, get_continuous_daily, generate_mock_data
from strategies import get_strategy, STRATEGY_REGISTRY
from backtest.engine import run_backtest
from optimizer.grid_search import grid_search, find_plateau
from monitor.signal_monitor import SignalMonitor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
    static_folder=os.path.join(os.path.dirname(__file__), "static"),
)

# ── 全局状态 ─────────────────────────────────────────────────────────────

_cache = {
    "data": {},           # {品种名: DataFrame}
    "backtest_results": {},
    "optimize_results": {},
    "last_signals": {},
}

DATASET_MODE = "mock"  # "tushare" 或 "mock"


def _ensure_data(symbols: list = None):
    """确保数据已加载（从缓存或 API）"""
    symbols = symbols or DEFAULT_SYMBOLS
    missing = [s for s in symbols if s not in _cache["data"]]
    if missing:
        configs = {k: v for k, v in AGRI_FUTURES.items() if k in missing}
        if DATASET_MODE == "mock":
            for name in missing:
                _cache["data"][name] = generate_mock_data(name)
            logger.info(f"[Mock] 生成 {len(missing)} 个品种模拟数据")
        else:
            bd = BACKTEST_DEFAULT
            _cache["data"].update(
                load_all_symbols(configs, bd["start_date"], bd["end_date"])
            )


# ── 页面路由 ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html",
        symbols=list(AGRI_FUTURES.keys()),
        strategies=list(STRATEGY_REGISTRY.keys()),
        default_symbols=DEFAULT_SYMBOLS,
    )


@app.route("/backtest")
def backtest_page():
    return render_template("backtest.html",
        symbols=list(AGRI_FUTURES.keys()),
        strategies=list(STRATEGY_REGISTRY.keys()),
        strategy_defaults=json.dumps(STRATEGY_DEFAULTS, ensure_ascii=False),
        backtest_defaults=json.dumps(BACKTEST_DEFAULT, ensure_ascii=False),
    )


@app.route("/optimize")
def optimize_page():
    return render_template("optimize.html",
        symbols=list(AGRI_FUTURES.keys()),
        strategies=list(STRATEGY_REGISTRY.keys()),
        optimize_space=json.dumps(OPTIMIZE_SPACE, ensure_ascii=False),
    )


@app.route("/monitor")
def monitor_page():
    return render_template("monitor.html",
        symbols=list(AGRI_FUTURES.keys()),
        strategies=list(STRATEGY_REGISTRY.keys()),
    )


# ── API 路由 ─────────────────────────────────────────────────────────────

@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    """执行回测"""
    params = request.get_json(force=True)
    symbol = params.get("symbol", "玉米")
    strategy_name = params.get("strategy", "ma_cross")
    strategy_params = params.get("strategy_params", {})
    bt_params = params.get("backtest_params", BACKTEST_DEFAULT)

    try:
        _ensure_data([symbol])
        df = _cache["data"][symbol]

        if df.empty:
            return jsonify({"error": f"{symbol} 无数据"}), 400

        strat = get_strategy(strategy_name, strategy_params)
        cfg = AGRI_FUTURES.get(symbol, {})

        perf, trades_df, equity = run_backtest(
            df, strat, cfg,
            init_capital=bt_params.get("init_capital", 500_000),
            position_pct=bt_params.get("position_pct", 0.2),
            slippage_pct=bt_params.get("slippage_pct", 0.001),
            max_hold_days=bt_params.get("max_hold_days", 30),
        )

        # K线 + 指标数据（给前端画图）
        df_sig = strat.generate_signals(df)
        chart_data = _prepare_chart_data(df_sig, trades_df, equity)

        _cache["backtest_results"][f"{symbol}_{strategy_name}"] = perf

        return jsonify({
            "performance": perf,
            "trades": trades_df.to_dict(orient="records") if not trades_df.empty else [],
            "chart": chart_data,
        })

    except Exception as e:
        logger.exception("回测失败")
        return jsonify({"error": str(e)}), 500


@app.route("/api/optimize", methods=["POST"])
def api_optimize():
    """执行参数优化"""
    params = request.get_json(force=True)
    symbol = params.get("symbol", "玉米")
    strategy_name = params.get("strategy", "ma_cross")

    try:
        _ensure_data([symbol])
        df = _cache["data"][symbol]

        space = OPTIMIZE_SPACE.get(strategy_name, {})
        if not space:
            return jsonify({"error": f"策略 {strategy_name} 无优化空间定义"}), 400

        strat_cls = STRATEGY_REGISTRY[strategy_name]
        cfg = AGRI_FUTURES.get(symbol, {})

        results = grid_search(
            df, strat_cls, cfg, space,
            min_trades=30,
            verbose=True,
        )

        if results.empty:
            return jsonify({"error": "未找到有效参数组合"}), 404

        # 高原分析
        plateaus = {}
        for param_name in space:
            plateaus[param_name] = find_plateau(results, param_name)

        best_row = results.iloc[0].to_dict()
        top10 = results.head(10).to_dict(orient="records")

        _cache["optimize_results"][f"{symbol}_{strategy_name}"] = {
            "best": best_row,
            "top10": top10,
            "plateaus": plateaus,
        }

        return jsonify({
            "best": best_row,
            "top10": top10,
            "plateaus": plateaus,
            "total_combos": len(results),
        })

    except Exception as e:
        logger.exception("优化失败")
        return jsonify({"error": str(e)}), 500


@app.route("/api/monitor", methods=["POST"])
def api_monitor():
    """信号扫描"""
    params = request.get_json(force=True)
    strategy_name = params.get("strategy", "multi_factor")
    strategy_params = params.get("strategy_params", {})
    symbols = params.get("symbols", DEFAULT_SYMBOLS)

    configs = {k: AGRI_FUTURES[k] for k in symbols if k in AGRI_FUTURES}

    monitor = SignalMonitor(configs, strategy_name, strategy_params)
    results = monitor.scan(use_mock=(DATASET_MODE == "mock"))

    _cache["last_signals"] = results

    return jsonify({
        "signals": results,
        "report": monitor.format_report(),
    })


@app.route("/api/chart/<symbol>")
def api_chart(symbol):
    """获取品种K线图表数据"""
    _ensure_data([symbol])
    df = _cache["data"].get(symbol)
    if df is None or df.empty:
        return jsonify({"error": "无数据"}), 404

    data = {
        "dates": [str(d.date()) for d in df.index],
        "open":   df["open"].tolist(),
        "high":   df["high"].tolist(),
        "low":    df["low"].tolist(),
        "close":  df["close"].tolist(),
        "vol":    df["vol"].tolist(),
    }
    return jsonify(data)


# ── 辅助函数 ─────────────────────────────────────────────────────────────

def _prepare_chart_data(df_sig: pd.DataFrame, trades_df: pd.DataFrame, equity: pd.Series) -> dict:
    """准备给 ECharts 的 K线+指标+交易标记数据"""
    chart = {
        "dates": [str(d.date()) for d in df_sig.index],
        "kline": list(zip(
            df_sig["open"].round(2),
            df_sig["close"].round(2),
            df_sig["low"].round(2),
            df_sig["high"].round(2),
        )),
        "volume": df_sig["vol"].tolist(),
        "equity": equity.tolist() if equity is not None else [],
        "equity_dates": [str(d.date()) for d in equity.index] if equity is not None else [],
    }

    # 附加指标线
    for col in ["fast_ma", "slow_ma", "boll_upper", "boll_mid", "boll_lower"]:
        if col in df_sig.columns:
            chart[col] = df_sig[col].round(2).tolist()

    # 交易标记
    markers = []
    if not trades_df.empty:
        for _, t in trades_df.iterrows():
            markers.append({
                "date": str(pd.Timestamp(t["entry_date"]).date()),
                "type": "buy" if t["direction"] == 1 else "sell",
                "price": t["entry_price"],
            })
            markers.append({
                "date": str(pd.Timestamp(t["exit_date"]).date()),
                "type": "close",
                "price": t["exit_price"],
            })
    chart["markers"] = markers

    return chart


# ── 启动 ─────────────────────────────────────────────────────────────────

def main():
    global DATASET_MODE
    token = os.environ.get("TUSHARE_TOKEN", "")
    DATASET_MODE = "tushare" if token else "mock"
    logger.info(f"数据模式: {DATASET_MODE}")

    _ensure_data()
    app.run(host=WEB_HOST, port=WEB_PORT, debug=DEBUG)


if __name__ == "__main__":
    main()
