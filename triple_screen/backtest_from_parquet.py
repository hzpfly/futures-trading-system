#!/usr/bin/env python3
"""
从 data/kline/ 加载已下载数据，运行三重滤网回测
对比：AkShare 直接下载 vs Parquet 文件加载
"""
import pandas as pd
import numpy as np
import os
import time
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

# ========== 参数设置 ==========
START_DATE = "2025-01-01"
END_DATE = "2026-06-18"
INITIAL_CAPITAL = 100000
CONTRACT_MULTIPLIER = 5
COMMISSION = 10

# 指标参数
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
EXPMA_PERIOD = 13
KD_PERIOD = 9
KD_MA1 = 3
KD_MA2 = 3
OVERSOLD = 20
OVERBOUGHT = 80

# 4个品种配置
PRODUCTS = {
    "棉花": {"file": "daily_棉花_CF0_akshare.parquet", "multiplier": 5},
    "铁矿石": {"file": "daily_铁矿石_I0_akshare.parquet", "multiplier": 100},
    "玉米": {"file": "daily_玉米_C0_akshare.parquet", "multiplier": 10},
    "豆粕": {"file": "daily_豆粕_M0_akshare.parquet", "multiplier": 10},
}

KLINE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "kline")
# ==============================


def load_from_parquet(filepath, start_date, end_date):
    """从 parquet 文件加载并裁剪日期"""
    df = pd.read_parquet(filepath)
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    df = df[(df['date'] >= start) & (df['date'] <= end)].reset_index(drop=True)
    return df


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


def calc_macd(df):
    ema_fast = df['close'].ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = df['close'].ewm(span=MACD_SLOW, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=MACD_SIGNAL, adjust=False).mean()
    macd_hist = 2 * (dif - dea)
    return dif, dea, macd_hist


def calc_kd(df):
    low_n = df['low'].rolling(window=KD_PERIOD).min()
    high_n = df['high'].rolling(window=KD_PERIOD).max()
    rsv = (df['close'] - low_n) / (high_n - low_n) * 100
    k = rsv.ewm(span=KD_MA1, adjust=False).mean()
    d = k.ewm(span=KD_MA2, adjust=False).mean()
    return k, d


def run_backtest(name, df_daily, contract_multiplier):
    """运行三重滤网回测，返回统计"""
    if len(df_daily) < 50:
        return None

    df_weekly = resample_to_weekly(df_daily)

    # 周线指标
    df_weekly['macd_dif'], df_weekly['macd_dea'], df_weekly['macd_hist'] = calc_macd(df_weekly)
    df_weekly['expma13'] = df_weekly['close'].ewm(span=EXPMA_PERIOD, adjust=False).mean()
    df_weekly['macd_slope'] = df_weekly['macd_hist'] - df_weekly['macd_hist'].shift(1)

    # 日线指标
    df_daily['k'], df_daily['d'] = calc_kd(df_daily)
    df_daily['k_prev'] = df_daily['k'].shift(1)
    df_daily['d_prev'] = df_daily['d'].shift(1)

    capital = INITIAL_CAPITAL
    position = 0
    entry_price = 0
    lot_size = 1
    trades = []
    equity_curve = []

    for i in range(30, len(df_daily) - 1):
        current_date = df_daily.iloc[i]['date']
        current_close = df_daily.iloc[i]['close']

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

        daily_k = df_daily.iloc[i]['k']
        daily_d = df_daily.iloc[i]['d']
        daily_k_prev = df_daily.iloc[i-1]['k']
        daily_d_prev = df_daily.iloc[i-1]['d']

        # 入场
        if position == 0:
            if weekly_trend == 'UP' and daily_k > daily_d and daily_k_prev <= daily_d_prev:
                entry_price = df_daily.iloc[i+1]['open']
                position = 1
                trades.append({'date': df_daily.iloc[i+1]['date'], 'action': 'BUY',
                               'price': entry_price, 'pnl': 0})
            elif weekly_trend == 'DOWN' and daily_k < daily_d and daily_k_prev >= daily_d_prev:
                entry_price = df_daily.iloc[i+1]['open']
                position = -1
                trades.append({'date': df_daily.iloc[i+1]['date'], 'action': 'SELL',
                               'price': entry_price, 'pnl': 0})

        # 平多
        elif position == 1:
            stop_loss = entry_price * 0.98
            exit_reason = None
            exit_price_val = current_close

            if current_close < stop_loss:
                exit_reason = "止损"
            elif daily_k > OVERBOUGHT and daily_k_prev >= daily_d_prev and daily_k < daily_d:
                exit_reason = "止盈"
                exit_price_val = current_close
            elif (current_date - trades[-1]['date']).days > 10:
                exit_reason = "时间"
                exit_price_val = current_close

            if exit_reason:
                pnl = (exit_price_val - entry_price) * lot_size * contract_multiplier - COMMISSION * 2 * lot_size
                capital += pnl
                trades[-1]['exit_date'] = current_date
                trades[-1]['exit_price'] = exit_price_val
                trades[-1]['pnl'] = pnl
                position = 0

        # 平空 (与原版一致：仅止损+止盈，无时间止损)
        elif position == -1:
            stop_loss = entry_price * 1.02
            exit_reason = None
            exit_price_val = current_close

            if current_close > stop_loss:
                exit_reason = "止损"
            elif daily_k < OVERSOLD and daily_k_prev <= daily_d_prev and daily_k > daily_d:
                exit_reason = "止盈"
                exit_price_val = current_close

            if exit_reason:
                pnl = (entry_price - exit_price_val) * lot_size * contract_multiplier - COMMISSION * 2 * lot_size
                capital += pnl
                trades[-1]['exit_date'] = current_date
                trades[-1]['exit_price'] = exit_price_val
                trades[-1]['pnl'] = pnl
                position = 0

            # 空头时间止损记录（仅用于对比说明，原版无此逻辑故注释掉）
            # elif (current_date - trades[-1]['date']).days > 10:
            #     ...

        current_position_value = 0
        if position == 1:
            current_position_value = (current_close - entry_price) * lot_size * contract_multiplier
        elif position == -1:
            current_position_value = (entry_price - current_close) * lot_size * contract_multiplier

        equity_curve.append({'date': current_date, 'equity': capital + current_position_value})

    if len(trades) == 0:
        return None

    winning = [t for t in trades if t.get('pnl', 0) > 0]
    losing = [t for t in trades if t.get('pnl', 0) < 0]
    win_rate = len(winning) / len(trades) * 100
    total_profit = sum(t['pnl'] for t in winning)
    total_loss = abs(sum(t['pnl'] for t in losing))
    profit_factor = total_profit / total_loss if total_loss > 0 else float('inf')
    final_equity = equity_curve[-1]['equity'] if equity_curve else capital
    total_return = (final_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100

    edf = pd.DataFrame(equity_curve)
    edf['cummax'] = edf['equity'].cummax()
    edf['drawdown'] = (edf['equity'] - edf['cummax']) / edf['cummax'] * 100
    max_dd = edf['drawdown'].min()

    return {
        "name": name,
        "bars": len(df_daily),
        "trades": len(trades),
        "wins": len(winning),
        "losses": len(losing),
        "win_rate": win_rate,
        "total_return": total_return,
        "max_drawdown": max_dd,
        "profit_factor": profit_factor,
        "final_equity": final_equity,
        "total_profit": total_profit,
        "total_loss": total_loss,
        "trades_list": trades,
    }


def main():
    print("=" * 70)
    print(f"三重滤网回测 — 从 data/kline/ Parquet 文件加载数据")
    print(f"回测区间: {START_DATE} → {END_DATE}")
    print("=" * 70)

    results = []
    for name, cfg in PRODUCTS.items():
        filepath = os.path.join(KLINE_DIR, cfg["file"])
        if not os.path.exists(filepath):
            print(f"\n[{name}] ⚠️ 文件不存在: {filepath}")
            continue

        t0 = time.time()
        print(f"\n[{name}] 加载 {cfg['file']} ...", end=" ", flush=True)
        df = load_from_parquet(filepath, START_DATE, END_DATE)
        print(f"{len(df)} 根日线 ({time.time()-t0:.2f}s)")

        if len(df) < 50:
            print(f"  ⚠️ 数据不足 ({len(df)}根)，跳过")
            continue

        print(f"  日期: {df['date'].iloc[0].strftime('%Y-%m-%d')} → {df['date'].iloc[-1].strftime('%Y-%m-%d')}")

        result = run_backtest(name, df, cfg["multiplier"])
        if result:
            results.append(result)

    # 输出对比表
    print("\n" + "=" * 70)
    print("回测结果汇总 (Parquet 数据)")
    print("=" * 70)
    print(f"{'品种':<8} {'日线':<6} {'交易':<5} {'胜率':<7} {'收益率':<9} {'最大回撤':<9} {'盈亏比':<7}")
    print("-" * 70)
    for r in results:
        print(f"{r['name']:<8} {r['bars']:<6} {r['trades']:<5} "
              f"{r['win_rate']:.1f}%   {r['total_return']:+.2f}%   "
              f"{r['max_drawdown']:.2f}%   {r['profit_factor']:.2f}")

    # 详细交易记录
    for r in results:
        print(f"\n--- {r['name']} 交易记录 ---")
        for t in r['trades_list']:
            ep = t.get('exit_price', None)
            exit_str = f"→ {ep:.0f}" if ep else "→ 持仓中"
            exit_str += f"  盈亏:{t.get('pnl', 0):+.0f}"
            print(f"  {str(t['date'])[:10]} {t['action']} @ {t['price']:.0f}  {exit_str}")

    # 保存汇总
    summary_path = os.path.join(KLINE_DIR, "_backtest_parquet_summary.parquet")
    pd.DataFrame([{k: v for k, v in r.items() if k != 'trades_list'} for r in results]
                 ).to_parquet(summary_path, index=False)
    print(f"\n汇总已保存: {summary_path}")


if __name__ == '__main__':
    main()
