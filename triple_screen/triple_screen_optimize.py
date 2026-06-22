#!/usr/bin/env python3
"""
三重滤网交易系统 - 参数优化版
目标：提高交易频率，保持盈利性
"""
import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

# ========== 基础参数 ==========
SYMBOL = "CF0"
START_DATE = "20250101"
END_DATE = "20260618"
INITIAL_CAPITAL = 100000
CONTRACT_MULTIPLIER = 5
COMMISSION = 10
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


def calc_macd(df, fast=12, slow=26, signal=9):
    """计算MACD"""
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd_hist = 2 * (dif - dea)
    return dif, dea, macd_hist


def calc_kd(df, period=9, ma1=3, ma2=3):
    """计算KD"""
    low_n = df['low'].rolling(window=period).min()
    high_n = df['high'].rolling(window=period).max()
    rsv = (df['close'] - low_n) / (high_n - low_n) * 100
    k = rsv.ewm(span=ma1, adjust=False).mean()
    d = k.ewm(span=ma2, adjust=False).mean()
    return k, d


def backtest_strategy(df_daily, df_weekly, params):
    """
    回测策略
    params: 参数字典
    """
    # 解包参数
    macd_fast = params['macd_fast']
    macd_slow = params['macd_slow']
    macd_signal = params['macd_signal']
    kd_period = params['kd_period']
    kd_ma1 = params['kd_ma1']
    kd_ma2 = params['kd_ma2']
    oversold = params['oversold']
    overbought = params['overbought']
    stop_loss_pct = params['stop_loss_pct']
    profit_k_threshold = params['profit_k_threshold']
    
    # 计算指标
    df_weekly['macd_dif'], df_weekly['macd_dea'], df_weekly['macd_hist'] = calc_macd(df_weekly, macd_fast, macd_slow, macd_signal)
    df_weekly['macd_hist_prev'] = df_weekly['macd_hist'].shift(1)
    df_weekly['macd_slope'] = df_weekly['macd_hist'] - df_weekly['macd_hist_prev']
    
    df_daily['k'], df_daily['d'] = calc_kd(df_daily, kd_period, kd_ma1, kd_ma2)
    df_daily['k_prev'] = df_daily['k'].shift(1)
    df_daily['d_prev'] = df_daily['d'].shift(1)
    
    # 回测
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
                    'date': df_daily.iloc[i+1]['date'],
                    'action': 'BUY',
                    'price': entry_price,
                    'pnl': 0
                })
            
            elif weekly_trend == 'DOWN' and daily_k < daily_d and daily_k_prev >= daily_d_prev:
                entry_price = df_daily.iloc[i+1]['open']
                position = -1
                trades.append({
                    'date': df_daily.iloc[i+1]['date'],
                    'action': 'SELL',
                    'price': entry_price,
                    'pnl': 0
                })
        
        # 平仓
        elif position == 1:
            # 止损
            if current_close < entry_price * (1 - stop_loss_pct):
                exit_price = current_close
                pnl = (exit_price - entry_price) * lot_size * CONTRACT_MULTIPLIER - COMMISSION * 2
                capital += pnl
                trades[-1]['exit_date'] = current_date
                trades[-1]['exit_price'] = exit_price
                trades[-1]['pnl'] = pnl
                position = 0
            # 止盈：KD超买死叉
            elif daily_k > profit_k_threshold and daily_k_prev >= daily_d_prev and daily_k < daily_d:
                exit_price = current_close
                pnl = (exit_price - entry_price) * lot_size * CONTRACT_MULTIPLIER - COMMISSION * 2
                capital += pnl
                trades[-1]['exit_date'] = current_date
                trades[-1]['exit_price'] = exit_price
                trades[-1]['pnl'] = pnl
                position = 0
        
        elif position == -1:
            # 止损
            if current_close > entry_price * (1 + stop_loss_pct):
                exit_price = current_close
                pnl = (entry_price - exit_price) * lot_size * CONTRACT_MULTIPLIER - COMMISSION * 2
                capital += pnl
                trades[-1]['exit_date'] = current_date
                trades[-1]['exit_price'] = exit_price
                trades[-1]['pnl'] = pnl
                position = 0
            # 止盈：KD超卖金叉
            elif daily_k < (100 - profit_k_threshold) and daily_k_prev <= daily_d_prev and daily_k > daily_d:
                exit_price = current_close
                pnl = (entry_price - exit_price) * lot_size * CONTRACT_MULTIPLIER - COMMISSION * 2
                capital += pnl
                trades[-1]['exit_date'] = current_date
                trades[-1]['exit_price'] = exit_price
                trades[-1]['pnl'] = pnl
                position = 0
        
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
    
    # 计算统计
    if len(trades) == 0:
        return {
            'total_return': 0,
            'win_rate': 0,
            'profit_factor': 0,
            'max_drawdown': 0,
            'total_trades': 0,
            'params': params
        }
    
    # 平仓未完成的交易
    if 'exit_price' not in trades[-1]:
        last_trade = trades[-1]
        last_close = df_daily.iloc[-1]['close']
        if last_trade['action'] == 'BUY':
            pnl = (last_close - last_trade['price']) * lot_size * CONTRACT_MULTIPLIER - COMMISSION * 2
        else:
            pnl = (last_trade['price'] - last_close) * lot_size * CONTRACT_MULTIPLIER - COMMISSION * 2
        trades[-1]['pnl'] = pnl
    
    closed_trades = [t for t in trades if 'pnl' in t]
    winning = [t for t in closed_trades if t['pnl'] > 0]
    losing = [t for t in closed_trades if t['pnl'] < 0]
    
    total_pnl = sum([t['pnl'] for t in closed_trades])
    total_return = total_pnl / INITIAL_CAPITAL * 100
    win_rate = len(winning) / len(closed_trades) * 100 if closed_trades else 0
    profit_factor = abs(sum([t['pnl'] for t in winning]) / sum([t['pnl'] for t in losing])) if losing else float('inf')
    
    # 最大回撤
    if equity_curve:
        equity_df = pd.DataFrame(equity_curve)
        equity_df['cummax'] = equity_df['equity'].cummax()
        equity_df['drawdown'] = (equity_df['equity'] - equity_df['cummax']) / equity_df['cummax'] * 100
        max_drawdown = equity_df['drawdown'].min()
    else:
        max_drawdown = 0
    
    return {
        'total_return': total_return,
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'max_drawdown': max_drawdown,
        'total_trades': len(closed_trades),
        'params': params
    }


def parameter_optimization():
    """参数优化：网格搜索"""
    print("=" * 60)
    print("三重滤网交易系统 - 参数优化")
    print("=" * 60)
    
    # 获取数据
    print("\n正在获取历史数据...")
    df_daily = get_history_data(SYMBOL, START_DATE, END_DATE)
    df_weekly = resample_to_weekly(df_daily)
    print(f"✅ 数据获取完成！日线: {len(df_daily)} 根, 周线: {len(df_weekly)} 根")
    
    # 参数网格
    param_grid = {
        'macd_fast': [8, 10, 12],
        'macd_slow': [24, 26, 30],
        'macd_signal': [9],
        'kd_period': [9, 14, 20],
        'kd_ma1': [3],
        'kd_ma2': [3],
        'oversold': [20],
        'overbought': [80],
        'stop_loss_pct': [0.02, 0.03, 0.05],
        'profit_k_threshold': [70, 80]
    }
    
    # 生成所有参数组合
    import itertools
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    param_combinations = list(itertools.product(*values))
    
    print(f"\n开始参数优化...")
    print(f"参数组合数: {len(param_combinations)}")
    print("=" * 60)
    
    results = []
    for i, combo in enumerate(param_combinations):
        params = dict(zip(keys, combo))
        result = backtest_strategy(df_daily.copy(), df_weekly.copy(), params)
        results.append(result)
        
        # 进度显示
        if (i + 1) % 10 == 0:
            print(f"进度: {i+1}/{len(param_combinations)}")
    
    # 排序：按总收益率
    results.sort(key=lambda x: x['total_return'], reverse=True)
    
    # 输出最优结果
    print("\n" + "=" * 60)
    print("🏆 最优参数组合 (Top 10)")
    print("=" * 60)
    
    for i, result in enumerate(results[:10]):
        print(f"\n#{i+1}")
        print(f"  总收益率: {result['total_return']:.2f}%")
        print(f"  胜率: {result['win_rate']:.1f}%")
        print(f"  盈亏比: {result['profit_factor']:.2f}")
        print(f"  最大回撤: {result['max_drawdown']:.2f}%")
        print(f"  交易次数: {result['total_trades']}")
        print(f"  参数: MACD({result['params']['macd_fast']},{result['params']['macd_slow']},{result['params']['macd_signal']}), "
              f"KD({result['params']['kd_period']},{result['params']['kd_ma1']},{result['params']['kd_ma2']}), "
              f"止损{result['params']['stop_loss_pct']*100:.0f}%, "
              f"止盈KD>{result['params']['profit_k_threshold']}")
    
    # 保存最优参数
    best_params = results[0]['params']
    print("\n" + "=" * 60)
    print("✅ 最优参数:")
    print("=" * 60)
    for key, value in best_params.items():
        print(f"  {key}: {value}")
    
    # 用最优参数重新回测并输出详细报告
    print("\n正在用最优参数重新回测...")
    # 这里可以调用详细回测函数
    
    return best_params


if __name__ == '__main__':
    best_params = parameter_optimization()
