#!/usr/bin/env python3
"""
三重滤网策略 - 统一回测入口
==============================

支持六个版本:
  v0  - 原始二层版 (MACD(8,24,9) + KD金叉)
  v1  - 三层版 (v0 + 60分钟精细择时)
  v2  - 动力系统版 (L1=周线MACD, L2=日线动力系统)
  v3  - 短周期版 (日-小时-15分钟, KD金叉, 需优化)
  v4  - 双层动力系统版(周-日-60)
  v5  - 双层动力系统版(日-小时-15)

用法:
  python run_backtest.py v0        # 运行v0
  python run_backtest.py v1        # 运行v1
  python run_backtest.py v2        # 运行v2
  python run_backtest.py v3        # 运行v3 (KD版)
  python run_backtest.py v3i       # 运行v3 (动力系统版)
  python run_backtest.py v4        # 运行v4
  python run_backtest.py v5        # 运行v5
  python run_backtest.py compare    # 六版本对比
  python run_backtest.py all       # 依次运行全部
"""
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

STRATEGIES = {
    'v0': {
        'name': '原始二层版',
        'file': 'triple_screen_optimized.py',
        'desc': 'MACD(8,24,9) + KD(14,3,3) 金叉进场, KD>70止盈',
        'func': 'run_detailed_backtest',
    },
    'v1': {
        'name': '三层版',
        'file': 'triple_screen_3layer.py',
        'desc': 'v0 + 60分钟精细择时, 避免追高杀低',
        'func': 'run_3layer_backtest',
    },
    'v2': {
        'name': '动力系统版',
        'file': 'triple_screen_impulse_v2.py',
        'desc': 'L1=周线MACD斜率, L2=日线EMA(13)+MACD柱颜色变化进场',
        'func': 'run_v2_backtest',
    },
    'v3': {
        'name': '短周期版(KD)',
        'file': 'triple_screen_v3.py',
        'desc': '日-小时-15分钟, KD金叉, 交易次数多（需优化）',
        'func': 'run_v3_backtest',
        'args': {'use_impulse': False},
    },
    'v3i': {
        'name': '短周期版(动力)',
        'file': 'triple_screen_v3.py',
        'desc': '日-小时-15分钟, 动力系统, 交易次数多（需优化）',
        'func': 'run_v3_backtest',
        'args': {'use_impulse': True},
    },
    'v4': {
        'name': '双层动力系统版(周-日-60)',
        'file': 'triple_screen_v4.py',
        'desc': 'L1=周线动力系统, L2=日线动力系统, L3=60分钟精细择时',
        'func': 'run_v4_backtest',
    },
    'v5': {
        'name': '双层动力系统版(日-小时-15)',
        'file': 'triple_screen_v5.py',
        'desc': 'L1=日线动力系统, L2=小时线动力系统, L3=15分钟精细择时',
        'func': 'run_v5_backtest',
    },
}


def run_version(version_key):
    """运行指定版本"""
    import importlib.util

    info = STRATEGIES[version_key]
    file_path = os.path.join(PROJECT_ROOT, 'triple_screen', info['file'])

    print(f"\n{'='*60}")
    print(f"正在运行: {info['name']} ({version_key})")
    print(f"文件: {info['file']}")
    print(f"策略: {info['desc']}")
    print(f"{'='*60}\n")

    spec = importlib.util.spec_from_file_location(info['file'], file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # 调用对应版本的回测函数
    func_name = info['func']
    func = getattr(module, func_name)
    args = info.get('args', {})
    trades, equity = func(**args)

    return trades, equity


def compare_versions():
    """五版本对比摘要"""
    import pandas as pd
    import importlib.util

    results = []

    for key in ['v0', 'v1', 'v2', 'v3', 'v4', 'v5']:
        info = STRATEGIES[key]
        file_path = os.path.join(PROJECT_ROOT, 'triple_screen', info['file'])
        spec = importlib.util.spec_from_file_location(info['file'], file_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            func_name = info['func']
            func = getattr(module, func_name)
            args = info.get('args', {})
            trades, equity = func(**args)

        output = f.getvalue()

        # 解析输出提取指标
        lines = output.split('\n')
        result = {'version': key, 'name': info['name']}

        for line in lines:
            if '总收益率' in line:
                try:
                    result['return'] = float(line.split(':')[1].strip().replace('%', '').replace('+', ''))
                except:
                    result['return'] = 0
            elif '胜率' in line and '交易统计' not in line:
                try:
                    result['win_rate'] = float(line.split(':')[1].strip().replace('%', ''))
                except:
                    result['win_rate'] = 0
            elif '盈亏比' in line:
                try:
                    result['profit_factor'] = float(line.split(':')[1].strip())
                except:
                    result['profit_factor'] = 0
            elif '最大回撤' in line:
                try:
                    result['max_dd'] = float(line.split(':')[1].strip().replace('%', ''))
                except:
                    result['max_dd'] = 0
            elif '总交易次数' in line:
                try:
                    result['trades'] = int(line.split(':')[1].strip())
                except:
                    result['trades'] = 0

        results.append(result)

    # 输出对比表
    print("\n" + "="*90)
    print("五版本回测对比 (2025-01 ~ 2026-06, 棉花CF0)")
    print("="*90)
    print(f"\n{'版本':<6} {'名称':<18} {'收益率':>8} {'胜率':>6} {'盈亏比':>6} {'最大回撤':>8} {'交易次数':>6}")
    print("-" * 90)

    for r in results:
        ret = r.get('return', 0)
        wr = r.get('win_rate', 0)
        pf = r.get('profit_factor', 0)
        md = r.get('max_dd', 0)
        t = r.get('trades', 0)
        print(f"{r['version']:<6} {r['name']:<18} {ret:>7.2f}% "
              f"{wr:>5.1f}% {pf:>5.2f}  "
              f"{md:>7.2f}% {t:>6}")

    print("\n推荐:")
    best_return = max(results, key=lambda x: x.get('return', -999))
    best_dd = max(results, key=lambda x: x.get('max_dd', -999))  # max_dd是负值,找最大(最接近0)
    print(f"  最高收益: {best_return['version']} ({best_return['name']})")
    print(f"  最小回撤: {best_dd['version']} ({best_dd['name']})")
    print("="*90)


if __name__ == '__main__':
    args = sys.argv[1:] if len(sys.argv) > 1 else ['compare']

    if len(args) == 0 or args[0] in ['help', '-h', '--help']:
        print(__doc__)
        sys.exit(0)

    version = args[0].lower()

    if version == 'compare':
        compare_versions()
    elif version == 'all':
        for k in ['v0', 'v1', 'v2', 'v3', 'v4']:
            run_version(k)
            print("\n" + "="*60)
    elif version in STRATEGIES:
        run_version(version)
    else:
        print(f"未知版本: {version}")
        print("支持: v0, v1, v2, v3, v3i, v4, compare, all")
        sys.exit(1)
