#!/usr/bin/env python3
"""
三重滤网 + 动力系统 组合版 (V2)
======================================

Elder 两本著作的组合:
- 《以交易为生》(1993):     三重滤网 (周线MACD + 日线KD)
- 《Come Into My Trading Room》(2002): 动力系统 (13日EMA + MACD柱)

V2 组合逻辑:
  第一层 (周线): MACD 柱斜率 → 主趋势方向
  第二层 (日线): 动力系统颜色变化 → 入场信号
    - 周线UP   + 动力系统 蓝→绿 → 做多
    - 周线DOWN + 动力系统 蓝→红 → 做空
  第三层 (60分钟): 精细择时 → 避免追高杀低

参数:
- MACD(12,26,9)  用于动力系统的MACD柱
- EMA(13)          用于动力系统的趋势线
- 止损: 3%
- 止盈: 动力系统颜色反转 (绿→蓝/红 平多; 红→蓝/绿 平空)
"""

import akshare as ak
import pandas as pd
import numpy as np
import os
import warnings
warnings.filterwarnings('ignore')

# ========== 参数 ==========
SYMBOL = "CF0"
START_DATE = "20250101"
END_DATE = "20260618"
INITIAL_CAPITAL = 100000
CONTRACT_MULTIPLIER = 5
COMMISSION = 10

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
EMA_IMPULSE = 13       # 动力系统 EMA 周期
STOP_LOSS_PCT = 0.03
# ===========================


def get_history_data(symbol, start_date, end_date):
    df = ak.futures_main_sina(symbol=symbol, start_date=start_date, end_date=end_date)
    df.columns = ['date', 'open', 'high', 'low', 'close', 'volume', 'open_interest', 'settle']
    df['date'] = pd.to_datetime(df['date']).dt.normalize()
    df = df.sort_values('date').reset_index(drop=True)
    return df


def get_60min_data(symbol, start_date, end_date):
    try:
        df = ak.futures_zh_minute_sina(symbol=symbol, period='60')
        df['datetime'] = pd.to_datetime(df['datetime'])
        df['date'] = df['datetime'].dt.normalize()
        start = pd.to_datetime(start_date)
        end = pd.to_datetime(end_date)
        df = df[(df['datetime'] >= start) & (df['datetime'] <= end + pd.Timedelta(days=1))]
        df = df.sort_values('datetime').reset_index(drop=True)
        return df
    except Exception as e:
        print(f"  ⚠️  60分钟数据获取失败: {e}")
        return None


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


def calc_macd(df, fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL):
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd_hist = 2 * (dif - dea)
    return dif, dea, macd_hist


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


def find_60min_entry(df_60min_day, signal_type, daily_close):
    """第三层滤网：60分钟精细择时"""
    if df_60min_day is None or len(df_60min_day) == 0:
        return daily_close, "无60分钟数据,使用日线收盘价"
    
    first_bar = df_60min_day.iloc[0]
    gap_pct = (first_bar['open'] - daily_close) / daily_close

    if signal_type == 'LONG':
        if gap_pct > 0.01:
            min_price = df_60min_day['low'].min()
            if min_price < first_bar['open']:
                return min_price, f"跳空{gap_pct*100:.1f}%,回调至{min_price:.0f}进场"
            else:
                return first_bar['close'], f"跳空{gap_pct*100:.1f}%,无回调,开盘进"
        else:
            return first_bar['close'], f"正常开盘,首根60min Bar进({first_bar['open']:.0f})"

    elif signal_type == 'SHORT':
        if gap_pct < -0.01:
            max_price = df_60min_day['high'].max()
            if max_price > first_bar['open']:
                return max_price, f"跳空{gap_pct*100:.1f}%,反弹至{max_price:.0f}进场"
            else:
                return first_bar['close'], f"跳空{gap_pct*100:.1f}%,无反弹,开盘进"
        else:
            return first_bar['close'], f"正常开盘,首根60min Bar进({first_bar['open']:.0f})"
    
    return daily_close, "默认"


def run_v2_backtest():
    print("\n" + "=" * 70)
    print("三重滤网 + 动力系统 组合版 (V2)")
    print("=" * 70)
    print(f"\n【参数设置】")
    print(f"  第一层(周线): MACD({MACD_FAST},{MACD_SLOW},{MACD_SIGNAL}) 柱斜率")
    print(f"  第二层(日线): 动力系统 EMA({EMA_IMPULSE}) + MACD柱")
    print(f"  第三层(60分钟): 精细择时")
    print(f"  止损: {STOP_LOSS_PCT*100:.0f}%")
    print(f"  止盈: 动力系统颜色反转")

    # 获取数据
    print(f"\n【数据获取】")
    df_daily = get_history_data(SYMBOL, START_DATE, END_DATE)
    df_weekly = resample_to_weekly(df_daily)
    print(f"  日线: {len(df_daily)} 根")
    print(f"  周线: {len(df_weekly)} 根")

    df_60min = get_60min_data(SYMBOL, START_DATE, END_DATE)
    has_60min = df_60min is not None and len(df_60min) > 0
    if has_60min:
        print(f"  60分钟: {len(df_60min)} 根")
        df_60min['k_60'], df_60min['d_60'] = _calc_kd(df_60min)
        daily_60min = {}
        for date, group in df_60min.groupby('date'):
            daily_60min[date] = group.sort_values('datetime').reset_index(drop=True)
    else:
        print(f"  ⚠️  无60分钟数据")
        daily_60min = {}
    print()

    # 计算指标
    print(f"【指标计算】")
    df_weekly['macd_dif'], df_weekly['macd_dea'], df_weekly['macd_hist'] = calc_macd(df_weekly)
    df_weekly['macd_hist_prev'] = df_weekly['macd_hist'].shift(1)
    df_weekly['macd_slope'] = df_weekly['macd_hist'] - df_weekly['macd_hist_prev']

    df_daily = calc_impulse(df_daily)
    print(f"  ✅ 动力系统计算完成")
    print(f"  动力系统分布: 绿={sum(df_daily['impulse']=='green')}, "
          f"红={sum(df_daily['impulse']=='red')}, "
          f"蓝={sum(df_daily['impulse']=='blue')}")
    print()

    # 回测
    print(f"【回测进行中】")
    capital = INITIAL_CAPITAL
    position = 0
    entry_price = 0
    lot_size = 1
    trades = []
    equity_curve = []

    for i in range(30, len(df_daily) - 1):
        current_date = df_daily.iloc[i]['date']
        current_close = df_daily.iloc[i]['close']
        next_date = df_daily.iloc[i + 1]['date']

        # 第一层：周线MACD斜率
        week_start = current_date - pd.Timedelta(days=current_date.weekday())
        week_data = df_weekly[df_weekly['date'] <= week_start].tail(2)
        if len(week_data) < 2:
            continue

        weekly_macd_hist = week_data.iloc[-1]['macd_hist']
        weekly_macd_slope = week_data.iloc[-1]['macd_slope']

        if weekly_macd_hist > 0 and weekly_macd_slope > 0:
            weekly_trend = 'UP'
        elif weekly_macd_hist < 0 and weekly_macd_slope < 0:
            weekly_trend = 'DOWN'
        else:
            weekly_trend = 'NEUTRAL'

        # 第二层：动力系统颜色变化
        impulse_today = df_daily.iloc[i]['impulse']
        impulse_prev = df_daily.iloc[i - 1]['impulse']

        # 开仓信号
        if position == 0:
            # 做多：周线UP + 动力系统 蓝→绿
            if weekly_trend == 'UP' and impulse_prev != 'green' and impulse_today == 'green':
                entry_price, reason = find_60min_entry(
                    daily_60min.get(next_date), 'LONG', current_close)
                position = 1
                trades.append({
                    'entry_date': next_date,
                    'action': 'BUY',
                    'entry_price': entry_price,
                    'exit_date': None,
                    'exit_price': None,
                    'pnl': 0,
                    'return_pct': 0,
                    'exit_reason': '',
                })
                layer_note = "✅第三层" if next_date in daily_60min else "⚠️日线"
                print(f"  {next_date.date()}: 🟢 做多 @ {entry_price:.0f} "
                      f"({layer_note}, {reason})")

            # 做空：周线DOWN + 动力系统 蓝→红
            elif weekly_trend == 'DOWN' and impulse_prev != 'red' and impulse_today == 'red':
                entry_price, reason = find_60min_entry(
                    daily_60min.get(next_date), 'SHORT', current_close)
                position = -1
                trades.append({
                    'entry_date': next_date,
                    'action': 'SELL',
                    'entry_price': entry_price,
                    'exit_date': None,
                    'exit_price': None,
                    'pnl': 0,
                    'return_pct': 0,
                    'exit_reason': '',
                })
                layer_note = "✅第三层" if next_date in daily_60min else "⚠️日线"
                print(f"  {next_date.date()}: 🔴 做空 @ {entry_price:.0f} "
                      f"({layer_note}, {reason})")

        # 平仓逻辑
        elif position == 1:
            # 止损
            if current_close < entry_price * (1 - STOP_LOSS_PCT):
                exit_price = current_close
                pnl = (exit_price - entry_price) * lot_size * CONTRACT_MULTIPLIER - COMMISSION * 2
                capital += pnl
                trades[-1]['exit_date'] = current_date
                trades[-1]['exit_price'] = exit_price
                trades[-1]['pnl'] = pnl
                trades[-1]['return_pct'] = (exit_price - entry_price) / entry_price * 100
                trades[-1]['exit_reason'] = '止损'
                print(f"  {current_date.date()}: ⚪ 平多(止损) @ {exit_price:.0f}, "
                      f"盈亏: {pnl:+.0f}")
                position = 0

            # 止盈：动力系统颜色反转 (绿→蓝 或 绿→红)
            elif impulse_prev == 'green' and impulse_today != 'green':
                exit_price = current_close
                pnl = (exit_price - entry_price) * lot_size * CONTRACT_MULTIPLIER - COMMISSION * 2
                capital += pnl
                trades[-1]['exit_date'] = current_date
                trades[-1]['exit_price'] = exit_price
                trades[-1]['pnl'] = pnl
                trades[-1]['return_pct'] = (exit_price - entry_price) / entry_price * 100
                new_color = '蓝' if impulse_today == 'blue' else '红'
                trades[-1]['exit_reason'] = f'动力系统绿→{new_color}'
                print(f"  {current_date.date()}: ✅ 平多(动力反转) @ {exit_price:.0f}, "
                      f"盈亏: {pnl:+.0f} (绿→{new_color})")
                position = 0

        elif position == -1:
            # 止损
            if current_close > entry_price * (1 + STOP_LOSS_PCT):
                exit_price = current_close
                pnl = (entry_price - exit_price) * lot_size * CONTRACT_MULTIPLIER - COMMISSION * 2
                capital += pnl
                trades[-1]['exit_date'] = current_date
                trades[-1]['exit_price'] = exit_price
                trades[-1]['pnl'] = pnl
                trades[-1]['return_pct'] = (entry_price - exit_price) / entry_price * 100
                trades[-1]['exit_reason'] = '止损'
                print(f"  {current_date.date()}: ⚪ 平空(止损) @ {exit_price:.0f}, "
                      f"盈亏: {pnl:+.0f}")
                position = 0

            # 止盈：动力系统颜色反转 (红→蓝 或 红→绿)
            elif impulse_prev == 'red' and impulse_today != 'red':
                exit_price = current_close
                pnl = (entry_price - exit_price) * lot_size * CONTRACT_MULTIPLIER - COMMISSION * 2
                capital += pnl
                trades[-1]['exit_date'] = current_date
                trades[-1]['exit_price'] = exit_price
                trades[-1]['pnl'] = pnl
                trades[-1]['return_pct'] = (entry_price - exit_price) / entry_price * 100
                new_color = '蓝' if impulse_today == 'blue' else '绿'
                trades[-1]['exit_reason'] = f'动力系统红→{new_color}'
                print(f"  {current_date.date()}: ✅ 平空(动力反转) @ {exit_price:.0f}, "
                      f"盈亏: {pnl:+.0f} (红→{new_color})")
                position = 0

        # 权益曲线
        pos_value = 0
        if position == 1:
            pos_value = (current_close - entry_price) * lot_size * CONTRACT_MULTIPLIER
        elif position == -1:
            pos_value = (entry_price - current_close) * lot_size * CONTRACT_MULTIPLIER
        equity_curve.append({'date': current_date, 'equity': capital + pos_value})

    # 平仓未完成的最后一笔
    if position != 0 and len(trades) > 0 and trades[-1]['exit_date'] is None:
        last_close = df_daily.iloc[-1]['close']
        if position == 1:
            pnl = (last_close - entry_price) * lot_size * CONTRACT_MULTIPLIER - COMMISSION * 2
        else:
            pnl = (entry_price - last_close) * lot_size * CONTRACT_MULTIPLIER - COMMISSION * 2
        trades[-1]['exit_date'] = df_daily.iloc[-1]['date']
        trades[-1]['exit_price'] = last_close
        trades[-1]['pnl'] = pnl
        trades[-1]['return_pct'] = pnl / (entry_price * lot_size * CONTRACT_MULTIPLIER) * 100
        trades[-1]['exit_reason'] = '持仓中'
        print(f"  {df_daily.iloc[-1]['date'].date()}: 📊 持仓中 @ {last_close:.0f}, "
              f"浮动盈亏: {pnl:+.0f}")

    # ========== 输出报告 ==========
    print("\n" + "=" * 70)
    print("【回测报告 - V2 三重滤网+动力系统】")
    print("=" * 70)

    closed_trades = [t for t in trades if t['exit_date'] is not None]
    winning_trades = [t for t in closed_trades if t['pnl'] > 0]
    losing_trades = [t for t in closed_trades if t['pnl'] < 0]
    total_trades = len(closed_trades)
    win_rate = len(winning_trades) / total_trades * 100 if total_trades > 0 else 0
    total_profit = sum([t['pnl'] for t in winning_trades])
    total_loss = abs(sum([t['pnl'] for t in losing_trades]))
    profit_factor = total_profit / total_loss if total_loss > 0 else float('inf')
    total_pnl = sum([t['pnl'] for t in closed_trades])
    total_return = total_pnl / INITIAL_CAPITAL * 100

    if equity_curve:
        equity_df = pd.DataFrame(equity_curve)
        equity_df['cummax'] = equity_df['equity'].cummax()
        equity_df['drawdown'] = (equity_df['equity'] - equity_df['cummax']) / equity_df['cummax'] * 100
        max_drawdown = equity_df['drawdown'].min()
    else:
        max_drawdown = 0

    print(f"\n💰 资金曲线:")
    print(f"  初始资金: {INITIAL_CAPITAL:,.0f} 元")
    print(f"  最终权益: {INITIAL_CAPITAL + total_pnl:,.0f} 元")
    print(f"  总盈亏: {total_pnl:+,.0f} 元")
    print(f"  总收益率: {total_return:+.2f}%")

    print(f"\n📊 交易统计:")
    print(f"  总交易次数: {total_trades}")
    print(f"  盈利次数: {len(winning_trades)}")
    print(f"  亏损次数: {len(losing_trades)}")
    print(f"  胜率: {win_rate:.1f}%")
    print(f"  盈亏比: {profit_factor:.2f}")
    if winning_trades:
        print(f"  平均盈利: {total_profit/len(winning_trades):,.0f} 元")
    if losing_trades:
        print(f"  平均亏损: {total_loss/len(losing_trades):,.0f} 元")

    print(f"\n📉 风险指标:")
    print(f"  最大回撤: {max_drawdown:.2f}%")

    # 平仓原因统计
    exit_reasons = {}
    for t in closed_trades:
        r = t.get('exit_reason', '未知')
        exit_reasons[r] = exit_reasons.get(r, 0) + 1
    print(f"\n🔍 平仓原因分布:")
    for r, cnt in exit_reasons.items():
        print(f"  {r}: {cnt} 笔")

    print(f"\n【详细交易记录】")
    print("-" * 90)
    for i, t in enumerate(trades, 1):
        action_cn = "做多" if t['action'] == 'BUY' else "做空"
        exit_price = t['exit_price'] if t['exit_price'] else "持仓中"
        pnl = t['pnl'] if t['pnl'] != 0 else 0
        return_pct = t['return_pct'] if t['return_pct'] != 0 else 0
        exit_r = t.get('exit_reason', '')
        print(f"{i:2d}. {t['entry_date'].date()} {action_cn} @ {t['entry_price']:.0f} => "
              f"{exit_price}  {exit_r:12s} 盈亏: {pnl:+,.0f} ({return_pct:+.1f}%)")

    # 保存报告
    report_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                              "reports", "triple_screen_impulse_v2_report.txt")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("三重滤网 + 动力系统 组合版 (V2) 回测报告\n")
        f.write("=" * 70 + "\n\n")
        f.write(f"回测时间: {START_DATE} 至 {END_DATE}\n")
        f.write(f"初始资金: {INITIAL_CAPITAL:,.0f} 元\n")
        f.write(f"最终权益: {INITIAL_CAPITAL + total_pnl:,.0f} 元\n")
        f.write(f"总收益率: {total_return:+.2f}%\n")
        f.write(f"胜率: {win_rate:.1f}%\n")
        f.write(f"盈亏比: {profit_factor:.2f}\n")
        f.write(f"最大回撤: {max_drawdown:.2f}%\n\n")
        f.write("交易记录:\n")
        for i, t in enumerate(trades, 1):
            f.write(f"{i}. {t['entry_date'].date()} {t['action']} @ {t['entry_price']:.0f} => "
                    f"{t['exit_price']}, {t.get('exit_reason','')}, "
                    f"盈亏: {t['pnl']:+.0f}\n")

    print(f"\n✅ 详细报告已保存到: {report_path}")
    return trades, equity_curve


def _calc_kd(df, period=14, ma1=3, ma2=3):
    """辅助函数：计算KD"""
    low_n = df['low'].rolling(window=period).min()
    high_n = df['high'].rolling(window=period).max()
    rsv = (df['close'] - low_n) / (high_n - low_n) * 100
    k = rsv.ewm(span=ma1, adjust=False).mean()
    d = k.ewm(span=ma2, adjust=False).mean()
    return k, d


if __name__ == '__main__':
    trades, equity = run_v2_backtest()
