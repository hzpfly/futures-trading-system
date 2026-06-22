#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三重滤网 V5 —— 短周期双层动力系统（日-小时-15分钟）

周期: 日线(L1) → 小时线(L2) → 15分钟(L3)

逻辑:
  L1(日线): 动力系统判断主趋势
  L2(小时线): 动力系统颜色变化（蓝→绿做多, 蓝→红做空）
  L3(15分钟): 精细择时
  平仓: 小时线动力系统颜色反转即出场

驱动方式: 以小时线为时间轴，每个小时Bar检查信号
"""
import sys
import os
import akshare as ak
import pandas as pd
import numpy as np

# ── 全局参数 ────────────────────────────────────────────────────────────────
EMA_PERIOD = 13
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
STOP_LOSS_PCT = 0.015       # 止损 1.5%

# ── 指标计算 ─────────────────────────────────────────────────────────────────
def compute_ema(df, period=EMA_PERIOD):
    df = df.copy()
    df[f'ema_{period}'] = df['close'].ewm(span=period, adjust=False).mean()
    return df

def compute_macd(df, fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL):
    df = df.copy()
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    df['macd'] = ema_fast - ema_slow
    df['macd_signal'] = df['macd'].ewm(span=signal, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']
    return df

def compute_impulse(df):
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
def get_daily(symbol, start, end):
    df = ak.futures_main_sina(symbol=symbol, start_date=start, end_date=end)
    df = df.rename(columns={'日期': 'date', '开盘价': 'open', '最高价': 'high',
                            '最低价': 'low', '收盘价': 'close',
                            '成交量': 'volume', '持仓量': 'hold'})
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    return compute_impulse(df)

def get_hourly(symbol, start, end):
    df = ak.futures_zh_minute_sina(symbol=symbol, period='60')
    df['datetime'] = pd.to_datetime(df['datetime'])
    mask = (df['datetime'] >= pd.Timestamp(start)) & (df['datetime'] <= pd.Timestamp(end))
    df = df[mask].copy()
    df = df.rename(columns={'open': 'open', 'high': 'high',
                            'low': 'low', 'close': 'close', 'volume': 'volume'})
    df = df.sort_values('datetime').reset_index(drop=True)
    return compute_impulse(df)

def get_15min(symbol, start, end):
    df = ak.futures_zh_minute_sina(symbol=symbol, period='15')
    df['datetime'] = pd.to_datetime(df['datetime'])
    mask = (df['datetime'] >= pd.Timestamp(start)) & (df['datetime'] <= pd.Timestamp(end))
    df = df[mask].copy()
    df = df.rename(columns={'open': 'open', 'high': 'high',
                            'low': 'low', 'close': 'close', 'volume': 'volume'})
    df = df.sort_values('datetime').reset_index(drop=True)
    return df

# ── 获取某时刻的日线趋势 ─────────────────────────────────────────────────────
def get_daily_trend_at(daily_df, dt):
    """获取指定时刻的日线动力系统趋势"""
    hist = daily_df[daily_df['date'] < dt.normalize()].copy()
    if len(hist) == 0:
        return 'NEUTRAL'
    last_impulse = hist.iloc[-1]['impulse']
    # 简单判断：最新日线 impulse 为绿→UP, 红→DOWN, 蓝→NEUTRAL
    if last_impulse == 'green':
        return 'UP'
    elif last_impulse == 'red':
        return 'DOWN'
    else:
        return 'NEUTRAL'

# ── L3 精细择时 ──────────────────────────────────────────────────────────────
def get_l3_entry_15min(min15_df, entry_dt, direction):
    """在 entry_dt 附近用15分钟数据精细择时"""
    # 找 entry_dt 所在交易日的15分钟数据
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
        # 用当天首根
        entry_bar = day_data.iloc[0]
    else:
        entry_bar = future_bars.iloc[0]

    entry_price = entry_bar['close']
    reason = f'15min Bar@{entry_bar["datetime"].strftime("%H:%M")} 收盘价{entry_price:.0f}'
    return entry_price, reason

# ── 回测核心 ─────────────────────────────────────────────────────────────────
def run_v5_backtest(symbol='CF0', start='20250101', end='20260620'):
    print(f"\n{'='*60}")
    print(f"【回测】V5（日-小时-15分钟双层动力系统）")
    print(f"品种: {symbol} | 周期: {start}~{end}")
    print(f"{'='*60}\n")

    # ── 数据 ──
    print("获取日线数据（L1）...")
    df_daily = get_daily(symbol, start, end)
    print(f"  {len(df_daily)} 根, {df_daily['date'].min().date()} ~ {df_daily['date'].max().date()}")

    print("获取小时线数据（L2）...")
    hourly = get_hourly(symbol, start, end)
    hourly['impulse_prev'] = hourly['impulse'].shift(1)
    print(f"  {len(hourly)} 根, {hourly['datetime'].min()} ~ {hourly['datetime'].max()}")

    print("获取15分钟数据（L3）...")
    min15 = get_15min(symbol, start, end)
    print(f"  {len(min15)} 根\n")

    # ── 以小时线为驱动，遍历每个小时Bar ──
    trades = []
    equity = [1.0]
    in_pos = None
    ep = xp = 0.0
    entry_dt = None
    reason_in = ''

    for i, row in hourly.iterrows():
        h_dt = row['datetime']
        h_close = row['close']
        h_impulse = row['impulse']
        h_impulse_prev = row['impulse_prev']

        # L1: 获取当前日线趋势
        daily_trend = get_daily_trend_at(df_daily, h_dt)

        # ── 平仓逻辑 ──
        if in_pos:
            pnl = (h_close - ep) / ep if in_pos == 'LONG' else (ep - h_close) / ep
            pnl_pct = pnl * 100

            # 止损
            if pnl_pct <= -STOP_LOSS_PCT * 100:
                trades.append({'entry': entry_dt, 'exit': h_dt,
                               'dir': in_pos, 'ep': ep, 'xp': h_close,
                               'pnl': pnl_pct, 'reason': f'止损{pnl_pct:.2f}%'})
                in_pos = None
                equity.append(equity[-1] * (1 + pnl_pct / 100))
                continue

            # 止盈: 小时线动力系统颜色反转
            if in_pos == 'LONG' and h_impulse in ['red', 'blue']:
                trades.append({'entry': entry_dt, 'exit': h_dt,
                               'dir': in_pos, 'ep': ep, 'xp': h_close,
                               'pnl': pnl_pct, 'reason': f'小时线→{h_impulse}'})
                in_pos = None
                equity.append(equity[-1] * (1 + pnl_pct / 100))
                continue

            if in_pos == 'SHORT' and h_impulse in ['green', 'blue']:
                trades.append({'entry': entry_dt, 'exit': h_dt,
                               'dir': in_pos, 'ep': ep, 'xp': h_close,
                               'pnl': pnl_pct, 'reason': f'小时线→{h_impulse}'})
                in_pos = None
                equity.append(equity[-1] * (1 + pnl_pct / 100))
                continue

        # ── 开仓逻辑 ──
        if not in_pos:
            sig = None
            if daily_trend == 'UP' and h_impulse_prev == 'blue' and h_impulse == 'green':
                sig = 'LONG'
            elif daily_trend == 'DOWN' and h_impulse_prev == 'blue' and h_impulse == 'red':
                sig = 'SHORT'

            if sig:
                # L3: 15分钟精细择时
                entry_price, l3_reason = get_l3_entry_15min(min15, h_dt, sig)

                if entry_price is None:
                    entry_price = h_close
                    l3_reason = '使用小时线收盘价'

                in_pos = sig
                ep = entry_price
                entry_dt = h_dt
                reason_in = f'日线={daily_trend}, 小时线={h_impulse_prev}→{h_impulse}, L3:{l3_reason}'
                emoji = '📈' if sig == 'LONG' else '📉'
                print(f"  {emoji} 开{sig} {str(h_dt)[:16]} @ {ep:.0f}  ({reason_in})")

    # 强制平仓
    if in_pos:
        last_close = hourly.iloc[-1]['close']
        pnl = (last_close - ep) / ep if in_pos == 'LONG' else (ep - last_close) / ep
        trades.append({'entry': entry_dt, 'exit': hourly.iloc[-1]['datetime'],
                       'dir': in_pos, 'ep': ep, 'xp': last_close,
                       'pnl': pnl * 100, 'reason': '期末强制平仓'})

    # ── 统计 ──
    total = len(trades)
    if total == 0:
        print("  ⚠️ 无交易信号")
        return trades, [1.0]

    wins = sum(1 for t in trades if t['pnl'] > 0)
    win_rate = wins / total * 100
    gross_win = sum(t['pnl'] for t in trades if t['pnl'] > 0)
    gross_loss = abs(sum(t['pnl'] for t in trades if t['pnl'] <= 0))
    pf = gross_win / gross_loss if gross_loss else float('inf')
    cum = [1.0]
    for t in trades:
        cum.append(cum[-1] * (1 + t['pnl'] / 100))
    max_eq = max(cum)
    max_dd = min([(x - max_eq) / max_eq * 100 for x in cum]) if max_eq > 1 else 0

    print(f"\n{'='*60}")
    print(f"【回测报告】V5（日-小时-15分钟双层动力系统）")
    print(f"{'='*60}")
    print(f"  交易次数: {total}")
    print(f"  胜率: {win_rate:.1f}%")
    print(f"  盈亏比: {pf:.2f}")
    print(f"  总收益率: {cum[-1]*100-100:.2f}%")
    print(f"  最大回撤: {max_dd:.2f}%")
    print(f"  平均盈亏: {(cum[-1]*100-100)/total:.2f}%/笔")
    print()
    for t in trades:
        emoji = "✅" if t['pnl'] > 0 else "❌"
        print(f"  {emoji} {str(t['entry'])[:16]}→{str(t['exit'])[:16]} {t['dir']:5s} "
              f"{t['ep']:.0f}→{t['xp']:.0f} {t['pnl']:+.2f}% [{t['reason']}]")
    print()
    return trades, cum

if __name__ == '__main__':
    run_v5_backtest()
