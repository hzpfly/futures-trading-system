#!/usr/bin/env python3
"""
三重滤网交易系统 - TqSdk 数据版
=============================
使用 TqSdk 获取 K线历史数据，策略逻辑离线计算。
与 triple_screen_akshare.py 相同的策略逻辑，方便对比结果。

第一层滤网（周线）: MACD柱斜率方向
第二层滤网（日线）: KD指标（超卖金叉做多，超买死叉做空）
第三层滤网（入场）: 次日开盘价入场

用法:
  python triple_screen_tqsdk.py
  python triple_screen_tqsdk.py --compare   # 同时运行AkShare版本并对比
"""

import sys
import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_tick_data import load_tqsdk_config, make_kqm_symbol

# ============================================================
# 回测参数（与 AkShare 优化版一致）
# ============================================================

PRODUCT_EXCHANGE = "CZCE"
PRODUCT_CODE = "CF"
PRODUCT_NAME = "棉花"

# 回测区间
BACKTEST_START = date(2025, 1, 1)
BACKTEST_END = date(2026, 6, 18)

# 资金
INITIAL_CAPITAL = 100000
CONTRACT_MULTIPLIER = 5
COMMISSION = 10

# 指标参数（优化版）
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 8, 24, 9
KD_N, KD_M1, KD_M2 = 14, 3, 3
OVERSOLD, OVERBOUGHT = 20, 80

# 风控
STOP_LOSS_PCT = 0.03
TIME_STOP_DAYS = 10
LOT_SIZE = 1


# ============================================================
# 指标计算
# ============================================================

def calc_macd(close: pd.Series, fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL):
    """MACD 指标"""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = 2 * (dif - dea)
    return dif, dea, hist


def calc_kd(high, low, close, n=KD_N, m1=KD_M1, m2=KD_M2):
    """KD 指标"""
    low_n = low.rolling(window=n).min()
    high_n = high.rolling(window=n).max()
    rsv = (close - low_n) / (high_n - low_n) * 100
    k = rsv.ewm(span=m1, adjust=False).mean()
    d = k.ewm(span=m2, adjust=False).mean()
    return k, d


# ============================================================
# 数据获取：通过 TqSdk 拉取 K 线数据
# ============================================================

def fetch_kline_data_tqsdk(symbol: str, start_dt: date, end_dt: date, timeout=120):
    """
    通过 TqSdk TqBacktest 拉取历史K线数据。
    使用短时间回测窗口绕过连接问题，一次性获取全部数据。

    Returns (daily_df, weekly_df) 或 (None, None)
    """
    from tqsdk import TqApi, TqAuth, TqBacktest, BacktestFinished

    user, pwd = load_tqsdk_config()

    # 扩大回测窗口确保足够数据（回测结束前至少一个月的额外数据用于指标计算）
    lookback_start = date(start_dt.year, max(1, start_dt.month - 2), 1)

    print(f"  [TqSdk] 连接回测引擎 (拉取 {start_dt} → {end_dt})...", flush=True)

    api = None
    try:
        api = TqApi(
            backtest=TqBacktest(start_dt=lookback_start, end_dt=end_dt),
            auth=TqAuth(user, pwd)
        )

        # 等待数据全部加载（不提前 break，让 backtest 完整运行）
        kd = api.get_kline_serial(symbol, 86400)   # 日线
        kw = api.get_kline_serial(symbol, 604800)   # 周线

        t0 = time.time()
        updates = 0
        try:
            while time.time() - t0 < timeout:
                api.wait_update()
                updates += 1
        except BacktestFinished:
            # Backtest 完成数据加载，包含事件驱动跑完整段历史
            pass

        # 转为 DataFrame
        if len(kd) == 0:
            print("  [ERROR] TqSdk 返回空数据")
            return None, None

        def to_df(kl_series, bar_type="daily"):
            cols = ['datetime', 'open', 'high', 'low', 'close', 'volume']
            data = {}
            for col in cols:
                data[col] = kl_series[col].to_numpy()
            df = pd.DataFrame(data)
            # 转换纳秒时间戳
            df['datetime'] = pd.to_datetime(df['datetime'], unit='ns')
            df = df.set_index('datetime').sort_index()
            # 去除重复索引
            df = df[~df.index.duplicated(keep='last')]
            return df

        daily_df = to_df(kd)
        weekly_df = to_df(kw)

        # 裁剪到回测区间
        daily_df = daily_df.loc[str(start_dt):str(end_dt)]
        weekly_df = weekly_df.loc[weekly_df.index <= str(end_dt)]

        print(f"  [TqSdk] 日线 {len(daily_df)} 条, 周线 {len(weekly_df)} 条")
        return daily_df, weekly_df

    except Exception as e:
        print(f"  [ERROR] TqSdk 数据获取失败: {e}")
        return None, None
    finally:
        if api:
            try:
                api.close()
            except Exception:
                pass


# ============================================================
# 回测逻辑（与 AkShare 版本完全一致）
# ============================================================

def run_strategy(daily_df: pd.DataFrame, weekly_df: pd.DataFrame, product_name: str,
                 start_dt: date, end_dt: date) -> list:
    """运行三重滤网策略，返回 trades 列表"""

    print(f"\n  [策略] 开始逐日回测...")
    print(f"  {'='*50}")

    trades = []
    position = 0
    entry_price = 0.0
    entry_idx = -1

    # 预计算周线 MACD
    dif_w, dea_w, hist_w = calc_macd(weekly_df['close'])

    # 预计算日线 KD
    k_vals, d_vals = calc_kd(daily_df['high'], daily_df['low'], daily_df['close'])

    min_idx = max(MACD_SLOW + 3, KD_N + 3)

    for i in range(min_idx, len(daily_df)):
        current_date = daily_df.index[i]
        current_open = daily_df['open'].iloc[i]
        current_close = daily_df['close'].iloc[i]

        # 找到当前日期对应的最近一周
        week_idx = weekly_df.index.searchsorted(current_date, side='right') - 1
        if week_idx < 2:
            continue

        # 周线 MACD 柱斜率
        if week_idx >= len(hist_w):
            continue
        wh_now = hist_w.iloc[week_idx]
        wh_prev = hist_w.iloc[max(0, week_idx - 1)]
        slope = wh_now - wh_prev

        if wh_now > 0 and slope > 0:
            weekly_trend = 'UP'
        elif wh_now < 0 and slope < 0:
            weekly_trend = 'DOWN'
        else:
            weekly_trend = 'NEUTRAL'

        # 日线 KD 值
        k_now = k_vals.iloc[i]
        d_now = d_vals.iloc[i]
        k_prev = k_vals.iloc[i - 1]
        d_prev = d_vals.iloc[i - 1]

        # --- 平仓检查 ---
        exit_reason = None

        if position == 1:
            if current_close <= entry_price * (1 - STOP_LOSS_PCT):
                exit_reason = "止损"
            elif (k_now > OVERBOUGHT and not np.isnan(k_prev)
                  and k_now < d_now and k_prev >= d_prev):
                exit_reason = "止盈"
            elif (entry_idx >= 0 and (i - entry_idx) > TIME_STOP_DAYS):
                exit_reason = "时间止损"

        elif position == -1:
            if current_close >= entry_price * (1 + STOP_LOSS_PCT):
                exit_reason = "止损"
            elif (k_now < OVERSOLD and not np.isnan(k_prev)
                  and k_now > d_now and k_prev <= d_prev):
                exit_reason = "止盈"

        if exit_reason and position != 0:
            if position == 1:
                pnl = (current_close - entry_price) * LOT_SIZE * CONTRACT_MULTIPLIER - COMMISSION * 2 * LOT_SIZE
                label = "⚪ 平多"
            else:
                pnl = (entry_price - current_close) * LOT_SIZE * CONTRACT_MULTIPLIER - COMMISSION * 2 * LOT_SIZE
                label = "⚪ 平空"

            trades[-1]["exit_date"] = str(current_date)[:10]
            trades[-1]["exit_price"] = current_close
            trades[-1]["pnl"] = pnl
            trades[-1]["reason"] = exit_reason
            print(f"    {str(current_date)[:10]} {label}({exit_reason}) @ {current_close:.1f}  PnL:{pnl:+.0f}")
            position = 0
            entry_price = 0
            entry_idx = -1

        # --- 入场检查 ---
        if position == 0 and not np.isnan(k_now) and not np.isnan(k_prev):
            if weekly_trend == 'UP' and k_now > d_now and k_prev <= d_prev:
                position = 1
                entry_price = current_close
                entry_idx = i
                trades.append({
                    "entry_date": str(current_date)[:10],
                    "direction": "LONG",
                    "entry_price": current_close,
                    "exit_date": "",
                    "exit_price": 0.0,
                    "pnl": 0.0,
                    "reason": "",
                })
                print(f"    {str(current_date)[:10]} 🟢 做多 @ {current_close:.1f}")

            elif weekly_trend == 'DOWN' and k_now < d_now and k_prev >= d_prev:
                position = -1
                entry_price = current_close
                entry_idx = i
                trades.append({
                    "entry_date": str(current_date)[:10],
                    "direction": "SHORT",
                    "entry_price": current_close,
                    "exit_date": "",
                    "exit_price": 0.0,
                    "pnl": 0.0,
                    "reason": "",
                })
                print(f"    {str(current_date)[:10]} 🔴 做空 @ {current_close:.1f}")

    # 强制平仓未平持仓
    if position != 0 and len(daily_df) > 0:
        final_close = daily_df['close'].iloc[-1]
        if position == 1:
            pnl = (final_close - entry_price) * LOT_SIZE * CONTRACT_MULTIPLIER - COMMISSION * 2 * LOT_SIZE
        else:
            pnl = (entry_price - final_close) * LOT_SIZE * CONTRACT_MULTIPLIER - COMMISSION * 2 * LOT_SIZE
        trades[-1]["exit_date"] = str(daily_df.index[-1])[:10]
        trades[-1]["exit_price"] = final_close
        trades[-1]["pnl"] = pnl
        trades[-1]["reason"] = "回测结束平仓"

    print(f"  {'='*50}")
    return trades


# ============================================================
# 统计输出
# ============================================================

def print_stats(trades: list, label="TqSdk"):
    """打印回测统计"""
    completed = [t for t in trades if t["pnl"] != 0]
    open_trades = [t for t in trades if t["pnl"] == 0]

    if not completed:
        print(f"\n  [{label}] 无已完成交易")
        return

    wins = [t for t in completed if t["pnl"] > 0]
    losses = [t for t in completed if t["pnl"] < 0]
    total_pnl = sum(t["pnl"] for t in completed)
    total_win = sum(t["pnl"] for t in wins) if wins else 0
    total_loss = abs(sum(t["pnl"] for t in losses)) if losses else 0

    print(f"\n  [{label}] {'='*50}")
    print(f"  总交易次数      : {len(completed)} (完成) + {len(open_trades)} (未平)")
    print(f"  盈利次数        : {len(wins)}")
    print(f"  亏损次数        : {len(losses)}")
    print(f"  胜率            : {len(wins)/len(completed)*100:.1f}%")
    print(f"  总盈亏          : {total_pnl:+,.0f} 元")
    print(f"  总盈利          : {total_win:,.0f} 元")
    print(f"  总亏损          : {total_loss:,.0f} 元")
    print(f"  盈亏比          : {total_win/total_loss:.2f}" if total_loss > 0 else f"  盈亏比          : ∞")
    print(f"  最终权益        : {INITIAL_CAPITAL + total_pnl:,.0f} 元")
    print(f"  总收益率        : {total_pnl/INITIAL_CAPITAL*100:.2f}%")

    # 最大回撤
    eq_curve = []
    running_pnl = 0
    for t in completed:
        running_pnl += t["pnl"]
        eq_curve.append(INITIAL_CAPITAL + running_pnl)

    if eq_curve:
        eq_s = pd.Series(eq_curve)
        dd = (eq_s - eq_s.cummax()) / eq_s.cummax() * 100
        print(f"  最大回撤        : {dd.min():.2f}%")

    return completed


# ============================================================
# 主流程
# ============================================================

def run_backtest():
    print("=" * 60, flush=True)
    print(f"三重滤网交易系统回测 - {PRODUCT_NAME}期货 (TqSdk数据)", flush=True)
    print(f"回测区间: {BACKTEST_START} → {BACKTEST_END}", flush=True)
    print("=" * 60, flush=True)

    main_symbol = make_kqm_symbol(PRODUCT_EXCHANGE, PRODUCT_CODE)
    print(f"\n[数据] 品种: {main_symbol}")

    # 通过 TqSdk 拉取数据
    daily_df, weekly_df = fetch_kline_data_tqsdk(main_symbol, BACKTEST_START, BACKTEST_END)

    if daily_df is None:
        print("\n[失败] 无法获取 TqSdk 数据。请检查:\n"
              "  1. TqSdk 账号密码是否配置正确 (~/.futures_config.toml)\n"
              "  2. 网络连通性\n"
              "  3. 是否在交易时段 (非交易时段连接较慢)")
        return

    # 运行策略
    trades = run_strategy(daily_df, weekly_df, PRODUCT_NAME, BACKTEST_START, BACKTEST_END)

    # 输出统计
    print_stats(trades, "TqSdk版")

    # 交易记录
    print(f"\n  --- 交易记录 (全部) ---")
    for t in trades:
        status = f"PnL:{t['pnl']:+.0f}" if t['pnl'] != 0 else "持仓中"
        print(f"  {t['entry_date']} {t['direction']:5s} {t['entry_price']:>8.1f}"
              f" → {t['exit_date']:>10s} {t['exit_price']:>8.1f}"
              f"  {status:>12s}  [{t['reason']}]")

    # 保存报告
    report_dir = Path(__file__).resolve().parent.parent / "reports"
    report_dir.mkdir(exist_ok=True)
    report_path = report_dir / "triple_screen_tqsdk_report.txt"

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"三重滤网交易系统回测报告 - {PRODUCT_NAME}期货 (TqSdk数据)\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"回测区间: {BACKTEST_START} → {BACKTEST_END}\n")
        f.write(f"初始资金: {INITIAL_CAPITAL:,} 元\n")
        f.write(f"品种: {PRODUCT_NAME} ({main_symbol})\n\n")

        completed = [t for t in trades if t["pnl"] != 0]
        if completed:
            total_pnl = sum(t["pnl"] for t in completed)
            wins = len([t for t in completed if t["pnl"] > 0])
            losses = len([t for t in completed if t["pnl"] < 0])
            f.write(f"交易次数: {len(completed)}\n")
            f.write(f"胜率: {wins/len(completed)*100:.1f}%\n")
            f.write(f"总盈亏: {total_pnl:+,.0f} 元\n")
            f.write(f"总收益率: {total_pnl/INITIAL_CAPITAL*100:.2f}%\n\n")

        f.write("交易记录:\n")
        for t in trades:
            pnl_str = f"{t['pnl']:+,.0f}" if t['pnl'] != 0 else "持仓中"
            f.write(f"  {t['entry_date']} {t['direction']} {t['entry_price']:.1f}"
                    f" → {t['exit_date']} {t['exit_price']:.1f} PnL:{pnl_str} [{t['reason']}]\n")

    print(f"\n  ✅ 报告已保存: {report_path}")
    return trades


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="三重滤网交易系统回测 - TqSdk数据版")
    parser.add_argument("--compare", action="store_true",
                        help="同时运行 AkShare 版本并对比结果")
    args = parser.parse_args()

    tqsdk_trades = run_backtest()

    if args.compare and tqsdk_trades:
        print("\n" + "=" * 60)
        print("[对比] 运行 AkShare 版本...")
        print("=" * 60)
        # 导入并运行 AkShare 版本
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            # 运行 AkShare 版本的核心策略逻辑
            from triple_screen_akshare import run_backtest as run_akshare
            akshare_trades = run_akshare()
            if akshare_trades:
                print_stats(akshare_trades, "AkShare版")
        except Exception as e:
            print(f"  [ERROR] AkShare 版本运行失败: {e}")
