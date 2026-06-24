#!/usr/bin/env python3
"""
Tick → 多周期 K 线重采样
从 tick 数据生成 1min / 5min / 15min / 60min / day / week K线

用法:
  python resample_klines.py                          # 处理今天所有产品
  python resample_klines.py 2026-06-24               # 处理指定日期
  python resample_klines.py 2026-06-24 棉花           # 处理指定日期+产品
  python resample_klines.py --all                     # 处理所有历史tick数据
"""
import os
import sys
import glob
import argparse
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

# 项目路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
TICK_DIR = os.path.join(REPO_DIR, 'data', 'ticks')
KLINE_DIR = os.path.join(REPO_DIR, 'data', 'klines')

TIMEFRAMES = {
    '1min':  '1min',
    '5min':  '5min',
    '15min': '15min',
    '60min': '60min',
    'day':   '1D',
    'week':  '1W-MON',  # 周一开始
}

KLINE_COLS = ['open', 'high', 'low', 'close', 'volume', 'amount', 'open_interest']


def load_ticks(date_str, product=None):
    """加载指定日期/产品的所有 tick 数据"""
    if product:
        # product like '棉花/CZCE.CF'
        pattern = os.path.join(TICK_DIR, product, f'{date_str}_*.parquet')
        files = sorted(glob.glob(pattern))
    else:
        pattern = os.path.join(TICK_DIR, '**', f'{date_str}_*.parquet')
        files = sorted(glob.glob(pattern, recursive=True))

    if not files:
        return None

    dfs = []
    for f in files:
        try:
            df = pd.read_parquet(f)
            dfs.append(df)
        except Exception:
            pass

    if not dfs:
        return None

    df = pd.concat(dfs, ignore_index=True)

    # 确保有 datetime 列
    if 'datetime' not in df.columns and 'time' in df.columns:
        df['datetime'] = pd.to_datetime(df['time'])
    if 'datetime' not in df.columns:
        return None

    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.sort_values('datetime').reset_index(drop=True)

    # 只保留需要的列
    cols = ['datetime', 'last_price', 'volume', 'amount', 'open_interest']
    available = [c for c in cols if c in df.columns]
    df = df[available].copy()
    df = df.rename(columns={'last_price': 'price'})

    # 价格去掉0值（可能的数据异常）
    df = df[df['price'] > 0]

    return df


def resample_klines(df, timeframe):
    """将 tick DataFrame 重采样为 K 线"""
    freq = TIMEFRAMES[timeframe]
    df = df.set_index('datetime')

    ohlc = df['price'].resample(freq).ohlc()
    ohlc.columns = ['open', 'high', 'low', 'close']

    result = ohlc.copy()

    # volume: 用累计量的差值
    if 'volume' in df.columns:
        vol_end = df['volume'].resample(freq).last()
        vol_start = df['volume'].resample(freq).first()
        result['volume'] = vol_end - vol_start
    else:
        result['volume'] = 0

    # amount: 用累计成交额的差值
    if 'amount' in df.columns:
        amt_end = df['amount'].resample(freq).last()
        amt_start = df['amount'].resample(freq).first()
        result['amount'] = amt_end - amt_start
    else:
        result['amount'] = 0

    # open_interest: 取最新
    if 'open_interest' in df.columns:
        result['open_interest'] = df['open_interest'].resample(freq).last()
    else:
        result['open_interest'] = 0

    # 去掉全 NaN 的行
    result = result.dropna(subset=['open', 'close'], how='all')

    return result.reset_index()


def save_klines(df_klines, product, contract, timeframe, date_str):
    """保存/追加 K 线数据到文件"""
    tf_dir = os.path.join(KLINE_DIR, timeframe)
    os.makedirs(tf_dir, exist_ok=True)

    fname = f'{contract}_{timeframe}.parquet'
    fpath = os.path.join(tf_dir, fname)

    if os.path.exists(fpath):
        existing = pd.read_parquet(fpath)
        existing['datetime'] = pd.to_datetime(existing['datetime'])

        # 合并：去重，新数据覆盖旧数据
        combined = pd.concat([existing, df_klines], ignore_index=True)
        combined = combined.drop_duplicates(subset='datetime', keep='last')
        combined = combined.sort_values('datetime')
    else:
        existing_dates = set()
        combined = df_klines

    combined.to_parquet(fpath, index=False)


def process_product(product_dir, date_str):
    """处理一个品种的所有合约"""
    results = {}
    full_path = os.path.join(TICK_DIR, product_dir)

    if not os.path.isdir(full_path):
        return results

    for contract_dir in os.listdir(full_path):
        contract_path = os.path.join(full_path, contract_dir)
        if not os.path.isdir(contract_path):
            continue

        # 检查是否有该日期的 tick 文件
        files = glob.glob(os.path.join(contract_path, f'{date_str}_*.parquet'))
        if not files:
            continue

        print(f'  {product_dir}/{contract_dir}: {len(files)} tick files')

        df = load_ticks(date_str, f'{product_dir}/{contract_dir}')
        if df is None or len(df) == 0:
            continue

        for tf in TIMEFRAMES:
            try:
                df_k = resample_klines(df, tf)
                if len(df_k) > 0:
                    save_klines(df_k, product_dir, contract_dir, tf, date_str)
                    if tf not in results:
                        results[tf] = 0
                    results[tf] += len(df_k)
            except Exception as e:
                print(f'    ⚠️ {tf} resample error: {e}')

    return results


def main():
    parser = argparse.ArgumentParser(description='Tick → K-line 重采样')
    parser.add_argument('date', nargs='?', default=None, help='日期 YYYY-MM-DD（默认今天）')
    parser.add_argument('product', nargs='?', default=None, help='品种名（可选）')
    parser.add_argument('--all', action='store_true', help='处理所有历史数据')
    args = parser.parse_args()

    if args.date is None:
        args.date = datetime.now().strftime('%Y-%m-%d')

    if args.product:
        products = [args.product]
    else:
        products = sorted([
            d for d in os.listdir(TICK_DIR)
            if os.path.isdir(os.path.join(TICK_DIR, d))
        ])

    print(f'日期: {args.date}')
    print(f'品种数: {len(products)}')
    print(f'时间框架: {list(TIMEFRAMES.keys())}')
    print()

    total = {tf: 0 for tf in TIMEFRAMES}

    for p in products:
        result = process_product(p, args.date)
        for tf, count in result.items():
            total[tf] += count

    print()
    print('=' * 50)
    print('K线生成汇总:')
    for tf in TIMEFRAMES:
        print(f'  {tf:>6}: {total[tf]:>6} 根')
    print(f'  输出目录: {KLINE_DIR}')
    print('=' * 50)


if __name__ == '__main__':
    main()
