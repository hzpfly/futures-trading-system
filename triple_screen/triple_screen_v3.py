"""
三重滤网策略 V3 - 短周期版（日-小时-15分钟）

周期: L1日线 → L2小时线 → L3 15分钟线
信号更频繁，交易次数更多。

数据来源: AkShare (免费)
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np
import akshare as ak

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ── 技术指标 ──────────────────────────────────────────────────
def compute_macd(df, fast=8, slow=24, signal=9):
    ema_f = df['close'].ewm(span=fast, adjust=False).mean()
    ema_s = df['close'].ewm(span=slow, adjust=False).mean()
    df['macd_dif'] = ema_f - ema_s
    df['macd_dea'] = df['macd_dif'].ewm(span=signal, adjust=False).mean()
    df['macd_hist'] = (df['macd_dif'] - df['macd_dea']) * 2
    return df

def compute_kd(df, n=14, m1=3, m2=3):
    lo_n = df['low'].rolling(n).min()
    hi_n = df['high'].rolling(n).max()
    rsv = (df['close'] - lo_n) / (hi_n - lo_n + 1e-9) * 100
    df['k'] = rsv.ewm(com=m1-1, adjust=False).mean()
    df['d'] = df['k'].ewm(com=m2-1, adjust=False).mean()
    return df

def compute_ema(df, period=13):
    df[f'ema_{period}'] = df['close'].ewm(span=period, adjust=False).mean()
    return df

def compute_impulse(df):
    """动力系统颜色: green/red/blue"""
    df = compute_ema(df, 13)
    df = compute_macd(df)
    df['ema_up'] = df['ema_13'] > df['ema_13'].shift(1)
    df['macd_up'] = df['macd_hist'] > df['macd_hist'].shift(1)
    df['impulse'] = 'blue'
    df.loc[df['ema_up'] & df['macd_up'], 'impulse'] = 'green'
    df.loc[~df['ema_up'] & ~df['macd_up'], 'impulse'] = 'red'
    return df


# ── 数据获取 ──────────────────────────────────────────────────
def get_daily(symbol, start, end):
    df = ak.futures_main_sina(symbol=symbol, start_date=start, end_date=end)
    df = df.rename(columns={'日期':'date','开盘价':'open','最高价':'high',
                            '最低价':'low','收盘价':'close',
                            '成交量':'volume','持仓量':'hold'})
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    df = compute_macd(df)
    df = compute_kd(df)
    return df

def get_hourly(symbol, start, end):
    df = ak.futures_zh_minute_sina(symbol=symbol, period='60')
    df['datetime'] = pd.to_datetime(df['datetime'])
    mask = (df['datetime'] >= pd.Timestamp(start)) & (df['datetime'] <= pd.Timestamp(end) + pd.Timedelta(days=1))
    df = df[mask].sort_values('datetime').reset_index(drop=True)
    df['date'] = df['datetime'].dt.date
    return compute_kd(df)

def get_15min(symbol, start, end):
    df = ak.futures_zh_minute_sina(symbol=symbol, period='15')
    df['datetime'] = pd.to_datetime(df['datetime'])
    mask = (df['datetime'] >= pd.Timestamp(start)) & (df['datetime'] <= pd.Timestamp(end) + pd.Timedelta(days=1))
    df = df[mask].sort_values('datetime').reset_index(drop=True)
    df['date'] = df['datetime'].dt.date
    return compute_kd(df)


# ── 回测 ──────────────────────────────────────────────────────
def run_v3_backtest(symbol="CF0", start_date="20240101", end_date="20260622",
                 stop_pct=3.0, take_pct=6.0, use_impulse=False):
    """
    V3 回测: 日-小时-15分钟

    L1: 日线 MACD柱斜率 → 主趋势
    L2: 小时线 KD金叉/死叉 或 动力系统 → 入场信号
    L3: 15分钟精细择时 → 避免追高杀低
    """
    mode = "动力系统" if use_impulse else "KD"
    print(f"\n{'='*60}")
    print(f"三重滤网 V3 回测（日-小时-15分钟, L2={mode}）")
    print(f"品种: {symbol} | {start_date} ~ {end_date}")
    print(f"{'='*60}")

    # L1: 日线
    df_d = get_daily(symbol, start_date, end_date)
    if len(df_d) < 30:
        print("❌ 日线数据不足"); return {}
    df_d = compute_macd(df_d)
    df_d['macd_slope'] = df_d['macd_hist'].diff()
    df_d['trend'] = 'NEUTRAL'
    df_d.loc[(df_d['macd_hist']>0)&(df_d['macd_slope']>0), 'trend'] = 'UP'
    df_d.loc[(df_d['macd_hist']<0)&(df_d['macd_slope']<0), 'trend'] = 'DOWN'
    n_up = sum(df_d['trend']=='UP')
    n_down = sum(df_d['trend']=='DOWN')
    print(f"✅ L1(日线): {len(df_d)}根 | UP={n_up} DOWN={n_down}")

    # L2: 小时线
    df_h = get_hourly(symbol, start_date, end_date)
    if len(df_h) < 30:
        print("❌ 小时线数据不足"); return {}
    if use_impulse:
        df_h = compute_impulse(df_h)
    print(f"✅ L2(小时线): {len(df_h)}根")

    # L3: 15分钟线
    df_m15 = get_15min(symbol, start_date, end_date)
    if len(df_m15) < 30:
        print("❌ 15分钟线数据不足"); return {}
    print(f"✅ L3(15分钟): {len(df_m15)}根")

    # ── 信号识别 ──
    trades = []
    pos = None
    ep = 0  # entry price
    ed = None  # entry date
    ei = None  # entry bar index (daily)

    for i in range(1, len(df_d)):
        row = df_d.iloc[i]
        prev = df_d.iloc[i-1]
        d_date = pd.Timestamp(row['date']).date()
        trend = row['trend']

        if pos is None:
            # ── 找入场信号 ──
            signal = False
            if not use_impulse:
                # KD 金叉/死叉（用日线KD，小时线数据已有KD）
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

            if signal:
                # L3: 15分钟精细择时
                # 次日开盘后，等15分钟K线回调再进
                next_i = i + 1
                if next_i >= len(df_d):
                    continue
                next_date = pd.Timestamp(df_d.iloc[next_i]['date']).date()
                m15_next = df_m15[df_m15['date'] == next_date]

                # 等待开盘后第一根15分钟Bar
                if len(m15_next) > 0:
                    # 精细择时：如果有跳空，等回调
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
                            else:
                                ep = m15_next.iloc[0]['close']
                    else:  # SHORT
                        if abs(open_price - m15_next.iloc[0]['close']) / open_price < 0.01:
                            ep = m15_next.iloc[0]['close']
                        else:
                            for j in range(1, min(len(m15_next), 20)):
                                if m15_next.iloc[j]['k'] < m15_next.iloc[j]['d']:
                                    ep = m15_next.iloc[j]['close']
                                    break
                            else:
                                ep = m15_next.iloc[0]['close']
                else:
                    ep = df_d.iloc[next_i]['open']

                pos = signal
                ed = df_d.iloc[next_i]['date']
                ei = next_i
                tag = "📈" if signal == 'LONG' else "📉"
                print(f"  {tag} L2信号(日线{i}): {signal} | L3进场 @{ep:.0f} [{next_date}]")

        else:
            # ── 持仓管理 ──
            curr_price = row['close']
            hi = df_d.iloc[ei:i+1]['high'].max()
            lo = df_d.iloc[ei:i+1]['low'].min()

            # 止损
            if pos == 'LONG' and (curr_price - ep)/ep*100 <= -stop_pct:
                xp = curr_price * 0.999
                pnl = (xp - ep)/ep*100
                trades.append({'entry': ed, 'exit': row['date'], 'dir': pos,
                               'ep': ep, 'xp': xp, 'pnl': pnl, 'reason': 'stop_loss'})
                print(f"  🛑 止损 @{xp:.0f} | {pnl:+.2f}%")
                pos = None; continue
            if pos == 'SHORT' and (ep - curr_price)/ep*100 <= -stop_pct:
                xp = curr_price * 1.001
                pnl = (ep - xp)/ep*100
                trades.append({'entry': ed, 'exit': row['date'], 'dir': pos,
                               'ep': ep, 'xp': xp, 'pnl': pnl, 'reason': 'stop_loss'})
                print(f"  🛑 止损 @{xp:.0f} | {pnl:+.2f}%")
                pos = None; continue

            # 止盈
            if not use_impulse:
                # KD 超买/超卖
                if pos == 'LONG' and row['k'] > 70:
                    xp = curr_price * 0.999
                    pnl = (xp - ep)/ep*100
                    trades.append({'entry': ed, 'exit': row['date'], 'dir': pos,
                                   'ep': ep, 'xp': xp, 'pnl': pnl, 'reason': 'take_profit'})
                    print(f"  🎯 止盈 @{xp:.0f} | {pnl:+.2f}%")
                    pos = None; continue
                if pos == 'SHORT' and row['k'] < 30:
                    xp = curr_price * 1.001
                    pnl = (ep - xp)/ep*100
                    trades.append({'entry': ed, 'exit': row['date'], 'dir': pos,
                                   'ep': ep, 'xp': xp, 'pnl': pnl, 'reason': 'take_profit'})
                    print(f"  🎯 止盈 @{xp:.0f} | {pnl:+.2f}%")
                    pos = None; continue
            else:
                # 动力系统颜色反转
                if pos == 'LONG' and prev['impulse'] == 'green' and row['impulse'] != 'green':
                    xp = curr_price * 0.999
                    pnl = (xp - ep)/ep*100
                    trades.append({'entry': ed, 'exit': row['date'], 'dir': pos,
                                   'ep': ep, 'xp': xp, 'pnl': pnl, 'reason': 'impulse_exit'})
                    print(f"  🔄 动力系统出场 @{xp:.0f} | {pnl:+.2f}%")
                    pos = None; continue
                if pos == 'SHORT' and prev['impulse'] == 'red' and row['impulse'] != 'red':
                    xp = curr_price * 1.001
                    pnl = (ep - xp)/ep*100
                    trades.append({'entry': ed, 'exit': row['date'], 'dir': pos,
                                   'ep': ep, 'xp': xp, 'pnl': pnl, 'reason': 'impulse_exit'})
                    print(f"  🔄 动力系统出场 @{xp:.0f} | {pnl:+.2f}%")
                    pos = None; continue

            # 趋势反转
            if pos == 'LONG' and prev['k'] >= prev['d'] and row['k'] < row['d']:
                xp = curr_price * 0.999
                pnl = (xp - ep)/ep*100
                trades.append({'entry': ed, 'exit': row['date'], 'dir': pos,
                               'ep': ep, 'xp': xp, 'pnl': pnl, 'reason': 'trend_reverse'})
                print(f"  🔄 趋势反转 @{xp:.0f} | {pnl:+.2f}%")
                pos = None; continue
            if pos == 'SHORT' and prev['k'] <= prev['d'] and row['k'] > row['d']:
                xp = curr_price * 1.001
                pnl = (ep - xp)/ep*100
                trades.append({'entry': ed, 'exit': row['date'], 'dir': pos,
                               'ep': ep, 'xp': xp, 'pnl': pnl, 'reason': 'trend_reverse'})
                print(f"  🔄 趋势反转 @{xp:.0f} | {pnl:+.2f}%")
                pos = None; continue

    # 强制平仓
    if pos:
        last = df_d.iloc[-1]
        xp = last['close'] * (0.999 if pos == 'LONG' else 1.001)
        pnl = (xp - ep)/ep*100 if pos == 'LONG' else (ep - xp)/ep*100
        trades.append({'entry': ed, 'exit': last['date'], 'dir': pos,
                       'ep': ep, 'xp': xp, 'pnl': pnl, 'reason': 'force_close'})
        print(f"  ⚠️ 强制平仓 @{xp:.0f} | {pnl:+.2f}%")

    # ── 统计 ──
    total = len(trades)
    if total == 0:
        print("\n⚠️ 无交易"); return {}
    wins = sum(1 for t in trades if t['pnl'] > 0)
    win_rate = wins / total * 100
    gp = sum(t['pnl'] for t in trades if t['pnl'] > 0)
    gl = abs(sum(t['pnl'] for t in trades if t['pnl'] < 0))
    pf = gp / gl if gl > 0 else float('inf')
    cum = np.cumsum([t['pnl'] for t in trades])
    max_dd = 0; peak = 0
    for v in cum:
        if v > peak: peak = v
        max_dd = max(max_dd, peak - v)

    version = f"V3{'I' if use_impulse else ''}"
    print(f"\n{'='*60}")
    print(f"【回测报告】{version}（日-小时-15分钟）")
    print(f"{'='*60}")
    print(f"  交易次数: {total}")
    print(f"  胜率: {win_rate:.1f}%")
    print(f"  盈亏比: {pf:.2f}")
    print(f"  总收益率: {cum[-1]:.2f}%")
    print(f"  最大回撤: {max_dd:.2f}%")
    print(f"  平均盈亏: {cum[-1]/total:.2f}%/笔")
    print()
    for t in trades:
        emoji = "✅" if t['pnl'] > 0 else "❌"
        print(f"  {emoji} {str(t['entry'])[:10]}→{str(t['exit'])[:10]} {t['dir']:5s} "
              f"{t['ep']:.0f}→{t['xp']:.0f} {t['pnl']:+.2f}% [{t['reason']}]")
    print()

    # 返回格式与其他版本一致: (trades_list, cumulative_list)
    return trades, cum.tolist()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="CF0")
    p.add_argument("--start", default="20240101")
    p.add_argument("--end", default="20260622")
    p.add_argument("--impulse", action="store_true", help="使用动力系统(L2)")
    args = p.parse_args()
    run_v3_backtest(symbol=args.symbol, start_date=args.start, end_date=args.end,
                 use_impulse=args.impulse)
