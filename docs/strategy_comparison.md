# 三重滤网策略六版本详细对比

> 本文档详细记录六种三重滤网策略实现的逻辑、代码对照及回测结果。
> 
> **数据范围**: 2025-01-01 ~ 2026-06-18  
> **回测品种**: 棉花主力连续合约 (CF0)  
> **初始资金**: 100,000 元

---

## 目录

- [策略演进概述](#策略演进概述)
- [V0: 原始二层版](#v0-原始二层版)
- [V1: 三层版](#v1-三层版)
- [V2: 动力系统版](#v2-动力系统版)
- [V3: 短周期版](#v3-短周期版)
- [V4: 双层动力系统版](#v4-双层动力系统版)
- [V5: 短周期双层动力系统版](#v5-短周期双层动力系统版)
- [回测结果汇总](#回测结果汇总)
- [版本选择建议](#版本选择建议)

---

## 策略演进概述

| 版本 | 周期结构 | 核心改进 | 交易次数 | 状态 |
|------|----------|----------|----------|------|
| V0 | 周-日（二层） | 基准版本 | 12 | ✅ 参考基准 |
| V1 | 周-日-60min | +L3精细择时 | 15 | ✅ 收益最优 |
| V2 | 周-日-60min | L2改用动力系统 | 13 | ✅ 风控最优 |
| V3 | 日-小时-15min | 短周期，信号频繁 | 32 | ❌ 亏损，需优化 |
| V4 | 周-日-60min | L1+L2均为动力系统 | 7 | ⚠️ 回撤偏大 |
| V5 | 日-小时-15min | 短周期+动力系统 | 52 | ⚠️ 胜率低 |

---

## V0: 原始二层版

**文件**: `triple_screen_optimized.py`

### 周期结构

| 层级 | 周期 | 指标 | 作用 |
|------|------|------|------|
| L1 | 周线 | MACD柱斜率 | 判断主趋势方向 |
| L2 | 日线 | KD金叉/死叉 | 识别趋势中的回调结束 |
| L3 | ❌ 无 | — | 次日开盘价直接进场 |

### L1: 周线趋势判断

**代码位置**: `triple_screen_optimized.py` 第 131-140 行

```python
# 周线 MACD 柱斜率
weekly_macd_hist = week_data.iloc[-1]['macd_hist']
weekly_macd_slope = week_data.iloc[-1]['macd_slope']

if weekly_macd_hist > 0 and weekly_macd_slope > 0:
    weekly_trend = 'UP'
elif weekly_macd_hist < 0 and weekly_macd_slope < 0:
    weekly_trend = 'DOWN'
else:
    weekly_trend = 'NEUTRAL'
```

**MACD 参数**: `(8, 24, 9)` — 最快参数，对价格变化敏感

**周线计算**: 用 `resample('W')` 将日线聚合为周线

```python
def resample_to_weekly(df_daily):
    df = df_daily.copy()
    df.set_index('date', inplace=True)
    weekly = pd.DataFrame({
        'open': df['open'].resample('W').first(),
        'high': df['high'].resample('W').max(),
        'low': df['low'].resample('W').min(),
        'close': df['close'].resample('W').last(),
        'volume': df['volume'].resample('W').sum(),
    })
    weekly = weekly.dropna().reset_index()
    return weekly
```

### L2: 日线入场信号

**代码位置**: 第 143-156 行

```python
# 第二层滤网
daily_k = df_daily.iloc[i]['k']
daily_d = df_daily.iloc[i]['d']
daily_k_prev = df_daily.iloc[i-1]['k']
daily_d_prev = df_daily.iloc[i-1]['d']

# 交易信号
if position == 0:
    if weekly_trend == 'UP' and daily_k > daily_d and daily_k_prev <= daily_d_prev:
        # KD金叉，做多
    elif weekly_trend == 'DOWN' and daily_k < daily_d and daily_k_prev >= daily_d_prev:
        # KD死叉，做空
```

**KD 参数**: `(14, 3, 3)`

### 进场逻辑

**代码位置**: 第 150-156 行

```python
# 无第三层，次日开盘价直接进场
entry_price = df_daily.iloc[i+1]['open']
```

### 出场逻辑

**代码位置**: 第 157-200 行

```python
# 止损: 亏损 ≥ 3%
if (position == 'LONG' and (close - entry_price) / entry_price <= -STOP_LOSS_PCT) or \
   (position == 'SHORT' and (entry_price - close) / entry_price <= -STOP_LOSS_PCT):
    # 止损出场

# 止盈(多): 当日 K > 70 且 K 下穿 D
if position == 'LONG' and daily_k > PROFIT_K_THRESHOLD and daily_k < daily_d:
    # 止盈出场

# 止盈(空): 当日 K < 30 且 K 上穿 D
if position == 'SHORT' and daily_k < (100 - PROFIT_K_THRESHOLD) and daily_k > daily_d:
    # 止盈出场
```

### V0 回测结果

| 指标 | 数值 |
|------|------|
| 总收益率 | +7.86% |
| 胜率 | 66.7% |
| 盈亏比 | 2.45 |
| 最大回撤 | -5.73% |
| 交易次数 | 12 |

---

## V1: 三层版

**文件**: `triple_screen_3layer.py`

### 周期结构

| 层级 | 周期 | 指标 | 作用 |
|------|------|------|------|
| L1 | 周线 | MACD柱斜率 | 判断主趋势方向（同V0） |
| L2 | 日线 | KD金叉/死叉 | 识别趋势中的回调结束（同V0） |
| L3 | 60分钟 | 精细择时 | **避免追高杀低** |

### 改进点: L3 精细择时

**代码位置**: `triple_screen_3layer.py` 第 106-151 行

```python
def find_60min_entry(df_60min_day, signal_type, daily_close):
    """
    第三层滤网：在当日60分钟K线中找到精细进场点
    
    多头信号:
      - 优先: 开盘后第一根60分钟K线（积极进场）
      - 备选: 如果开盘跳空>1%，等回调——第一根阴线或KD回落后再进
    
    空头信号:
      - 优先: 开盘后第一根60分钟K线
      - 备选: 如果开盘跳空< -1%，等反弹——第一根阳线或KD反弹后再进
    """
    if df_60min_day is None or len(df_60min_day) == 0:
        return daily_close, -1, "无60分钟数据,使用日线收盘价"
    
    first_bar = df_60min_day.iloc[0]
    gap_pct = (first_bar['open'] - daily_close) / daily_close
    
    if signal_type == 'LONG':
        # 开盘跳空过高（>1%），等回调
        if gap_pct > 0.01:
            min_price = df_60min_day['low'].min()
            pullback_pct = (min_price - daily_close) / daily_close
            if min_price < first_bar['open']:
                # 找到回调低点，在此进场
                return min_price, 0, f"跳空{gap_pct*100:.1f}%,回调至{min_price:.0f}进场"
            else:
                return first_bar['close'], 0, f"跳空{gap_pct*100:.1f}%,无回调,开盘进"
        else:
            # 正常开盘，第一根60分钟Bar收盘价进场
            return first_bar['close'], 0, f"正常开盘,第一根60min Bar进(开:{first_bar['open']:.0f})"
```

### 60分钟数据获取

**代码位置**: 第 55-71 行

```python
def get_60min_data(symbol, start_date, end_date):
    """获取60分钟线，返回按日期分组的字典"""
    df = ak.futures_zh_minute_sina(symbol=symbol, period='60')
    df['datetime'] = pd.to_datetime(df['datetime'])
    df['date'] = df['datetime'].dt.normalize()
    # 过滤日期范围
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)
    df = df[(df['datetime'] >= start) & (df['datetime'] <= end + pd.Timedelta(days=1))]
    return df.sort_values('datetime').reset_index(drop=True)
```

### V1 回测结果

| 指标 | 数值 | vs V0 |
|------|------|-------|
| 总收益率 | **+14.03%** | +6.17% ✅ |
| 胜率 | 66.7% | 持平 |
| 盈亏比 | 4.60 | +87% ✅ |
| 最大回撤 | **-3.29%** | 改善 43% ✅ |
| 交易次数 | 15 | +3 |

**核心优势**: 避免在不利价位追入。例如 2026-01-07 那笔，开盘跳空 1.1%，V1 等回调至 14840 才进（V0 直接追在 14810 开盘价，结果止损出局）。

---

## V2: 动力系统版

**文件**: `triple_screen_impulse_v2.py`

### 周期结构

| 层级 | 周期 | 指标 | 作用 |
|------|------|------|------|
| L1 | 周线 | MACD柱斜率 | 判断主趋势方向（同V0） |
| L2 | 日线 | **动力系统颜色变化** | 替代KD金叉，更敏感 |
| L3 | 60分钟 | 精细择时 | 同V1 |

### 核心改进: L2 改用动力系统

**动力系统规则** (Elder《Come Into My Trading Room》):

| EMA(13) | MACD柱 | 颜色 | 含义 |
|-----------|--------|------|------|
| 上升 | 上升 | 🟢 绿柱 | 多头主导，持有多单 |
| 下降 | 下降 | 🔴 红柱 | 空头主导，持有空单 |
| 不同步 | 不同步 | 🔵 蓝柱 | 中性，不开新仓 |

**代码位置**: `triple_screen_impulse_v2.py` 第 93-115 行

```python
def calc_impulse(df):
    """
    计算动力系统颜色 (每日)
    返回: impulse_color 列: 'green', 'red', 'blue'
    """
    # 13日EMA
    ema13 = df['close'].ewm(span=EMA_IMPULSE, adjust=False).mean()
    df['ema13'] = ema13
    df['ema13_rising'] = ema13 > ema13.shift(1)
    
    # MACD柱 (12,26,9)
    _, _, macd_hist = calc_macd(df, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    df['macd_hist'] = macd_hist
    df['macd_hist_rising'] = macd_hist > macd_hist.shift(1)
    
    # 动力系统颜色
    conditions = [
        df['ema13_rising'] & df['macd_hist_rising'],   # 绿柱
        ~df['ema13_rising'] & ~df['macd_hist_rising'],  # 红柱
    ]
    choices = ['green', 'red']
    df['impulse'] = np.select(conditions, choices, default='blue')
    return df
```

### L2 入场信号

**代码位置**: 第 149-165 行

```python
# 第二层滤网：动力系统颜色变化
df_daily['impulse_prev'] = df_daily['impulse'].shift(1)

# 做多：周线UP + 动力系统 蓝→绿
if weekly_trend == 'UP' and \
   df_daily.iloc[i-1]['impulse'] == 'blue' and \
   df_daily.iloc[i]['impulse'] == 'green':
    # 做多信号

# 做空：周线DOWN + 动力系统 蓝→红
if weekly_trend == 'DOWN' and \
   df_daily.iloc[i-1]['impulse'] == 'blue' and \
   df_daily.iloc[i]['impulse'] == 'red':
    # 做空信号
```

### 出场逻辑改进

**V2 的平仓逻辑与V0/V1不同**——使用动力系统颜色反转作为止盈信号：

```python
# 止损: 亏损 ≥ 3% (同V0/V1)

# 止盈: 动力系统颜色反转即出场（不贪最后一棒）
if position == 'LONG':
    # 绿→蓝 或 绿→红 → 平仓
    if (prev_impulse == 'green' and impulse in ['blue', 'red']):
        # 止盈出场
        
if position == 'SHORT':
    # 红→蓝 或 红→绿 → 平仓
    if (prev_impulse == 'red' and impulse in ['blue', 'green']):
        # 止盈出场
```

### V2 回测结果

| 指标 | 数值 | vs V0 | vs V1 |
|------|------|-------|-------|
| 总收益率 | +11.69% | +3.83% | -2.34% |
| 胜率 | **69.2%** | +2.5% | +2.5% |
| 盈亏比 | **8.64** | +252% ✅ | +88% ✅ |
| 最大回撤 | **-2.25%** | 改善61% ✅ | 改善32% ✅ |
| 交易次数 | 13 | +1 | -2 |

**核心优势**: 盈亏比最高（8.64），最大回撤最小（-2.25%）。动力系统在动能衰竭时就平仓，不贪最后一棒。

---

## V3: 短周期版

**文件**: `triple_screen_v3.py`

### 周期结构

| 层级 | 周期 | 指标 | 作用 |
|------|------|------|------|
| L1 | **日线** | MACD柱斜率 | 判断主趋势（周期缩短！） |
| L2 | **小时线** | KD金叉/死叉 或 动力系统 | 识别回调结束 |
| L3 | **15分钟** | 精细择时 | 避免追高杀低 |

### 设计目标

缩短周期 → 信号更频繁 → **交易次数增加**

### L1: 日线趋势判断

**代码位置**: `triple_screen_v3.py` 第 98-109 行

```python
# L1: 日线
df_d = get_daily(symbol, start_date, end_date)
df_d = compute_macd(df_d)
df_d['macd_slope'] = df_d['macd_hist'].diff()
df_d['trend'] = 'NEUTRAL'
df_d.loc[(df_d['macd_hist']>0)&(df_d['macd_slope']>0), 'trend'] = 'UP'
df_d.loc[(df_d['macd_hist']<0)&(df_d['macd_slope']<0), 'trend'] = 'DOWN'
```

### L2: 小时线入场信号

**代码位置**: 第 141-152 行

```python
if not use_impulse:
    # KD 金叉/死叉（日线KD，小时线数据已有KD）
    if trend == 'UP' and prev['k'] <= prev['d'] and row['k'] > row['d']:
        signal = 'LONG'
    elif trend == 'DOWN' and prev['k'] >= prev['d'] and row['k'] < row['d']:
        signal = 'SHORT'
else:
    # 动力系统: 蓝→绿做多，蓝→红做空
    if trend == 'UP' and prev['impulse'] == 'blue' and row['impulse'] == 'green':
        signal = 'LONG'
    elif trend == 'DOWN' and prev['impulse'] == 'blue' and row['impulse'] == 'red':
        signal = 'SHORT'
```

### L3: 15分钟精细择时

**代码位置**: 第 163-188 行

```python
# L3: 15分钟精细择时
next_date = pd.Timestamp(df_d.iloc[next_i]['date']).date()
m15_next = df_m15[df_m15['date'] == next_date]

if len(m15_next) > 0:
    open_price = df_d.iloc[next_i]['open']
    if signal == 'LONG':
        # 正常开盘 or 小幅跳空 → 首根15min收盘进
        if abs(open_price - m15_next.iloc[0]['close']) / open_price < 0.01:
            ep = m15_next.iloc[0]['close']
        else:
            # 跳空较大，等15min KD回调（K<D后再K>D）
            for j in range(1, min(len(m15_next), 20)):
                if m15_next.iloc[j]['k'] > m15_next.iloc[j]['d']:
                    ep = m15_next.iloc[j]['close']
                    break
```

### V3 回测结果

| 指标 | KD版 | 动力版 |
|------|-------|---------|
| 总收益率 | **-5.04%** | ? |
| 胜率 | 53.1% | ? |
| 盈亏比 | 0.71 | ? |
| 交易次数 | 32 | 32 |

**问题**: 
1. 胜率仅 53.1%（V1 是 66.7%）
2. 盈亏比 0.71（小于 1，每亏 1 元只赚 0.71 元）
3. 很多交易当天进出，15 分钟精细择时没起作用

**原因**: 周期缩短后，噪音变多，小时线KD信号不可靠。

---

## V4: 双层动力系统版

**文件**: `triple_screen_v4.py`

### 周期结构

| 层级 | 周期 | 指标 | 作用 |
|------|------|------|------|
| L1 | **周线** | **动力系统** | 判断主趋势（改用动力系统！） |
| L2 | **日线** | **动力系统** | 颜色变化入场信号 |
| L3 | 60分钟 | 精细择时 | 同V1/V2 |

### 与V2的区别

| | V2 | V4 |
|---|-----|-----|
| L1 | 周线MACD斜率 | **周线动力系统** |
| L2 | 日线动力系统 | 日线动力系统（相同） |
| 逻辑 | 混合 | **双层动力系统，更一致** |

### L1: 周线动力系统

**代码位置**: `triple_screen_v4.py` 第 84-109 行

```python
def get_weekly_impulse(symbol: str, start: str, end: str) -> pd.DataFrame:
    """获取周线级别的动力系统颜色"""
    df = get_daily(symbol, start, end)
    df = compute_impulse(df)
    
    # 用每年第几周分组，取每周最后一根日线的 impulse 作为周线 impulse
    df['year_week'] = df['date'].dt.strftime('%Y-%U')
    weekly = df.groupby('year_week').last().reset_index()
    weekly['date'] = pd.to_datetime(weekly['date'])
    weekly = weekly[['date', 'impulse', 'close']].rename(columns={'impulse': 'weekly_impulse'})
    return weekly

def get_weekly_trend(weekly_impulse: pd.DataFrame, lookback: int = WEEKLY_IMPULSE_LOOKBACK) -> str:
    """根据最近N周的动力系统颜色判断主趋势"""
    recent = weekly_impulse.tail(lookback)
    greens = (recent['weekly_impulse'] == 'green').sum()
    reds = (recent['weekly_impulse'] == 'red').sum()
    
    if greens >= lookback:
        return 'UP'
    elif reds >= lookback:
        return 'DOWN'
    else:
        return 'NEUTRAL'
```

### L2: 日线动力系统信号

**代码位置**: 第 169-172 行

```python
# 日线 impulse 信号: 蓝→绿 / 蓝→红
df_daily['impulse_prev'] = df_daily['impulse'].shift(1)
signal_long = (df_daily['impulse_prev'] == 'blue') & (df_daily['impulse'] == 'green')
signal_short = (df_daily['impulse_prev'] == 'blue') & (df_daily['impulse'] == 'red')
```

### L3: 60分钟精细择时

**代码位置**: 第 112-143 行

```python
def get_l3_entry_price(hourly_df: pd.DataFrame, sig_date: pd.Timestamp,
                        direction: str, open_price: float) -> tuple:
    """L3: 60分钟精细择时，返回(进场价, 说明)"""
    day_data = hourly_df[hourly_df['datetime'].dt.date == sig_date.date()].copy()
    if day_data.empty:
        return open_price, '无60分钟数据,使用开盘价'
    
    # 首根60分钟Bar收盘价
    first_bar = day_data.iloc[0]
    entry_from_1st_bar = first_bar['close']
    
    gap_pct = (entry_from_1st_bar - open_price) / open_price
    
    if direction == 'LONG':
        if gap_pct > 0.01:
            # 跳空高开 >1%，等回调（当日最低价）
            day_low = day_data['low'].min()
            entry = min(entry_from_1st_bar, day_low)
            reason = f'跳空高开{gap_pct:.2%}, 等回调至{min(entry_from_1st_bar, day_low):.0f}'
        else:
            entry = entry_from_1st_bar
            reason = f'首根60min Bar收盘价 {entry_from_1st_bar:.0f}'
```

### V4 回测结果

| 指标 | 数值 | vs V2 |
|------|------|-------|
| 总收益率 | +9.88% | -1.81% |
| 胜率 | **71.4%** | +2.2% ✅ |
| 盈亏比 | **30.00** | +247% ✅✅ | 
| 最大回撤 | -8.99% | 劣化300% ❌ |
| 交易次数 | 7 | -6 |

**特点**: 
- 盈亏比极高（30.00），因为亏损交易的止损很小
- 交易次数少（7笔），非常谨慎
- **最大回撤偏大（-8.99%）**，需要优化止损逻辑

---

## V5: 短周期双层动力系统版

**文件**: `triple_screen_v5.py`

### 周期结构

| 层级 | 周期 | 指标 | 作用 |
|------|------|------|------|
| L1 | **日线** | **动力系统** | 判断主趋势（周期缩短！） |
| L2 | **小时线** | **动力系统** | 颜色变化入场信号 |
| L3 | **15分钟** | 精细择时 | 避免追高杀低 |

### 设计思路

将V4的双层动力系统逻辑，搬到短周期（日-小时-15分钟），以**增加交易频率**。

### L1: 日线动力系统

**代码位置**: `triple_screen_v5.py` 第 56-63 行

```python
def get_daily(symbol, start, end):
    df = ak.futures_main_sina(symbol=symbol, start_date=start, end_date=end)
    df = df.rename(columns={'日期':'date','开盘价':'open','最高价':'high',
                            '最低价':'low','收盘价':'close',
                            '成交量':'volume','持仓量':'hold'})
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    return compute_impulse(df)  # 计算动力系统
```

### L2: 小时线动力系统信号

**代码位置**: 第 200-210 行

```python
# 开仓逻辑
if not in_pos:
    # L1: 日线趋势
    daily_trend = get_daily_trend_at(df_daily, h_dt)
    
    # L2: 小时线动力系统信号
    if daily_trend == 'UP' and h_impulse_prev == 'blue' and h_impulse == 'green':
        # 做多
    elif daily_trend == 'DOWN' and h_impulse_prev == 'blue' and h_impulse == 'red':
        # 做空
```

### L3: 15分钟精细择时

**代码位置**: 第 101-128 行

```python
def get_l3_entry_15min(min15_df, entry_dt, direction):
    """在 entry_dt 附近用15分钟数据精细择时"""
    target_date = entry_dt.date()
    day_data = min15_df[min15_df['datetime'].dt.date == target_date].copy()
    
    if day_data.empty:
        # 可能是夜盘，找下一交易日
        for offset in range(1, 4):
            next_date = target_date + pd.Timedelta(days=offset)
            day_data = min15_df[min15_df['datetime'].dt.date == next_date].copy()
            if not day_data.empty:
                break
    
    if day_data.empty:
        return None, '无15分钟数据'
    
    # 找 entry_dt 之后的第一根15分钟Bar
    future_bars = day_data[day_data['datetime'] >= entry_dt].copy()
    if future_bars.empty:
        entry_bar = day_data.iloc[0]
    else:
        entry_bar = future_bars.iloc[0]
    
    entry_price = entry_bar['close']
    reason = f'15min Bar@{entry_bar["datetime"].strftime("%H:%M")} 收盘价{entry_price:.0f}'
    return entry_price, reason
```

### 出场逻辑

**代码位置**: 第 168-197 行

```python
# 止损: 亏损 ≥ 1.5% (V5使用更紧的止损)
if pnl_pct <= -STOP_LOSS_PCT * 100:
    # 止损出场

# 止盈: 小时线动力系统颜色反转
if in_pos == 'LONG' and h_impulse in ['red', 'blue']:
    # 小时线→红/蓝，止盈出场
    
if in_pos == 'SHORT' and h_impulse in ['green', 'blue']:
    # 小时线→绿/蓝，止盈出场
```

### V5 回测结果

| 指标 | 数值 | vs V4 |
|------|------|-------|
| 总收益率 | +4.93% | -4.95% |
| 胜率 | 38.5% | -32.9% ❌ |
| 盈亏比 | 1.67 | -28.33 ❌ |
| 最大回撤 | -6.03% | +2.96% |
| 交易次数 | 52 | +45 |

**问题**: 
1. **胜率仅 38.5%**（小时线动力系统噪音太大）
2. 信号过于频繁（52笔）→ 手续费和滑点会进一步侵蚀利润
3. 需要增加过滤条件（例如要求小时线 impulse 连续 2 根才确认信号）

---

## 回测结果汇总

### 六版本完整对比（2025-01 ~ 2026-06，棉花 CF0）

| 版本 | 名称 | 收益率 | 胜率 | 盈亏比 | 最大回撤 | 交易次数 | 推荐度 |
|------|------|--------|------|--------|----------|----------|---------|
| **V0** | 原始二层版 | +7.86% | 66.7% | 2.45 | -5.73% | 12 | ⭐⭐ |
| **V1** | 三层版 | **+14.03%** 🏆 | 66.7% | 4.60 | -3.29% | 15 | ⭐⭐⭐⭐⭐ |
| **V2** | 动力系统版 | +11.69% | **69.2%** | **8.64** 🏆 | **-2.25%** 🏆 | 13 | ⭐⭐⭐⭐⭐ |
| **V3** | 短周期版(KD) | -5.04% ❌ | 53.1% | 0.71 | ? | 32 | ⭐ |
| **V4** | 双层动力系统 | +9.88% | **71.4%** | **30.00** 🏆🏆 | -8.99% ❌ | 7 | ⭐⭐⭐ |
| **V5** | 短周期双层 | +4.93% | 38.5% ❌ | 1.67 | -6.03% | 52 | ⭐⭐ |

### 分维度排名

| 维度 | 第1名 | 第2名 | 第3名 |
|------|--------|--------|--------|
| **收益率** | V1 (+14.03%) | V2 (+11.69%) | V4 (+9.88%) |
| **胜率** | V4 (71.4%) | V2 (69.2%) | V1 (66.7%) |
| **盈亏比** | V4 (30.00) | V2 (8.64) | V1 (4.60) |
| **最大回撤(最小)** | V2 (-2.25%) | V1 (-3.29%) | V0 (-5.73%) |
| **交易次数(多)** | V5 (52) | V3 (32) | V1 (15) |

---

## 版本选择建议

### 根据风险偏好选择

| 风险偏好 | 推荐版本 | 理由 |
|----------|----------|------|
| **稳健型** | **V2** | 最大回撤最小(-2.25%)，盈亏比高(8.64) |
| **收益型** | **V1** | 收益率最高(+14.03%)，交易次数适中(15) |
| **高频型** | V3/V5（需优化） | 交易次数多，但目前亏损，不推荐实盘 |

### 根据资金规模选择

| 资金规模 | 推荐版本 | 理由 |
|----------|----------|------|
| **小资金(<10万)** | V1 | 收益高，交易次数适中，资金利用率高 |
| **中资金(10-50万)** | V2 | 风控好，回撤小，适合不加杠杆 |
| **大资金(>50万)** | V2 + V1 组合 | V2为主（70%资金），V1为辅（30%资金） |

### 实盘部署建议

1. **先用V2模拟盘验证**（最大回撤小，更安全）
2. **验证通过后，V1和V2各分配50%资金**
3. **定期（每月）运行`run_backtest.py compare`对比表现**
4. **V3/V5暂不推荐实盘**（亏损中，需优化）

---

## 附录: 指标计算代码汇总

### MACD计算（所有版本通用）

```python
def calc_macd(df, fast=8, slow=24, signal=9):
    """计算MACD"""
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd_hist = 2 * (dif - dea)
    return dif, dea, macd_hist
```

### KD计算（V0/V1/V3）

```python
def calc_kd(df, period=14, ma1=3, ma2=3):
    """计算KD"""
    low_n = df['low'].rolling(window=period).min()
    high_n = df['high'].rolling(window=period).max()
    rsv = (df['close'] - low_n) / (high_n - low_n) * 100
    k = rsv.ewm(span=ma1, adjust=False).mean()
    d = k.ewm(span=ma2, adjust=False).mean()
    return k, d
```

### 动力系统计算（V2/V4/V5）

```python
def compute_impulse(df):
    """动力系统颜色: green/red/blue"""
    df = compute_ema(df, 13)
    df = compute_macd(df, 12, 26, 9)
    df['ema_up'] = df['ema_13'] > df['ema_13'].shift(1)
    df['macd_up'] = df['macd_hist'] > df['macd_hist'].shift(1)
    df['impulse'] = 'blue'
    df.loc[df['ema_up'] & df['macd_up'], 'impulse'] = 'green'
    df.loc[~df['ema_up'] & ~df['macd_up'], 'impulse'] = 'red'
    return df
```

---

## 文档维护记录

| 日期 | 版本 | 修改内容 |
|------|------|----------|
| 2026-06-23 | v1.0 | 初始版本，记录V0-V5六个版本的详细逻辑 |

---

**文件路径**: `docs/strategy_comparison.md`  
**维护人**: Michael He  
**最后更新**: 2026-06-23
