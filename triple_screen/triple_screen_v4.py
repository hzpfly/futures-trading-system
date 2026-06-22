#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三重滤网 V4 —— L1+L2 均为动力系统，L3 60分钟精细择时

周期: 周线(L1) → 日线(L2) → 60分钟(L3)

V4 特色:
  - L1(周线): 动力系统判断主趋势（绿柱=多头, 红柱=空头）
  - L2(日线): 动力系统颜色变化（蓝→绿做多, 蓝→红做空）
  - L3(60分钟): 精细择时，避免追高杀低
  - 平仓: 日线动力系统颜色反转即出场

与 V2 的区别:
  - V2: L1=周线MACD斜率, L2=日线动力系统
  - V4: L1=周线动力系统, L2=日线动力系统（两层一致）
"""
import sys
import os
import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# ── 全局参数 ────────────────────────────────────────────────────────────────
EMA_PERIOD = 13
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
STOP_LOSS_PCT = 0.03        # 止损 3%
WEEKLY_IMPULSE_LOOKBACK = 1  # 周线需连续N根绿/红柱才确认趋势（改小以便出信号）

# ── 指标计算 ─────────────────────────────────────────────────────────────────
def compute_ema(df: pd.DataFrame, period: int = EMA_PERIOD) -> pd.DataFrame:
    df = df.copy()
    df[f'ema_{period}'] = df['close'].ewm(span=period, adjust=False).mean()
    return df

def compute_macd(df: pd.DataFrame,
                 fast: int = MACD_FAST,
                 slow: int = MACD_SLOW,
                 signal: int = MACD_SIGNAL) -> pd.DataFrame:
    df = df.copy()
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    df['macd'] = ema_fast - ema_slow
    df['macd_signal'] = df['macd'].ewm(span=signal, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']
    return df

def compute_impulse(df: pd.DataFrame) -> pd.DataFrame:
    """计算动力系统颜色: green / red / blue"""
    df = df.copy()
    df = compute_ema(df)
    df = compute_macd(df)

    ema_up = df[f'ema_{EMA_PERIOD}'] > df[f'ema_{EMA_PERIOD}'].shift(1)
    macd_up = df['macd_hist'] > df['macd_hist'].shift(1)

    df['impulse'] = 'blue'
    df.loc[ema_up & macd_up, 'impulse'] = 'green'
    df.loc[~ema_up & ~macd_up, 'impulse'] = 'red'
    return df

# ── 数据获取 ─────────────────────────────────────────────────────────────────
def get_daily(symbol: str, start: str, end: str) -> pd.DataFrame:
    df = ak.futures_main_sina(symbol=symbol, start_date=start, end_date=end)
    df = df.rename(columns={'日期': 'date', '开盘价': 'open', '最高价': 'high',
                            '最低价': 'low', '收盘价': 'close',
                            '成交量': 'volume', '持仓量': 'hold'})
    df['date'] = pd.to_datetime(df['date'])
    return df.sort_values('date').reset_index(drop=True)

def get_hourly(symbol: str, start: str, end: str) -> pd.DataFrame:
    df = ak.futures_zh_minute_sina(symbol=symbol, period='60')
    df['datetime'] = pd.to_datetime(df['datetime'])
    mask = (df['datetime'] >= pd.Timestamp(start)) & (df['datetime'] <= pd.Timestamp(end))
    df = df[mask].copy()
    df = df.rename(columns={'open': 'open', 'high': 'high',
                            'low': 'low', 'close': 'close',
                            'volume': 'volume'})
    return df.sort_values('datetime').reset_index(drop=True)

# ── 周线动力系统 ─────────────────────────────────────────────────────────────
def get_weekly_impulse(symbol: str, start: str, end: str) -> pd.DataFrame:
    """获取周线级别的动力系统颜色"""
    df = get_daily(symbol, start, end)
    df = compute_impulse(df)

    # 用每年第几周分组，取每周最后一根日线的 impulse 作为周线 impulse
    df['year_week'] = df['date'].dt.strftime('%Y-%U')
    weekly = df.groupby('year_week').last().reset_index()
    # 用每周最后一根日线的 date 作为周线 date
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

# ── L3 精细择时 ──────────────────────────────────────────────────────────────
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
    else:  # SHORT
        if gap_pct < -0.01:
            day_high = day_data['high'].max()
            entry = max(entry_from_1st_bar, day_high)
            reason = f'跳空低开{gap_pct:.2%}, 等反弹至{max(entry_from_1st_bar, day_high):.0f}'
        else:
            entry = entry_from_1st_bar
            reason = f'首根60min Bar收盘价 {entry_from_1st_bar:.0f}'

    return entry, reason

# ── 回测核心 ─────────────────────────────────────────────────────────────────
def run_v4_backtest(symbol: str = 'CF0',
                     start: str = '20250101',
                     end: str = '20260620') -> tuple:
    """运行 V4 回测"""
    print(f"\n{'='*60}")
    print(f"【回测】V4（L1+L2动力系统, L3 60分钟精细择时）")
    print(f"品种: {symbol} | 周期: {start}~{end}")
    print(f"{'='*60}\n")

    # ── 数据 ──
    print("获取日线数据...")
    df_daily = get_daily(symbol, start, end)
    df_daily = compute_impulse(df_daily)
    print(f"  日线: {len(df_daily)} 根, {df_daily['date'].min().date()} ~ {df_daily['date'].max().date()}")

    print("获取周线动力系统...")
    weekly = get_weekly_impulse(symbol, start, end)
    print(f"  周线: {len(weekly)} 根, 最新: {weekly.iloc[-1]['weekly_impulse']}")

    print("获取60分钟数据（L3）...")
    hourly = get_hourly(symbol, start, end)
    print(f"  60分钟: {len(hourly)} 根\n")

    # ── 日线 impulse 信号: 蓝→绿 / 蓝→红 ──
    df_daily['impulse_prev'] = df_daily['impulse'].shift(1)
    signal_long = (df_daily['impulse_prev'] == 'blue') & (df_daily['impulse'] == 'green')
    signal_short = (df_daily['impulse_prev'] == 'blue') & (df_daily['impulse'] == 'red')

    # ── 回测循环 ──
    trades = []
    equity = [1.0]
    in_pos = None
    ep = xp = 0.0
    reason_in = reason_out = ''
    entry_date = None

    for i in range(1, len(df_daily)):
        row = df_daily.iloc[i]
        prev = df_daily.iloc[i - 1]
        d = row['date']
        close = row['close']
        open_price = row['open']

        # 周线趋势（用截止到昨天的周线数据）
        weekly_hist = weekly[weekly['date'] < d].copy()
        if len(weekly_hist) >= WEEKLY_IMPULSE_LOOKBACK:
            weekly_trend = get_weekly_trend(weekly_hist, WEEKLY_IMPULSE_LOOKBACK)
        else:
            weekly_trend = 'NEUTRAL'

        # ── 平仓逻辑 ──
        if in_pos:
            bars_held = (d - entry_date).days
            pnl = (close - ep) / ep if in_pos == 'LONG' else (ep - close) / ep
            pnl_pct = pnl * 100

            # 止损
            if pnl_pct <= -STOP_LOSS_PCT * 100:
                reason_out = f'止损 {pnl_pct:.2f}%'
                xp = close
                trades.append({'entry': entry_date, 'exit': d, 'dir': in_pos,
                               'ep': ep, 'xp': xp, 'pnl': pnl_pct, 'reason': reason_out,
                               'bars': bars_held})
                in_pos = None
                equity.append(equity[-1] * (1 + pnl_pct / 100))
                continue

            # 止盈: 日线动力系统颜色反转
            if in_pos == 'LONG' and row['impulse'] in ['red', 'blue']:
                reason_out = f'动力系统反转至{row["impulse"]}'
                xp = close
                trades.append({'entry': entry_date, 'exit': d, 'dir': in_pos,
                               'ep': ep, 'xp': xp, 'pnl': pnl_pct, 'reason': reason_out,
                               'bars': bars_held})
                in_pos = None
                equity.append(equity[-1] * (1 + pnl_pct / 100))
                continue

            if in_pos == 'SHORT' and row['impulse'] in ['green', 'blue']:
                reason_out = f'动力系统反转至{row["impulse"]}'
                xp = close
                trades.append({'entry': entry_date, 'exit': d, 'dir': in_pos,
                               'ep': ep, 'xp': xp, 'pnl': pnl_pct, 'reason': reason_out,
                               'bars': bars_held})
                in_pos = None
                equity.append(equity[-1] * (1 + pnl_pct / 100))
                continue

        # ── 开仓逻辑 ──
        if not in_pos:
            # L1: 周线动力系统确认主趋势
            if weekly_trend == 'UP' and signal_long.iloc[i]:
                # L3: 精细择时
                next_date = df_daily.iloc[i + 1]['date'] if i + 1 < len(df_daily) else d
                entry_price, l3_reason = get_l3_entry_price(hourly, next_date, 'LONG', open_price)

                in_pos = 'LONG'
                ep = entry_price
                entry_date = next_date
                reason_in = f'周线{weekly_trend}+日线蓝→绿, L3:{l3_reason}'
                print(f"  📈 开多 {entry_date.date()} @ {ep:.0f}  ({reason_in})")

            elif weekly_trend == 'DOWN' and signal_short.iloc[i]:
                next_date = df_daily.iloc[i + 1]['date'] if i + 1 < len(df_daily) else d
                entry_price, l3_reason = get_l3_entry_price(hourly, next_date, 'SHORT', open_price)

                in_pos = 'SHORT'
                ep = entry_price
                entry_date = next_date
                reason_in = f'周线{weekly_trend}+日线蓝→红, L3:{l3_reason}'
                print(f"  📉 开空 {entry_date.date()} @ {ep:.0f}  ({reason_in})")

    # 强制平仓
    if in_pos:
        last_close = df_daily.iloc[-1]['close']
        pnl = (last_close - ep) / ep if in_pos == 'LONG' else (ep - last_close) / ep
        trades.append({'entry': entry_date, 'exit': df_daily.iloc[-1]['date'],
                       'dir': in_pos, 'ep': ep, 'xp': last_close,
                       'pnl': pnl * 100, 'reason': '期末强制平仓',
                       'bars': (df_daily.iloc[-1]['date'] - entry_date).days})

    # ── 统计 ──
    total = len(trades)
    wins = sum(1 for t in trades if t['pnl'] > 0)
    win_rate = wins / total * 100 if total else 0
    gross_win = sum(t['pnl'] for t in trades if t['pnl'] > 0)
    gross_loss = abs(sum(t['pnl'] for t in trades if t['pnl'] <= 0))
    pf = gross_win / gross_loss if gross_loss else float('inf')
    cum = [1.0]
    for t in trades:
        cum.append(cum[-1] * (1 + t['pnl'] / 100))
    max_eq = max(cum)
    max_dd = min([(x - max_eq) / max_eq * 100 for x in cum]) if max_eq > 1 else 0

    print(f"\n{'='*60}")
    print(f"【回测报告】V4（L1+L2动力系统）")
    print(f"{'='*60}")
    print(f"  交易次数: {total}")
    if total > 0:
        print(f"  胜率: {win_rate:.1f}%")
        print(f"  盈亏比: {pf:.2f}")
        print(f"  总收益率: {cum[-1]*100-100:.2f}%")
        print(f"  最大回撤: {max_dd:.2f}%")
        print(f"  平均盈亏: {(cum[-1]*100-100)/total:.2f}%/笔")
    else:
        print("  ⚠️ 无交易信号，请检查策略参数")
    print()
    for t in trades:
        emoji = "✅" if t['pnl'] > 0 else "❌"
        print(f"  {emoji} {str(t['entry'])[:10]}→{str(t['exit'])[:10]} {t['dir']:5s} "
              f"{t['ep']:.0f}→{t['xp']:.0f} {t['pnl']:+.2f}% [{t['reason']}]")
    print()
    return trades, cum

if __name__ == '__main__':
    run_v4_backtest()
