"""
期货波段交易策略系统 - 核心配置
"""
import os

# ============================================================
# Tushare 配置
# ============================================================
TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")  # 从环境变量读取

# ============================================================
# 农产品期货品种配置
# 格式: {品种名: (主力连续合约代码, 交易所, 每手单位, 手续费率/元每手, 保证金率)}
# ============================================================
AGRI_FUTURES = {
    "玉米":      {"code": "C.DCE",   "exchange": "DCE",  "unit": 10,   "fee_per_lot": 1.2,  "margin_rate": 0.05},
    "豆粕":      {"code": "M.DCE",   "exchange": "DCE",  "unit": 10,   "fee_per_lot": 1.5,  "margin_rate": 0.07},
    "豆油":      {"code": "Y.DCE",   "exchange": "DCE",  "unit": 10,   "fee_per_lot": 2.5,  "margin_rate": 0.07},
    "大豆一号":  {"code": "A.DCE",   "exchange": "DCE",  "unit": 10,   "fee_per_lot": 2.0,  "margin_rate": 0.07},
    "棉花":      {"code": "CF.ZCE",  "exchange": "ZCE",  "unit": 5,    "fee_per_lot": 4.3,  "margin_rate": 0.07},
    "白糖":      {"code": "SR.ZCE",  "exchange": "ZCE",  "unit": 10,   "fee_per_lot": 3.0,  "margin_rate": 0.07},
    "菜粕":      {"code": "RM.ZCE",  "exchange": "ZCE",  "unit": 10,   "fee_per_lot": 1.5,  "margin_rate": 0.07},
    "强麦":      {"code": "WH.ZCE",  "exchange": "ZCE",  "unit": 20,   "fee_per_lot": 3.0,  "margin_rate": 0.05},
    "生猪":      {"code": "LH.DCE",  "exchange": "DCE",  "unit": 16,   "fee_per_lot": 6.0,  "margin_rate": 0.15},
    "鸡蛋":      {"code": "JD.DCE",  "exchange": "DCE",  "unit": 10,   "fee_per_lot": 1.5,  "margin_rate": 0.08},
}

# 默认回测品种
DEFAULT_SYMBOLS = ["玉米", "豆粕", "棉花", "白糖", "菜粕"]

# ============================================================
# 回测参数默认值
# ============================================================
BACKTEST_DEFAULT = {
    "start_date": "20200101",
    "end_date": "20260101",
    "init_capital": 500_000,      # 初始资金（元）
    "position_pct": 0.2,          # 单品种仓位比例
    "slippage_ticks": 1,          # 滑点（tick数）
    "max_hold_days": 30,          # 最大持仓天数（时间止损）
}

# ============================================================
# 策略参数默认值（各策略独立）
# ============================================================
STRATEGY_DEFAULTS = {
    "ma_cross": {
        "fast_period": 10,
        "slow_period": 30,
        "signal_period": 5,        # 信号确认周期
        "atr_period": 14,
        "atr_multiplier": 2.0,     # 止损ATR倍数
    },
    "boll_reversion": {
        "period": 20,
        "std_dev": 2.0,
        "rsi_period": 14,
        "rsi_oversold": 30,
        "rsi_overbought": 70,
        "atr_period": 14,
        "atr_multiplier": 1.5,
    },
    "multi_factor": {
        "trend_fast": 10,
        "trend_slow": 30,
        "momentum_period": 20,
        "volatility_period": 20,
        "volume_period": 20,
        "atr_period": 14,
        "atr_multiplier": 2.5,
        "score_threshold": 2,      # 多因子综合得分阈值
    },
}

# ============================================================
# 参数优化搜索空间
# ============================================================
OPTIMIZE_SPACE = {
    "ma_cross": {
        "fast_period":    [5, 8, 10, 13, 15],
        "slow_period":    [20, 25, 30, 40, 50],
        "atr_multiplier": [1.5, 2.0, 2.5, 3.0],
    },
    "boll_reversion": {
        "period":         [15, 20, 25, 30],
        "std_dev":        [1.5, 2.0, 2.5],
        "rsi_oversold":   [25, 30, 35],
        "rsi_overbought": [65, 70, 75],
        "atr_multiplier": [1.0, 1.5, 2.0],
    },
    "multi_factor": {
        "trend_fast":     [8, 10, 13],
        "trend_slow":     [25, 30, 40],
        "atr_multiplier": [2.0, 2.5, 3.0],
        "score_threshold":[1, 2, 3],
    },
}

# ============================================================
# Web 服务配置
# ============================================================
WEB_HOST = "127.0.0.1"
WEB_PORT = 5050
DEBUG = True

# 数据缓存目录
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
