# 期货波段交易系统

基于三重滤网（Triple Screen）理论的农产品期货交易系统，覆盖棉花、铁矿石等品种。

## 项目结构

```
futures-trading-system/
├── triple_screen/              # 三重滤网策略
│   ├── triple_screen_optimized.py   # 最优参数版（主力回测脚本）
│   ├── triple_screen_optimize.py    # 参数优化网格搜索
│   ├── triple_screen_akshare.py     # 初始版本（基础逻辑）
│   ├── get_history_akshare.py       # AkShare历史数据获取工具
│   ├── c_quote_monitor.py           # 玉米行情实时监控
│   └── jd_quote_monitor.py          # 鸡蛋行情实时监控
│
├── futures_strategy/           # 多策略框架（Web看板）
│   ├── main.py                 # 启动入口
│   ├── strategies/             # 三套策略
│   │   ├── ma_cross.py         # 均线交叉策略
│   │   ├── boll_reversion.py   # 布林带均值回归
│   │   └── multi_factor.py     # 多因子策略
│   ├── backtest/engine.py      # 回测引擎
│   ├── optimizer/grid_search.py # 参数优化
│   ├── monitor/signal_monitor.py # 信号监控
│   ├── web/app.py              # Flask Web看板
│   └── utils/indicators.py    # 指标库
│
├── data/                       # 历史数据
│   ├── cotton_cf_history.csv   # 棉花主力日线（2025-至今）
│   └── iron_ore_history.csv    # 铁矿石主力日线（2025-至今）
│
└── reports/
    └── triple_screen_report.html  # 回测报告（含可视化）
```

## 三重滤网策略说明

### 核心思路（Alexander Elder 原著）
| 层级 | 周期 | 指标 | 作用 |
|------|------|------|------|
| **第一层**（大趋势）| 周线 | MACD(8,24,9) + EXPMA(13) | 判断方向，只顺势操作 |
| **第二层**（入场时机）| 日线 | KD(14,3,3) | 超卖金叉做多，超买死叉做空 |
| **第三层**（执行入场）| 次日开盘 | 跟踪止损 | 分批入场，顺势加仓 |

### 最优参数（棉花CF，2025-2026 回测）
```python
MACD_FAST   = 8       # 原12，加快响应
MACD_SLOW   = 24      # 原26，加快响应
MACD_SIGNAL = 9
KD_PERIOD   = 14      # 稳定版KD
STOP_LOSS   = 3%      # 3%固定止损
TAKE_PROFIT = KD > 70 # KD超买区止盈
```

### 回测结果（棉花主力，2025-01 至 2026-06）
| 指标 | 结果 |
|------|------|
| 初始资金 | 100,000 元 |
| 最终权益 | ~108,000 元 |
| 总收益率 | **+7.86%** |
| 交易次数 | 12 笔 |
| 胜率 | **66.7%** |
| 最大回撤 | -5.73% |

## 快速开始

### 安装依赖
```bash
pip install -r requirements.txt
```

### 运行三重滤网回测（棉花）
```bash
python triple_screen/triple_screen_optimized.py
```

### 参数优化网格搜索
```bash
python triple_screen/triple_screen_optimize.py
```

### 启动多策略 Web 看板
```bash
python futures_strategy/main.py
# 访问 http://127.0.0.1:5050
```

### 获取最新历史数据
```bash
python triple_screen/get_history_akshare.py
# 无需认证，直接运行
```

## 数据来源

- **AkShare**（首选）：免费，无需账号，盘后即可获取。棉花/铁矿石均支持。
- **TqSdk**：实时行情（仅交易时段）。配置账号密码见 `triple_screen/config.toml`（需自行创建，参考 `config.toml.example`）。
- **Tushare**：备用，需配置 Token（`export TUSHARE_TOKEN="xxx"`）。

## 合约代码

| 品种 | AkShare 代码 | TqSdk 代码 |
|------|------------|----------|
| 棉花主力 | `CF0` | `CZCE.CF609` |
| 铁矿石主力 | `I0` | `DCE.i2609` |
| 玉米主力 | `C0` | `DCE.c2609` |
| 豆粕主力 | `M0` | `DCE.m2509` |
| 鸡蛋主力 | `JD0` | `DCE.jd2605` |

## 注意事项

- TqSdk **非交易时段**无法连接免费服务器，历史数据请用 AkShare
- 棉花合约乘数：5 元/吨，保证金约 10%
- 回测结果不代表实盘收益，仅供参考
