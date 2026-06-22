# 期货波段交易系统

基于 Alexander Elder 三重滤网（Triple Screen）理论的期货交易系统，覆盖棉花、铁矿石等农产品期货。

## 项目结构

```
futures-trading-system/
├── triple_screen/                # 三重滤网策略（三个版本）
│   ├── triple_screen_optimized.py   # V0: 原始二层版
│   ├── triple_screen_3layer.py      # V1: 三层版（60分钟精细择时）
│   ├── triple_screen_impulse_v2.py  # V2: 动力系统版
│   ├── run_backtest.py             # 统一回测入口（支持三版本对比）
│   ├── triple_screen_optimize.py    # 参数优化网格搜索
│   ├── triple_screen_akshare.py     # 初始版本（基础逻辑）
│   ├── backtest_from_parquet.py     # 基于本地Parquet数据的回测
│   ├── triple_screen_tqsdk.py      # TqSdk数据源版回测
│   ├── get_history_akshare.py       # AkShare历史数据获取工具
│   ├── fetch_tick_data.py           # TqSdk Tick数据实时采集器
│   ├── view_ticks.py               # 命令行Tick数据查看器
│   ├── maintain_data.py             # Tick数据维护（归档/合并/清理）
│   ├── maintain_and_clean.sh        # 数据维护一键脚本
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
│   ├── kline/                 # K线历史数据（Parquet，不上传Git）
│   └── ticks/                # Tick逐笔数据（Parquet，不上传Git）
│
└── reports/                   # 回测报告（不上传Git）
```

---

## 三重滤网策略 - 三个版本

### V0：原始二层版 `triple_screen_optimized.py`

Elder《以交易为生》原始实现，两层滤网。

| 层级 | 周期 | 指标 | 作用 |
|------|------|------|------|
| 第一层 | 周线 | MACD(8,24,9) 柱斜率 | 判断主趋势方向 |
| 第二层 | 日线 | KD(14,3,3) 金叉/死叉 | 识别回调结束，次日开盘进场 |
| 止盈 | 日线 | KD > 70（多）/ KD < 30（空） | KD极端值平仓 |

- 文件：`triple_screen/triple_screen_optimized.py`
- 运行：`python triple_screen/run_backtest.py v0`

### V1：三层版 `triple_screen_3layer.py`

在 V0 基础上补上 Elder 原著的第三层滤网。

| 层级 | 周期 | 指标 | 作用 |
|------|------|------|------|
| 第一层 | 周线 | MACD(8,24,9) 柱斜率 | 判断主趋势方向 |
| 第二层 | 日线 | KD(14,3,3) 金叉/死叉 | 识别回调结束 |
| **第三层** | **60分钟** | **精细择时** | **避免追高杀低** |
| 止盈 | 日线 | KD > 70（多）/ KD < 30（空） | 同 V0 |

**第三层逻辑：**
- 正常开盘 → 首根 60 分钟 Bar 收盘价进场
- 开盘跳空 >1%（多头）→ 等日内回调，用当日最低价进场
- 开盘跳空 <-1%（空头）→ 等日内反弹，用当日最高价进场

- 文件：`triple_screen/triple_screen_3layer.py`
- 运行：`python triple_screen/run_backtest.py v1`

### V2：动力系统版 `triple_screen_impulse_v2.py`

结合 Elder《Come Into My Trading Room》中的**动力系统（Impulse System）**，用 EMA(13) + MACD 柱颜色变化替代 KD 金叉。

| 层级 | 周期 | 指标 | 作用 |
|------|------|------|------|
| 第一层 | 周线 | MACD(12,26,9) 柱斜率 | 判断主趋势方向 |
| 第二层 | 日线 | 动力系统颜色变化 | 蓝→绿做多，蓝→红做空 |
| **第三层** | **60分钟** | **精细择时** | **避免追高杀低** |
| 止盈 | 日线 | **动力系统颜色反转** | 绿→蓝/红→蓝 即平仓 |

**动力系统颜色规则：**
- 🟢 绿柱：EMA(13) 上升 + MACD 柱上升 → 多头动能
- 🔴 红柱：EMA(13) 下降 + MACD 柱下降 → 空头动能
- 🔵 蓝柱：混合信号 → 不操作

- 文件：`triple_screen/triple_screen_impulse_v2.py`
- 运行：`python triple_screen/run_backtest.py v2`

---

## 三版本回测对比

> 回测区间：2025-01 ~ 2026-06，品种：棉花主力 CF0，初始资金：100,000 元

| 指标 | V0 原始二层版 | V1 三层版 | V2 动力系统版 |
|------|-------------|-----------|----------------|
| **收益率** | +7.86% | **+14.03%** | +11.69% |
| **胜率** | 66.7% | 66.7% | **69.2%** |
| **盈亏比** | 2.45 | 4.60 | **8.64** |
| **最大回撤** | -5.73% | -3.29% | **-2.25%** |
| **交易次数** | 12 | 15 | 13 |
| **适用场景** | 基准参考 | 趋势行情 | 稳健风格 |

**结论：**
- 追求最高收益 → 选 **V1**（三层精细择时，避免追高杀低）
- 追求最小回撤/最高盈亏比 → 选 **V2**（动能衰竭即出场，风险控制最好）
- V0 最简单，适合作为基准对照

---

## 快速开始

### 安装依赖
```bash
pip install -r requirements.txt
```

### 运行回测
```bash
# 运行指定版本
python triple_screen/run_backtest.py v0
python triple_screen/run_backtest.py v1
python triple_screen/run_backtest.py v2

# 三版本对比
python triple_screen/run_backtest.py compare

# 依次运行全部版本
python triple_screen/run_backtest.py all
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

### 采集实时 Tick 数据（交易时段）
```bash
# 检查连接和主力合约
python triple_screen/fetch_tick_data.py --check

# 开始采集（按 Ctrl+C 停止）
python triple_screen/fetch_tick_data.py

# 采集指定时长后自动退出（日盘365分钟 / 夜盘125分钟）
python triple_screen/fetch_tick_data.py --duration 365 --session day
python triple_screen/fetch_tick_data.py --duration 125 --session night
```
Tick 数据保存至 `data/ticks/{品种}/{交易所}.{代码}/` 目录，按日期+时段命名（如 `2026-06-23_day.parquet`）。

### 查看 Tick 数据
```bash
python triple_screen/view_ticks.py              # 列出所有文件摘要
python triple_screen/view_ticks.py 棉花          # 查看棉花详细数据
python triple_screen/view_ticks.py --all         # 所有品种汇总
```

### 维护 Tick 数据
```bash
python triple_screen/maintain_data.py list              # 查看数据状态
python triple_screen/maintain_data.py merge --age 30  # 合并30天前数据
python triple_screen/maintain_data.py clean --age 30  # 清理已归档原始文件
./triple_screen/maintain_and_clean.sh 30             # 一键 merge + clean
```

---

## 数据来源

- **AkShare**（首选）：免费，无需账号，盘后也可获取。用于历史回测数据。
- **TqSdk**：实时行情（仅交易时段）。创建 `~/.futures_config.toml` 填写账号密码（参考 `triple_screen/config.toml.example`）。
- **Tushare**：备用，需配置 Token（`export TUSHARE_TOKEN="xxx"`）。

## 合约代码

| 品种 | AkShare 代码 | TqSdk 代码 |
|------|------------|----------|
| 棉花主力 | `CF0` | `CZCE.CF609` |
| 铁矿石主力 | `I0` | `DCE.i2609` |
| 玉米主力 | `C0` | `DCE.c2609` |
| 豆粕主力 | `M0` | `DCE.m2609` |
| 鸡蛋主力 | `JD0` | `DCE.jd2608` |

## 注意事项

- TqSdk **非交易时段**无法连接免费服务器，历史数据请用 AkShare
- 棉花合约乘数：5 元/吨，保证金约 10%
- 回测结果不代表实盘收益，仅供参考
- Tick 数据采集已配置自动化：日盘 9:05 / 夜盘 21:05 自动触发
