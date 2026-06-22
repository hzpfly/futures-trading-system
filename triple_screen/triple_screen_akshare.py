#!/usr/bin/env python3
"""
三重滤网交易系统 - 棉花期货(CF)完整回测
使用 AkShare 真实历史数据

第一层滤网（周线）: MACD柱斜率 + EXPMA(13)位置
第二层滤网（日线）: KD指标（超卖做多，超买做空）
第三层滤网（入场）: 次日开盘价或突破前高入场
"""
import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

# ========== 参数设置 ==========
SYMBOL = "CF0"  # 棉花主力合约
START_DATE = "20250101"
END_DATE = "20260618"
INITIAL_CAPITAL = 100000  # 初始资金（元）
CONTRACT_MULTIPLIER = 5   # 棉花期货合约乘数（5吨/手）
COMMISSION = 10            # 单边手续费（元/手）

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
# ==============================


def get_history_data(symbol, start_date, end_date):
    """获取历史数据"""
    print(f"正在获取 {symbol} 历史数据...")
    df = ak.futures_main_sina(symbol=symbol, start_date=start_date, end_date=end_date)
    
    # 统一列名
    df.columns = ['date', 'open', 'high', 'low', 'close', 'volume', 'open_interest', 'settle']
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    
    print(f"✅ 获取成功！共 {len(df)} 根日K线")
    print(f"时间范围: {df['date'].min()} 至 {df['date'].max()}")
    return df


def resample_to_weekly(df_daily):
    """将日线数据重采样为周线"""
    df = df_daily.copy()
    df.set_index('date', inplace=True)
    
    # 重采样
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
    """计算MACD指标"""
    # DIF
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    
    # DEA
    dea = dif.ewm(span=signal, adjust=False).mean()
    
    # MACD柱
    macd_hist = 2 * (dif - dea)
    
    return dif, dea, macd_hist


def calc_expma(df, period=EXPMA_PERIOD):
    """计算EXPMA（指数平滑移动平均线）"""
    expma = df['close'].ewm(span=period, adjust=False).mean()
    return expma


def calc_kd(df, period=KD_PERIOD, ma1=KD_MA1, ma2=KD_MA2):
    """计算KD指标（随机指标）"""
    low_n = df['low'].rolling(window=period).min()
    high_n = df['high'].rolling(window=period).max()
    
    # %K值
    rsv = (df['close'] - low_n) / (high_n - low_n) * 100
    k = rsv.ewm(span=ma1, adjust=False).mean()
    d = k.ewm(span=ma2, adjust=False).mean()
    
    return k, d


def run_triple_screen_backtest():
    """运行三重滤网回测"""
    print("\n" + "=" * 60)
    print("三重滤网交易系统回测 - 棉花期货(CF)")
    print("=" * 60)
    
    # 1. 获取数据
    df_daily = get_history_data(SYMBOL, START_DATE, END_DATE)
    df_weekly = resample_to_weekly(df_daily)
    
    # 2. 计算指标
    print("\n正在计算指标...")
    
    # 周线指标
    df_weekly['macd_dif'], df_weekly['macd_dea'], df_weekly['macd_hist'] = calc_macd(df_weekly)
    df_weekly['expma13'] = calc_expma(df_weekly, EXPMA_PERIOD)
    df_weekly['macd_hist_prev'] = df_weekly['macd_hist'].shift(1)
    df_weekly['macd_slope'] = df_weekly['macd_hist'] - df_weekly['macd_hist_prev']
    
    # 日线指标
    df_daily['k'], df_daily['d'] = calc_kd(df_daily)
    df_daily['k_prev'] = df_daily['k'].shift(1)
    df_daily['d_prev'] = df_daily['d'].shift(1)
    
    print("✅ 指标计算完成")
    
    # 调试：查看指标分布
    print("\n指标统计:")
    print(f"周线MACD柱 > 0 的比例: {(df_weekly['macd_hist'] > 0).sum() / len(df_weekly) * 100:.1f}%")
    print(f"日线K值 < 20 (超卖) 的比例: {(df_daily['k'] < OVERSOLD).sum() / len(df_daily) * 100:.1f}%")
    print(f"日线K值 > 80 (超买) 的比例: {(df_daily['k'] > OVERBOUGHT).sum() / len(df_daily) * 100:.1f}%")
    
    # 3. 回测循环
    print("\n开始回测...")
    
    capital = INITIAL_CAPITAL
    position = 0  # 0: 空仓, 1: 持多单, -1: 持空单
    entry_price = 0
    lot_size = 1  # 固定1手（简化测试）
    trades = []
    equity_curve = []
    
    for i in range(30, len(df_daily) - 1):  # 前30根跳过，-1确保有次日数据
        current_date = df_daily.iloc[i]['date']
        current_close = df_daily.iloc[i]['close']
        
        # 获取当周的周线数据
        week_start = current_date - pd.Timedelta(days=current_date.weekday())
        week_data = df_weekly[df_weekly['date'] <= week_start].tail(2)
        
        if len(week_data) < 2:
            continue
        
        # 第一层滤网（周线）- 简化条件
        weekly_macd_hist = week_data.iloc[-1]['macd_hist']
        weekly_macd_slope = week_data.iloc[-1]['macd_slope']
        weekly_expma13 = week_data.iloc[-1]['expma13']
        
        # 趋势判断：MACD柱 > 0 且斜率向上 = 多头趋势
        #           MACD柱 < 0 且斜率向下 = 空头趋势
        if weekly_macd_hist > 0 and weekly_macd_slope > 0:
            weekly_trend = 'UP'
        elif weekly_macd_hist < 0 and weekly_macd_slope < 0:
            weekly_trend = 'DOWN'
        else:
            weekly_trend = 'NEUTRAL'
        
        # 第二层滤网（日线KD）
        daily_k = df_daily.iloc[i]['k']
        daily_d = df_daily.iloc[i]['d']
        daily_k_prev = df_daily.iloc[i-1]['k']
        daily_d_prev = df_daily.iloc[i-1]['d']
        
        # 交易信号
        if position == 0:  # 空仓时寻找入场机会
            # 周线向上 + KD金叉（从超卖区向上）
            if weekly_trend == 'UP':
                # KD金叉
                if daily_k > daily_d and daily_k_prev <= daily_d_prev:
                    # 入场：次日开盘价
                    entry_price = df_daily.iloc[i+1]['open']
                    position = 1
                    trades.append({
                        'date': df_daily.iloc[i+1]['date'],
                        'action': 'BUY',
                        'price': entry_price,
                        'lots': lot_size,
                        'pnl': 0
                    })
                    print(f"{df_daily.iloc[i+1]['date'].date()}: 🟢 做多入场 @ {entry_price:.0f}, {lot_size}手")
            
            # 周线向下 + KD死叉（从超买区向下）
            elif weekly_trend == 'DOWN':
                # KD死叉
                if daily_k < daily_d and daily_k_prev >= daily_d_prev:
                    # 入场：次日开盘价
                    entry_price = df_daily.iloc[i+1]['open']
                    position = -1
                    trades.append({
                        'date': df_daily.iloc[i+1]['date'],
                        'action': 'SELL',
                        'price': entry_price,
                        'lots': lot_size,
                        'pnl': 0
                    })
                    print(f"{df_daily.iloc[i+1]['date'].date()}: 🔴 做空入场 @ {entry_price:.0f}, {lot_size}手")
        
        # 平仓逻辑
        elif position == 1:  # 持多单
            # 止损：跌破前低或-2%
            stop_loss = entry_price * 0.98
            if current_close < stop_loss:
                exit_price = current_close
                pnl = (exit_price - entry_price) * lot_size * CONTRACT_MULTIPLIER - COMMISSION * 2 * lot_size
                capital += pnl
                trades[-1]['exit_date'] = current_date
                trades[-1]['exit_price'] = exit_price
                trades[-1]['pnl'] = pnl
                print(f"{current_date.date()}: ⚪ 平多(止损) @ {exit_price:.0f}, 盈亏: {pnl:+.0f}")
                position = 0
            
            # 止盈：KD超买区死叉 或 周线趋势反转
            elif daily_k > OVERBOUGHT and daily_k_prev >= daily_d_prev and daily_k < daily_d:
                exit_price = current_close
                pnl = (exit_price - entry_price) * lot_size * CONTRACT_MULTIPLIER - COMMISSION * 2 * lot_size
                capital += pnl
                trades[-1]['exit_date'] = current_date
                trades[-1]['exit_price'] = exit_price
                trades[-1]['pnl'] = pnl
                print(f"{current_date.date()}: ✅ 平多(止盈) @ {exit_price:.0f}, 盈亏: {pnl:+.0f}")
                position = 0
            
            # 时间止损：持仓超过10天
            elif (current_date - trades[-1]['date']).days > 10 * 86400e9:  # 约10个交易日
                exit_price = current_close
                pnl = (exit_price - entry_price) * lot_size * CONTRACT_MULTIPLIER - COMMISSION * 2 * lot_size
                capital += pnl
                trades[-1]['exit_date'] = current_date
                trades[-1]['exit_price'] = exit_price
                trades[-1]['pnl'] = pnl
                print(f"{current_date.date()}: ⏰ 平多(时间止损) @ {exit_price:.0f}, 盈亏: {pnl:+.0f}")
                position = 0
        
        elif position == -1:  # 持空单
            # 止损：突破前高或+2%
            stop_loss = entry_price * 1.02
            if current_close > stop_loss:
                exit_price = current_close
                pnl = (entry_price - exit_price) * lot_size * CONTRACT_MULTIPLIER - COMMISSION * 2 * lot_size
                capital += pnl
                trades[-1]['exit_date'] = current_date
                trades[-1]['exit_price'] = exit_price
                trades[-1]['pnl'] = pnl
                print(f"{current_date.date()}: ⚪ 平空(止损) @ {exit_price:.0f}, 盈亏: {pnl:+.0f}")
                position = 0
            
            # 止盈：KD超卖区金叉
            elif daily_k < OVERSOLD and daily_k_prev <= daily_d_prev and daily_k > daily_d:
                exit_price = current_close
                pnl = (entry_price - exit_price) * lot_size * CONTRACT_MULTIPLIER - COMMISSION * 2 * lot_size
                capital += pnl
                trades[-1]['exit_date'] = current_date
                trades[-1]['exit_price'] = exit_price
                trades[-1]['pnl'] = pnl
                print(f"{current_date.date()}: ✅ 平空(止盈) @ {exit_price:.0f}, 盈亏: {pnl:+.0f}")
                position = 0
        
        # 记录权益曲线
        current_position_value = 0
        if position == 1:
            current_position_value = (current_close - entry_price) * lot_size * CONTRACT_MULTIPLIER
        elif position == -1:
            current_position_value = (entry_price - current_close) * lot_size * CONTRACT_MULTIPLIER
        
        equity_curve.append({
            'date': current_date,
            'equity': capital + current_position_value,
            'close': current_close
        })
    
    # 4. 输出回测报告
    print("\n" + "=" * 60)
    print("回测报告")
    print("=" * 60)
    
    if len(trades) == 0:
        print("❌ 没有产生任何交易")
        return
    
    # 计算统计指标
    total_trades = len(trades)
    winning_trades = [t for t in trades if t.get('pnl', 0) > 0]
    losing_trades = [t for t in trades if t.get('pnl', 0) < 0]
    win_rate = len(winning_trades) / total_trades * 100 if total_trades > 0 else 0
    
    total_profit = sum([t['pnl'] for t in winning_trades])
    total_loss = abs(sum([t['pnl'] for t in losing_trades]))
    profit_factor = total_profit / total_loss if total_loss > 0 else float('inf')
    
    final_equity = equity_curve[-1]['equity'] if len(equity_curve) > 0 else capital
    total_return = (final_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    
    print(f"\n初始资金: {INITIAL_CAPITAL:,.0f} 元")
    print(f"最终权益: {final_equity:,.0f} 元")
    print(f"总收益率: {total_return:.2f}%")
    print(f"总交易次数: {total_trades}")
    print(f"盈利次数: {len(winning_trades)}")
    print(f"亏损次数: {len(losing_trades)}")
    print(f"胜率: {win_rate:.1f}%")
    print(f"总盈利: {total_profit:,.0f} 元")
    print(f"总亏损: {total_loss:,.0f} 元")
    print(f"盈亏比: {profit_factor:.2f}")
    
    # 最大回撤
    equity_df = pd.DataFrame(equity_curve)
    equity_df['cummax'] = equity_df['equity'].cummax()
    equity_df['drawdown'] = (equity_df['equity'] - equity_df['cummax']) / equity_df['cummax'] * 100
    max_drawdown = equity_df['drawdown'].min()
    print(f"最大回撤: {max_drawdown:.2f}%")
    
    print("\n" + "=" * 60)
    print("交易记录（最近10笔）")
    print("=" * 60)
    for t in trades[-10:]:
        exit_price = t.get('exit_price', '持仓中')
        pnl = t.get('pnl', 0)
        print(f"{t['date'].date()}: {t['action']} @ {t['price']:.0f} => {exit_price}, 盈亏: {pnl:+.0f}")
    
    # 保存详细报告
    report_path = "/Users/michaelhe/WorkBuddy/Claw/triple_screen_report.txt"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("三重滤网交易系统回测报告 - 棉花期货(CF)\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"回测时间: {START_DATE} 至 {END_DATE}\n")
        f.write(f"初始资金: {INITIAL_CAPITAL:,.0f} 元\n")
        f.write(f"最终权益: {final_equity:,.0f} 元\n")
        f.write(f"总收益率: {total_return:.2f}%\n")
        f.write(f"总交易次数: {total_trades}\n")
        f.write(f"胜率: {win_rate:.1f}%\n")
        f.write(f"盈亏比: {profit_factor:.2f}\n")
        f.write(f"最大回撤: {max_drawdown:.2f}%\n\n")
        f.write("交易记录:\n")
        for t in trades:
            f.write(f"{t['date'].date()}: {t['action']} @ {t['price']:.0f}, 盈亏: {t.get('pnl', 0):+.0f}\n")
    
    print(f"\n✅ 详细报告已保存到: {report_path}")


if __name__ == '__main__':
    run_triple_screen_backtest()
