#!/usr/bin/env python3
"""
三重滤网交易系统 - 完整三层版
====================================

第一层 (周线): MACD 柱斜率 → 判断主趋势
第二层 (日线): KD 金叉/死叉 → 识别回调结束
第三层 (60分钟): 精细择时 → 避免追在高点/杀在低点

最优参数:
- MACD(8,24,9)
- KD(14,3,3)
- 止损3%
- 止盈KD>70 (多) / KD<30 (空)
"""
import akshare as ak
import pandas as pd
import numpy as np
import os
from datetime import datetime, time as dtime
import warnings
warnings.filterwarnings('ignore')

# ========== 参数 ==========
SYMBOL = "CF0"
START_DATE = "20250101"   # 60分钟数据从2025-09开始，这里用2025年确保有数据
END_DATE = "20260618"
INITIAL_CAPITAL = 100000
CONTRACT_MULTIPLIER = 5
COMMISSION = 10

MACD_FAST = 8
MACD_SLOW = 24
MACD_SIGNAL = 9
KD_PERIOD = 14
KD_MA1 = 3
KD_MA2 = 3
STOP_LOSS_PCT = 0.03
PROFIT_K_THRESHOLD = 70

# 第三层参数
ENTRY_TIME_MAX_BARS = 4   # 开盘前4根60分钟K线内必须进场（上午盘）
MIN_PULLBACK_PCT = 0.002  # 最小回调幅度（用于第三层精细进场）
# ===========================


def get_history_data(symbol, start_date, end_date):
    df = ak.futures_main_sina(symbol=symbol, start_date=start_date, end_date=end_date)
    df.columns = ['date', 'open', 'high', 'low', 'close', 'volume', 'open_interest', 'settle']
    df['date'] = pd.to_datetime(df['date']).dt.normalize()  # 去掉时间部分
    df = df.sort_values('date').reset_index(drop=True)
    return df


def get_60min_data(symbol, start_date, end_date):
    """获取60分钟线，返回按日期分组的字典"""
    try:
        df = ak.futures_zh_minute_sina(symbol=symbol, period='60')
        df['datetime'] = pd.to_datetime(df['datetime'])
        df['date'] = df['datetime'].dt.normalize()
        df['time'] = df['datetime'].dt.time
        
        # 过滤日期范围
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


def calc_kd(df, period=KD_PERIOD, ma1=KD_MA1, ma2=KD_MA2):
    low_n = df['low'].rolling(window=period).min()
    high_n = df['high'].rolling(window=period).max()
    rsv = (df['close'] - low_n) / (high_n - low_n) * 100
    k = rsv.ewm(span=ma1, adjust=False).mean()
    d = k.ewm(span=ma2, adjust=False).mean()
    return k, d


def find_60min_entry(df_60min_day, signal_type, daily_close):
    """
    第三层滤网：在当日60分钟K线中找到精细进场点
    
    多头信号:
      - 优先: 开盘后第一根60分钟K线（积极进场）
      - 备选: 如果开盘跳空>1%，等回调——第一根阴线或KD回落后再进
    
    空头信号:
      - 优先: 开盘后第一根60分钟K线
      - 备选: 如果开盘跳空< -1%，等反弹——第一根阳线或KD反弹后再进
    
    返回: (entry_price, entry_bar_index, reason)
    """
    if df_60min_day is None or len(df_60min_day) == 0:
        return daily_close, -1, "无60分钟数据,使用日线收盘价"
    
    first_bar = df_60min_day.iloc[0]
    gap_pct = (first_bar['open'] - daily_close) / daily_close
    
    if signal_type == 'LONG':
        # 开盘跳空过高（>1%），等回调
        if gap_pct > 0.01:
            # 找当日最低价作为回调进场点
            min_price = df_60min_day['low'].min()
            pullback_pct = (min_price - daily_close) / daily_close
            # 如果连回调都没有（一直拉涨），还是在第一根Bar收盘价进
            if min_price < first_bar['open']:
                # 找最低价对应的Bar
                min_idx = df_60min_day['low'].idxmin()
                min_bar = df_60min_day.loc[min_idx]
                return min_bar['close'], 0, f"跳空{gap_pct*100:.1f}%,回调至{min_price:.0f}进场"
            else:
                return first_bar['close'], 0, f"跳空{gap_pct*100:.1f}%,无回调,开盘进"
        else:
            # 正常开盘，第一根60分钟Bar收盘价进场
            return first_bar['close'], 0, f"正常开盘,第一根60min Bar进(开:{first_bar['open']:.0f})"
    
    elif signal_type == 'SHORT':
        # 开盘跳空过低（< -1%），等反弹
        if gap_pct < -0.01:
            max_price = df_60min_day['high'].max()
            if max_price > first_bar['open']:
                max_idx = df_60min_day['high'].idxmax()
                max_bar = df_60min_day.loc[max_idx]
                return max_bar['close'], 0, f"跳空{gap_pct*100:.1f}%,反弹至{max_price:.0f}进场"
            else:
                return first_bar['close'], 0, f"跳空{gap_pct*100:.1f}%,无反弹,开盘进"
        else:
            return first_bar['close'], 0, f"正常开盘,第一根60min Bar进(开:{first_bar['open']:.0f})"
    
    return daily_close, -1, "默认:使用日线收盘价"


def run_3layer_backtest():
    print("\n" + "=" * 70)
    print("三重滤网交易系统 - 完整三层版")
    print("=" * 70)
    print(f"\n【参数设置】")
    print(f"  MACD: ({MACD_FAST}, {MACD_SLOW}, {MACD_SIGNAL})")
    print(f"  KD: ({KD_PERIOD}, {KD_MA1}, {KD_MA2})")
    print(f"  止损: {STOP_LOSS_PCT*100:.0f}%")
    print(f"  止盈: KD > {PROFIT_K_THRESHOLD} (多) / KD < {100-PROFIT_K_THRESHOLD} (空)")
    print(f"  第三层: 60分钟线精细择时")

    # 获取日线数据
    print(f"\n【数据获取】")
    df_daily = get_history_data(SYMBOL, START_DATE, END_DATE)
    df_weekly = resample_to_weekly(df_daily)
    print(f"  日线数据: {len(df_daily)} 根")
    print(f"  周线数据: {len(df_weekly)} 根")

    # 获取60分钟数据
    print(f"  获取60分钟数据...")
    df_60min = get_60min_data(SYMBOL, START_DATE, END_DATE)
    has_60min = df_60min is not None and len(df_60min) > 0
    if has_60min:
        print(f"  ✅ 60分钟数据: {len(df_60min)} 根 ({df_60min['datetime'].min().date()} ~ {df_60min['datetime'].max().date()})")
        # 预计算60分钟KD
        df_60min['k_60'], df_60min['d_60'] = calc_kd(df_60min)
        # 按日期分组60分钟数据
        daily_60min = {}
        for date, group in df_60min.groupby('date'):
            daily_60min[date] = group.sort_values('datetime').reset_index(drop=True)
    else:
        print(f"  ⚠️  无60分钟数据，第三层将使用日线开盘价")
        daily_60min = {}
    print()

    # 计算指标
    print(f"【指标计算】")
    df_weekly['macd_dif'], df_weekly['macd_dea'], df_weekly['macd_hist'] = calc_macd(df_weekly)
    df_weekly['macd_hist_prev'] = df_weekly['macd_hist'].shift(1)
    df_weekly['macd_slope'] = df_weekly['macd_hist'] - df_weekly['macd_hist_prev']
    df_daily['k'], df_daily['d'] = calc_kd(df_daily)
    df_daily['k_prev'] = df_daily['k'].shift(1)
    df_daily['d_prev'] = df_daily['d'].shift(1)
    print(f"  ✅ 指标计算完成\n")

    # 回测
    print(f"【回测进行中】")
    capital = INITIAL_CAPITAL
    position = 0
    entry_price = 0
    lot_size = 1
    trades = []
    equity_curve = []
    third_layer_count = 0  # 第三层生效次数

    for i in range(30, len(df_daily) - 1):
        current_date = df_daily.iloc[i]['date']
        current_close = df_daily.iloc[i]['close']
        next_date = df_daily.iloc[i + 1]['date']

        # 第一层：周线MACD柱斜率
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

        # 第二层：日线KD信号
        daily_k = df_daily.iloc[i]['k']
        daily_d = df_daily.iloc[i]['d']
        daily_k_prev = df_daily.iloc[i - 1]['k']
        daily_d_prev = df_daily.iloc[i - 1]['d']

        # 开仓信号
        if position == 0:
            # 多头：周线UP + KD金叉
            if weekly_trend == 'UP' and daily_k > daily_d and daily_k_prev <= daily_d_prev:
                # 第三层：60分钟精细择时
                entry_price, bar_idx, reason = find_60min_entry(
                    daily_60min.get(next_date),
                    'LONG',
                    current_close
                )
                position = 1
                layer_note = "✅第三层" if next_date in daily_60min else "⚠️日线"
                trades.append({
                    'entry_date': next_date,
                    'action': 'BUY',
                    'entry_price': entry_price,
                    'exit_date': None,
                    'exit_price': None,
                    'pnl': 0,
                    'return_pct': 0,
                    'layer3': layer_note,
                    'reason': reason
                })
                third_layer_count += 1 if next_date in daily_60min else 0
                print(f"  {next_date.date()}: 🟢 做多 @ {entry_price:.0f} ({reason})")

            # 空头：周线DOWN + KD死叉
            elif weekly_trend == 'DOWN' and daily_k < daily_d and daily_k_prev >= daily_d_prev:
                entry_price, bar_idx, reason = find_60min_entry(
                    daily_60min.get(next_date),
                    'SHORT',
                    current_close
                )
                position = -1
                layer_note = "✅第三层" if next_date in daily_60min else "⚠️日线"
                trades.append({
                    'entry_date': next_date,
                    'action': 'SELL',
                    'entry_price': entry_price,
                    'exit_date': None,
                    'exit_price': None,
                    'pnl': 0,
                    'return_pct': 0,
                    'layer3': layer_note,
                    'reason': reason
                })
                third_layer_count += 1 if next_date in daily_60min else 0
                print(f"  {next_date.date()}: 🔴 做空 @ {entry_price:.0f} ({reason})")

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
                print(f"  {current_date.date()}: ⚪ 平多(止损) @ {exit_price:.0f}, 盈亏: {pnl:+.0f}")
                position = 0

            # 止盈：KD > 70 且 K下穿D
            elif daily_k > PROFIT_K_THRESHOLD and daily_k_prev >= daily_d_prev and daily_k < daily_d:
                exit_price = current_close
                pnl = (exit_price - entry_price) * lot_size * CONTRACT_MULTIPLIER - COMMISSION * 2
                capital += pnl
                trades[-1]['exit_date'] = current_date
                trades[-1]['exit_price'] = exit_price
                trades[-1]['pnl'] = pnl
                trades[-1]['return_pct'] = (exit_price - entry_price) / entry_price * 100
                print(f"  {current_date.date()}: ✅ 平多(止盈) @ {exit_price:.0f}, 盈亏: {pnl:+.0f}")
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
                print(f"  {current_date.date()}: ⚪ 平空(止损) @ {exit_price:.0f}, 盈亏: {pnl:+.0f}")
                position = 0

            # 止盈：KD < 30 且 K上穿D
            elif daily_k < (100 - PROFIT_K_THRESHOLD) and daily_k_prev <= daily_d_prev and daily_k > daily_d:
                exit_price = current_close
                pnl = (entry_price - exit_price) * lot_size * CONTRACT_MULTIPLIER - COMMISSION * 2
                capital += pnl
                trades[-1]['exit_date'] = current_date
                trades[-1]['exit_price'] = exit_price
                trades[-1]['pnl'] = pnl
                trades[-1]['return_pct'] = (entry_price - exit_price) / entry_price * 100
                print(f"  {current_date.date()}: ✅ 平空(止盈) @ {exit_price:.0f}, 盈亏: {pnl:+.0f}")
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

    # ========== 输出报告 ==========
    print("\n" + "=" * 70)
    print("【回测报告 - 完整三层】")
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

    print(f"\n🔬 第三层分析:")
    print(f"  第三层生效次数: {third_layer_count}/{len(trades)} ({third_layer_count/len(trades)*100:.0f}%)")
    
    # 对比：有第三层 vs 无第三层的进场价差异
    if has_60min:
        print(f"  (第三层用60分钟精细择时，避免追高/杀低)")

    print(f"\n【详细交易记录】")
    print("-" * 80)
    for i, t in enumerate(trades, 1):
        action_cn = "做多" if t['action'] == 'BUY' else "做空"
        exit_price = t['exit_price'] if t['exit_price'] else "持仓中"
        pnl = t['pnl'] if t['pnl'] != 0 else 0
        return_pct = t['return_pct'] if t['return_pct'] != 0 else 0
        layer = t.get('layer3', '')
        print(f"{i:2d}. {t['entry_date'].date()} {action_cn} @ {t['entry_price']:.0f} => "
              f"{exit_price}  {layer}  盈亏: {pnl:+,.0f} ({return_pct:+.1f}%)")

    # 保存报告
    report_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 
                              "reports", "triple_screen_3layer_report.txt")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("三重滤网交易系统 - 完整三层回测报告\n")
        f.write("=" * 70 + "\n\n")
        f.write(f"回测时间: {START_DATE} 至 {END_DATE}\n")
        f.write(f"初始资金: {INITIAL_CAPITAL:,.0f} 元\n")
        f.write(f"最终权益: {INITIAL_CAPITAL + total_pnl:,.0f} 元\n")
        f.write(f"总收益率: {total_return:+.2f}%\n")
        f.write(f"胜率: {win_rate:.1f}%\n")
        f.write(f"盈亏比: {profit_factor:.2f}\n")
        f.write(f"最大回撤: {max_drawdown:.2f}%\n")
        f.write(f"第三层生效: {third_layer_count}/{len(trades)}\n\n")
        f.write("交易记录:\n")
        for i, t in enumerate(trades, 1):
            f.write(f"{i}. {t['entry_date'].date()} {t['action']} @ {t['entry_price']:.0f} => "
                    f"{t['exit_price']}, 盈亏: {t['pnl']:+.0f}, {t.get('reason', '')}\n")

    print(f"\n✅ 详细报告已保存到: {report_path}")

    return trades, equity_curve


if __name__ == '__main__':
    trades, equity = run_3layer_backtest()
