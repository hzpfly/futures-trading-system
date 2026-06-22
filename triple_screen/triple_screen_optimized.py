#!/usr/bin/env python3
"""
三重滤网交易系统 - 最优参数版（详细回测报告）
最优参数:
- MACD(8,24,9)
- KD(14,3,3)
- 止损3%
- 止盈KD>70
"""
import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

# ========== 最优参数 ==========
SYMBOL = "CF0"
START_DATE = "20250101"
END_DATE = "20260618"
INITIAL_CAPITAL = 100000
CONTRACT_MULTIPLIER = 5
COMMISSION = 10

# 最优参数
MACD_FAST = 8
MACD_SLOW = 24
MACD_SIGNAL = 9
KD_PERIOD = 14
KD_MA1 = 3
KD_MA2 = 3
STOP_LOSS_PCT = 0.03
PROFIT_K_THRESHOLD = 70
# ==============================


def get_history_data(symbol, start_date, end_date):
    """获取历史数据"""
    df = ak.futures_main_sina(symbol=symbol, start_date=start_date, end_date=end_date)
    df.columns = ['date', 'open', 'high', 'low', 'close', 'volume', 'open_interest', 'settle']
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    return df


def resample_to_weekly(df_daily):
    """日线转周线"""
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
    """计算MACD"""
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd_hist = 2 * (dif - dea)
    return dif, dea, macd_hist


def calc_kd(df, period=KD_PERIOD, ma1=KD_MA1, ma2=KD_MA2):
    """计算KD"""
    low_n = df['low'].rolling(window=period).min()
    high_n = df['high'].rolling(window=period).max()
    rsv = (df['close'] - low_n) / (high_n - low_n) * 100
    k = rsv.ewm(span=ma1, adjust=False).mean()
    d = k.ewm(span=ma2, adjust=False).mean()
    return k, d


def run_detailed_backtest():
    """运行详细回测"""
    print("\n" + "=" * 70)
    print("三重滤网交易系统 - 最优参数详细回测")
    print("=" * 70)
    print(f"\n【参数设置】")
    print(f"  MACD: ({MACD_FAST}, {MACD_SLOW}, {MACD_SIGNAL})")
    print(f"  KD: ({KD_PERIOD}, {KD_MA1}, {KD_MA2})")
    print(f"  止损: {STOP_LOSS_PCT*100:.0f}%")
    print(f"  止盈: KD > {PROFIT_K_THRESHOLD}")
    
    # 获取数据
    print(f"\n【数据获取】")
    df_daily = get_history_data(SYMBOL, START_DATE, END_DATE)
    df_weekly = resample_to_weekly(df_daily)
    print(f"  日线数据: {len(df_daily)} 根 ({df_daily['date'].min().date()} 至 {df_daily['date'].max().date()})")
    print(f"  周线数据: {len(df_weekly)} 根")
    
    # 计算指标
    print(f"\n【指标计算】")
    df_weekly['macd_dif'], df_weekly['macd_dea'], df_weekly['macd_hist'] = calc_macd(df_weekly)
    df_weekly['macd_hist_prev'] = df_weekly['macd_hist'].shift(1)
    df_weekly['macd_slope'] = df_weekly['macd_hist'] - df_weekly['macd_hist_prev']
    
    df_daily['k'], df_daily['d'] = calc_kd(df_daily)
    df_daily['k_prev'] = df_daily['k'].shift(1)
    df_daily['d_prev'] = df_daily['d'].shift(1)
    print(f"  ✅ 指标计算完成")
    
    # 回测
    print(f"\n【回测进行中】")
    capital = INITIAL_CAPITAL
    position = 0
    entry_price = 0
    lot_size = 1
    trades = []
    equity_curve = []
    
    for i in range(30, len(df_daily) - 1):
        current_date = df_daily.iloc[i]['date']
        current_close = df_daily.iloc[i]['close']
        
        # 周线数据
        week_start = current_date - pd.Timedelta(days=current_date.weekday())
        week_data = df_weekly[df_weekly['date'] <= week_start].tail(2)
        
        if len(week_data) < 2:
            continue
        
        # 第一层滤网
        weekly_macd_hist = week_data.iloc[-1]['macd_hist']
        weekly_macd_slope = week_data.iloc[-1]['macd_slope']
        
        if weekly_macd_hist > 0 and weekly_macd_slope > 0:
            weekly_trend = 'UP'
        elif weekly_macd_hist < 0 and weekly_macd_slope < 0:
            weekly_trend = 'DOWN'
        else:
            weekly_trend = 'NEUTRAL'
        
        # 第二层滤网
        daily_k = df_daily.iloc[i]['k']
        daily_d = df_daily.iloc[i]['d']
        daily_k_prev = df_daily.iloc[i-1]['k']
        daily_d_prev = df_daily.iloc[i-1]['d']
        
        # 交易信号
        if position == 0:
            if weekly_trend == 'UP' and daily_k > daily_d and daily_k_prev <= daily_d_prev:
                entry_price = df_daily.iloc[i+1]['open']
                position = 1
                trades.append({
                    'entry_date': df_daily.iloc[i+1]['date'],
                    'action': 'BUY',
                    'entry_price': entry_price,
                    'exit_date': None,
                    'exit_price': None,
                    'pnl': 0,
                    'return_pct': 0
                })
                print(f"  {df_daily.iloc[i+1]['date'].date()}: 🟢 做多 @ {entry_price:.0f}")
            
            elif weekly_trend == 'DOWN' and daily_k < daily_d and daily_k_prev >= daily_d_prev:
                entry_price = df_daily.iloc[i+1]['open']
                position = -1
                trades.append({
                    'entry_date': df_daily.iloc[i+1]['date'],
                    'action': 'SELL',
                    'entry_price': entry_price,
                    'exit_date': None,
                    'exit_price': None,
                    'pnl': 0,
                    'return_pct': 0
                })
                print(f"  {df_daily.iloc[i+1]['date'].date()}: 🔴 做空 @ {entry_price:.0f}")
        
        # 平仓
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
                print(f"  {current_date.date()}: ⚪ 平多(止损) @ {exit_price:.0f}, 盈亏: {pnl:+.0f} ({trades[-1]['return_pct']:+.1f}%)")
                position = 0
            
            # 止盈
            elif daily_k > PROFIT_K_THRESHOLD and daily_k_prev >= daily_d_prev and daily_k < daily_d:
                exit_price = current_close
                pnl = (exit_price - entry_price) * lot_size * CONTRACT_MULTIPLIER - COMMISSION * 2
                capital += pnl
                trades[-1]['exit_date'] = current_date
                trades[-1]['exit_price'] = exit_price
                trades[-1]['pnl'] = pnl
                trades[-1]['return_pct'] = (exit_price - entry_price) / entry_price * 100
                print(f"  {current_date.date()}: ✅ 平多(止盈) @ {exit_price:.0f}, 盈亏: {pnl:+.0f} ({trades[-1]['return_pct']:+.1f}%)")
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
                print(f"  {current_date.date()}: ⚪ 平空(止损) @ {exit_price:.0f}, 盈亏: {pnl:+.0f} ({trades[-1]['return_pct']:+.1f}%)")
                position = 0
            
            # 止盈
            elif daily_k < (100 - PROFIT_K_THRESHOLD) and daily_k_prev <= daily_d_prev and daily_k > daily_d:
                exit_price = current_close
                pnl = (entry_price - exit_price) * lot_size * CONTRACT_MULTIPLIER - COMMISSION * 2
                capital += pnl
                trades[-1]['exit_date'] = current_date
                trades[-1]['exit_price'] = exit_price
                trades[-1]['pnl'] = pnl
                trades[-1]['return_pct'] = (entry_price - exit_price) / entry_price * 100
                print(f"  {current_date.date()}: ✅ 平空(止盈) @ {exit_price:.0f}, 盈亏: {pnl:+.0f} ({trades[-1]['return_pct']:+.1f}%)")
        
        # 权益曲线
        pos_value = 0
        if position == 1:
            pos_value = (current_close - entry_price) * lot_size * CONTRACT_MULTIPLIER
        elif position == -1:
            pos_value = (entry_price - current_close) * lot_size * CONTRACT_MULTIPLIER
        
        equity_curve.append({
            'date': current_date,
            'equity': capital + pos_value
        })
    
    # 平仓未完成的最后一笔交易
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
        print(f"  {df_daily.iloc[-1]['date'].date()}: 📊 持仓中 @ {last_close:.0f}, 浮动盈亏: {pnl:+.0f}")
    
    # 输出报告
    print("\n" + "=" * 70)
    print("【回测报告】")
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
    
    # 最大回撤
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
    print(f"  平均盈利: {total_profit/len(winning_trades):,.0f} 元" if winning_trades else "  平均盈利: N/A")
    print(f"  平均亏损: {total_loss/len(losing_trades):,.0f} 元" if losing_trades else "  平均亏损: N/A")
    
    print(f"\n📉 风险指标:")
    print(f"  最大回撤: {max_drawdown:.2f}%")
    
    print(f"\n【详细交易记录】")
    print("-" * 70)
    for i, t in enumerate(trades, 1):
        action_cn = "做多" if t['action'] == 'BUY' else "做空"
        exit_price = t['exit_price'] if t['exit_price'] else "持仓中"
        pnl = t['pnl'] if t['pnl'] != 0 else 0
        return_pct = t['return_pct'] if t['return_pct'] != 0 else 0
        print(f"{i:2d}. {t['entry_date'].date()} {action_cn} @ {t['entry_price']:.0f} => "
              f"{exit_price}, 盈亏: {pnl:+,.0f} ({return_pct:+.1f}%)")
    
    # 保存报告
    report_path = "/Users/michaelhe/WorkBuddy/Claw/triple_screen_optimized_report.txt"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("三重滤网交易系统 - 最优参数回测报告\n")
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
                    f"{t['exit_price']}, 盈亏: {t['pnl']:+.0f}\n")
    
    print(f"\n✅ 详细报告已保存到: {report_path}")
    
    return trades, equity_curve


if __name__ == '__main__':
    trades, equity = run_detailed_backtest()
