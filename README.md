# 期货波段交易系统

基于 Alexander Elder 三重滤网（Triple Screen）理论的期货交易系统，覆盖棉花、铁矿石等农产品期货。

## 项目结构

```
futures-trading-system/
├── triple_screen/                # 三重滤网策略（六个版本）
│   ├── triple_screen_optimized.py   # V0: 原始二层版
│   ├── triple_screen_3layer.py      # V1: 三层版（60分钟精细择时）
│   ├── triple_screen_impulse_v2.py  # V2: 动力系统版
│   ├── triple_screen_v3.py          # V3: 短周期版（日-小时-15分钟）
│   ├── triple_screen_v4.py          # V4: 双层动力系统版
│   ├── triple_screen_v5.py          # V5: 短周期双层动力系统版
│   ├── run_backtest.py             # 统一回测入口（支持六版本对比）
│   ├── triple_screen_optimize.py    # 参数优化网格搜索
│   ├── triple_screen_akshare.py     # 初始版本（基础逻辑）
│   ├── backtest_from_parquet.py     # 基于本地Parquet数据的回测
│   ├── triple_screen_tqsdk.py      # TqSdk数据源版回测
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
├── docs/                       # 策略文档
│   └── strategy_comparison.md  # 六版本详细对比（含代码对照）
├── data/                       # 历史数据（不上传Git）
│   ├── kline/                 # K线历史数据（Parquet，不上传Git）
│   └── ticks/                # Tick逐笔数据（Parquet，不上传Git）
│
└── reports/                   # 回测报告（不上传Git）
```

---

## 三重滤网策略 - 六个版本

> 📖 **详细逻辑对照代码实现** → [docs/strategy_comparison.md](docs/strategy_comparison.md)

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

### V3：短周期版 `triple_screen_v3.py`

缩短周期 → 信号更频繁 → **交易次数增加**。

| 层级 | 周期 | 指标 | 作用 |
|------|------|------|------|
| L1 | **日线** | MACD(8,24,9) 柱斜率 | 判断主趋势（周期缩短！） |
| L2 | **小时线** | KD(14,3,3) 金叉/死叉 或 动力系统 | 识别回调结束 |
| **L3** | **15分钟** | **精细择时** | **避免追高杀低** |

**两种模式：**
- `use_impulse=False`（默认）：L2 用 KD 金叉/死叉
- `use_impulse=True`：L2 用动力系统颜色变化

**注意**：V3 当前回测收益率为负（-5.04%），**不推荐实盘**，需进一步优化过滤条件。

- 文件：`triple_screen/triple_screen_v3.py`
- 运行：
  ```bash
  python triple_screen/run_backtest.py v3    # KD版
  python triple_screen/run_backtest.py v3i   # 动力系统版
  ```

### V4：双层动力系统版 `triple_screen_v4.py`

V2 的改进版：**L1 和 L2 均为动力系统**，判断更一致。

| 层级 | 周期 | 指标 | 作用 |
|------|------|------|------|
| L1 | **周线** | **动力系统** | 判断主趋势（改用动力系统！） |
| L2 | **日线** | **动力系统** | 颜色变化入场信号 |
| L3 | 60分钟 | 精细择时 | 同 V1/V2 |

**与 V2 的区别：**
| | V2 | V4 |
|---|-----|-----|
| L1 | 周线MACD斜率 | **周线动力系统** |
| L2 | 日线动力系统 | 日线动力系统（相同） |
| 逻辑 | 混合 | **双层动力系统，更一致** |

- 文件：`triple_screen/triple_screen_v4.py`
- 运行：`python triple_screen/run_backtest.py v4`

### V5：短周期双层动力系统版 `triple_screen_v5.py`

将 V4 的双层动力系统逻辑搬到短周期（日-小时-15分钟），以**增加交易频率**。

| 层级 | 周期 | 指标 | 作用 |
|------|------|------|------|
| L1 | **日线** | **动力系统** | 判断主趋势（周期缩短！） |
| L2 | **小时线** | **动力系统** | 颜色变化入场信号 |
| L3 | **15分钟** | 精细择时 | 避免追高杀低 |

**驱动方式**：以小时线为时间轴，每个小时 Bar 检查信号。

**注意**：小时线动力系统噪音太大，胜率仅 38.5%，**不推荐实盘**，需增加过滤条件（如要求小时线 impulse 连续 2 根才确认信号）。

- 文件：`triple_screen/triple_screen_v5.py`
- 运行：`python triple_screen/run_backtest.py v5`

---

## 六版本回测对比

> 📖 **详细逻辑对照代码实现** → [docs/strategy_comparison.md](docs/strategy_comparison.md)

> 回测区间：2025-01 ~ 2026-06，品种：棉花主力 CF0，初始资金：100,000 元

> 回测区间：2025-01 ~ 2026-06，品种：棉花主力 CF0，初始资金：100,000 元

| 指标 | V0 原始二层 | V1 三层 | V2 动力系统 | V3 短周期(KD) | V4 双层动力 | V5 短周期动力 |
|------|------------|--------|-------------|---------------|-------------|-----------------|
| **收益率** | +7.86% | **+14.03%** 🏆 | +11.69% | -5.04% ❌ | +9.88% | +4.93% |
| **胜率** | 66.7% | 66.7% | 69.2% | 53.1% | **71.4%** 🏆 | 38.5% ❌ |
| **盈亏比** | 2.45 | 4.60 | 8.64 | 0.71 | **30.00** 🏆🏆 | 1.67 |
| **最大回撤** | -5.73% | -3.29% | **-2.25%** 🏆 | ? | -8.99% ❌ | -6.03% |
| **交易次数** | 12 | 15 | 13 | 32 | 7 | **52** |
| **状态** | ✅ 基准 | ✅ 收益最优 | ✅ 风控最优 | ❌ 亏损中 | ⚠️ 回撤大 | ⚠️ 胜率低 |

### 分维度排名

| 维度 | 🥇 第1名 | 🥈 第2名 | 🥉 第3名 |
|------|--------|--------|--------|
| **收益率** | V1 (+14.03%) | V2 (+11.69%) | V4 (+9.88%) |
| **胜率** | V4 (71.4%) | V2 (69.2%) | V1 (66.7%) |
| **盈亏比** | V4 (30.00) | V2 (8.64) | V1 (4.60) |
| **最大回撤(最小)** | V2 (-2.25%) | V1 (-3.29%) | V0 (-5.73%) |
| **交易次数(多)** | V5 (52) | V3 (32) | V1 (15) |

### 版本选择建议

| 风险偏好 | 推荐版本 | 理由 |
|----------|----------|------|
| **稳健型** | **V2** | 最大回撤最小(-2.25%)，盈亏比高(8.64) |
| **收益型** | **V1** | 收益率最高(+14.03%)，交易次数适中(15) |
| **高频型** | V3/V5（需优化） | 交易次数多，但目前亏损，不推荐实盘 |

**实盘部署建议：**
1. **先用 V2 模拟盘验证**（最大回撤小，更安全）
2. **验证通过后，V1 和 V2 各分配 50% 资金**
3. **定期（每月）运行** `python triple_screen/run_backtest.py compare` **对比表现**
4. **V3/V5 暂不推荐实盘**（亏损中，需优化）

---

## 快速开始

### 安装依赖
```bash
pip install -r requirements.txt
```

### 运行回测
```bash
# 运行指定版本
python triple_screen/run_backtest.py v0        # V0: 原始二层版
python triple_screen/run_backtest.py v1        # V1: 三层版（60分钟精细择时）
python triple_screen/run_backtest.py v2        # V2: 动力系统版
python triple_screen/run_backtest.py v3        # V3: 短周期版（KD）
python triple_screen/run_backtest.py v3i       # V3: 短周期版（动力系统）
python triple_screen/run_backtest.py v4        # V4: 双层动力系统版
python triple_screen/run_backtest.py v5        # V5: 短周期双层动力系统版

# 六版本对比
python triple_screen/run_backtest.py compare

# 依次运行全部版本
python triple_screen/run_backtest.py all
```

> 📖 **详细逻辑对照代码实现** → [docs/strategy_comparison.md](docs/strategy_comparison.md)

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
