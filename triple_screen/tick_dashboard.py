#!/usr/bin/env python3
"""
Tick Data Web Dashboard
展示当日期货Tick数据的交互式Web看板
"""

import os
import sys
import json
import glob
from datetime import datetime, timezone, timedelta
from functools import lru_cache

import pandas as pd
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__)

TZ_BEIJING = timezone(timedelta(hours=8))
DATA_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'ticks')
HTML_DIR = os.path.dirname(os.path.abspath(__file__))

# 使用中的日期（第一次请求时确定）
_used_date = None


def get_best_date():
    """获取最佳数据日期：先试今天，没有数据就用昨天"""
    today = datetime.now(TZ_BEIJING)
    yesterday = today - timedelta(days=1)
    for d in [today, yesterday]:
        ds = d.strftime('%Y-%m-%d')
        # 快速检查：只看第一层目录
        try:
            for item in os.listdir(DATA_ROOT):
                item_path = os.path.join(DATA_ROOT, item)
                if os.path.isdir(item_path):
                    # 查看子目录是否存在匹配的文件
                    for sub in os.listdir(item_path):
                        sub_path = os.path.join(item_path, sub)
                        if os.path.isdir(sub_path):
                            files = glob.glob(os.path.join(sub_path, f'{ds}_*.parquet'))
                            if files:
                                return ds
                elif ds in item and item.endswith('.parquet'):
                    return ds
        except Exception:
            pass
    return today.strftime('%Y-%m-%d')


def scan_products(date_str):
    """快速扫描所有产品及其文件数量（不读取parquet内容）"""
    products = {}
    if not os.path.exists(DATA_ROOT):
        return products

    for product_dir in sorted(os.listdir(DATA_ROOT)):
        product_path = os.path.join(DATA_ROOT, product_dir)
        if not os.path.isdir(product_path):
            continue

        for contract_dir in sorted(os.listdir(product_path)):
            contract_path = os.path.join(product_path, contract_dir)
            if not os.path.isdir(contract_path):
                continue

            day_files = glob.glob(os.path.join(contract_path, f'{date_str}_day_*.parquet'))
            night_files = glob.glob(os.path.join(contract_path, f'{date_str}_night_*.parquet'))

            if day_files or night_files:
                products[product_dir] = {
                    'contract': contract_dir,
                    'day_files': sorted(day_files),
                    'night_files': sorted(night_files),
                    'day_count': len(day_files),
                    'night_count': len(night_files),
                }

    return products


_products_cache = None


def get_products():
    """获取产品列表（带缓存）"""
    global _used_date, _products_cache
    if _products_cache is not None:
        return _products_cache, _used_date

    _used_date = get_best_date()
    _products_cache = scan_products(_used_date)
    return _products_cache, _used_date


def compute_summary(file_list):
    """从一组parquet文件中计算摘要统计"""
    if not file_list:
        return None

    dfs = []
    for f in file_list:
        try:
            df = pd.read_parquet(f)
            dfs.append(df)
        except Exception:
            continue

    if not dfs:
        return None

    df = pd.concat(dfs, ignore_index=True)
    df = df.sort_values('datetime').drop_duplicates(subset=['datetime', 'last_price'])
    df['datetime_bj'] = df['datetime'].dt.tz_localize('UTC').dt.tz_convert(TZ_BEIJING)

    last = df.iloc[-1] if len(df) > 0 else None
    prices = df['last_price'].dropna()

    if len(prices) > 1:
        change = prices.iloc[-1] - prices.iloc[0]
        change_pct = (change / prices.iloc[0]) * 100
    else:
        change = 0
        change_pct = 0

    return {
        'ticks': len(df),
        'open': float(prices.iloc[0]) if len(prices) > 0 else None,
        'high': float(df['highest'].max()) if 'highest' in df.columns else float(prices.max()),
        'low': float(df['lowest'].min()) if 'lowest' in df.columns else float(prices.min()),
        'last': float(prices.iloc[-1]),
        'change': round(change, 4),
        'change_pct': round(change_pct, 2),
        'volume': int(df['volume'].sum()) if 'volume' in df.columns else 0,
        'amount': float(df['amount'].sum()) if 'amount' in df.columns else 0,
        'open_interest': float(last['open_interest']) if last is not None and pd.notna(last.get('open_interest')) else 0,
        'time_start': str(df['datetime_bj'].iloc[0]),
        'time_end': str(df['datetime_bj'].iloc[-1]),
        'contract': str(df.iloc[0].get('contract', 'N/A')) if len(df) > 0 else 'N/A',
    }


def load_tick_data(file_list):
    """加载tick数据用于图表"""
    if not file_list:
        return None

    dfs = []
    for f in file_list:
        try:
            df = pd.read_parquet(f)
            dfs.append(df)
        except Exception:
            continue

    if not dfs:
        return None

    df = pd.concat(dfs, ignore_index=True)
    df = df.sort_values('datetime').drop_duplicates(subset=['datetime', 'last_price'])
    df['datetime_bj'] = df['datetime'].dt.tz_localize('UTC').dt.tz_convert(TZ_BEIJING)

    result = df[['datetime_bj', 'last_price', 'volume', 'open_interest',
                  'ask_price1', 'bid_price1', 'highest', 'lowest']].copy()
    result = result.dropna(subset=['last_price'])

    # 采样避免传输过大
    max_points = 2000
    if len(result) > max_points:
        step = len(result) // max_points
        result = result.iloc[::step]

    return {
        'count': len(result),
        'time_start': str(result['datetime_bj'].iloc[0]),
        'time_end': str(result['datetime_bj'].iloc[-1]),
        'data': [
            {
                'time': str(row['datetime_bj']),
                'price': float(row['last_price']),
                'volume': int(row['volume']) if pd.notna(row['volume']) else 0,
                'oi': float(row['open_interest']) if pd.notna(row['open_interest']) else 0,
                'ask1': float(row['ask_price1']) if pd.notna(row['ask_price1']) else None,
                'bid1': float(row['bid_price1']) if pd.notna(row['bid_price1']) else None,
                'high': float(row['highest']) if pd.notna(row['highest']) else None,
                'low': float(row['lowest']) if pd.notna(row['lowest']) else None,
            }
            for _, row in result.iterrows()
        ]
    }


# ========== API Routes ==========

@app.route('/api/products')
def api_products():
    """获取所有产品列表（轻量，只含文件计数）"""
    products, date = get_products()
    # 只返回轻量信息
    result = {}
    for name, info in products.items():
        result[name] = {
            'contract': info['contract'],
            'day_files': info['day_count'],
            'night_files': info['night_count'],
            'has_day': info['day_count'] > 0,
            'has_night': info['night_count'] > 0,
        }
    return jsonify({'products': result, 'count': len(result), 'date': date})


@app.route('/api/summary/<product_name>')
def api_summary(product_name):
    """获取单个产品的摘要统计"""
    products, date = get_products()
    if product_name not in products:
        return jsonify({'error': 'product not found'}), 404

    info = products[product_name]
    day_summary = compute_summary(info['day_files'])
    night_summary = compute_summary(info['night_files'])

    return jsonify({
        'product': product_name,
        'contract': info['contract'],
        'date': date,
        'day': day_summary,
        'night': night_summary,
    })


@app.route('/api/ticks/<product_name>/<session>')
def api_ticks(product_name, session):
    """获取某个产品的tick数据"""
    if session not in ('day', 'night'):
        return jsonify({'error': 'session must be day or night'}), 400

    products, date = get_products()
    if product_name not in products:
        return jsonify({'error': 'product not found'}), 404

    info = products[product_name]
    files = info['day_files'] if session == 'day' else info['night_files']
    if not files:
        return jsonify({'error': 'no data', 'product': product_name, 'session': session}), 404

    data = load_tick_data(files)
    if data is None:
        return jsonify({'error': 'load failed'}), 500

    data['contract'] = info['contract']
    data['session'] = session
    data['date'] = date
    return jsonify(data)


@app.route('/api/refresh')
def api_refresh():
    """强制刷新缓存"""
    global _used_date, _products_cache
    _used_date = None
    _products_cache = None
    products, date = get_products()
    return jsonify({'status': 'ok', 'count': len(products), 'date': date})


@app.route('/')
def index():
    return send_from_directory(HTML_DIR, 'tick_dashboard.html')


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5070
    print(f'Tick Dashboard starting at http://127.0.0.1:{port}')
    app.run(host='0.0.0.0', port=port, debug=False)
