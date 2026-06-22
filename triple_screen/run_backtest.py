#!/usr/bin/env python3
"""
三重滤网策略 - 统一回测入口
==============================

支持四个版本:
  v0  - 原始二层版 (MACD(8,24,9) + KD金叉)
  v1  - 三层版 (v0 + 60分钟精细择时)
  v2  - 动力系统版 (EMA(13) + MACD柱颜色变化)
  v3  - 短周期版 (日-小时-15分钟, 交易次数多但需优化)

用法:
  python run_backtest.py v0        # 运行v0
  python run_backtest.py v1        # 运行v1
  python run_backtest.py v2        # 运行v2
  python run_backtest.py v3        # 运行v3 (KD版)
  python run_backtest.py v3i       # 运行v3 (动力系统版)
  python run_backtest.py compare    # 四版本对比
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
        'func': 'run_backtest',
    },
    'v1': {
        'name': '三层版',
        'file': 'triple_screen_3layer.py',
        'desc': 'v0 + 60分钟精细择时, 避免追高杀低',
        'func': 'run_backtest',
    },
    'v2': {
        'name': '动力系统版',
        'file': 'triple_screen_impulse_v2.py',
        'desc': 'EMA(13)+MACD柱颜色变化进场, 动能衰竭即出场',
        'func': 'run_backtest',
    },
    'v3': {
        'name': '短周期KD版',
        'file': 'triple_screen_v3.py',
        'desc': '日-小时-15分钟, L2=KD, 交易次数多(需优化)',
        'func': 'run_backtest',
    },
    'v3i': {
        'name': '短周期动力系统版',
        'file': 'triple_screen_v3.py',
        'desc': '日-小时-15分钟, L2=动力系统, 交易次数多(需优化)',
        'func': 'run_backtest',
        'impulse': True,
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
    kwargs = {}
    if version_key == 'v3' or version_key == 'v3i':
        kwargs['use_impulse'] = info.get('impulse', False)
        trades, equity = module.run_backtest(**kwargs)
    elif version_key == 'v0':
        trades, equity = module.run_detailed_backtest()
    elif version_key == 'v1':
        trades, equity = module.run_3layer_backtest()
    elif version_key == 'v2':
        trades, equity = module.run_v2_backtest()

    return trades, equity


def compare_versions():
    """四版本对比摘要"""
    import pandas as pd
    import importlib.util

    results = []

    for key in ['v0', 'v1', 'v2', 'v3', 'v3i']:
        info = STRATEGIES[key]
        file_path = os.path.join(PROJECT_ROOT, 'triple_screen', info['file'])
        spec = importlib.util.spec_from_file_location(info['file'], file_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # 捕获输出并提取关键指标
        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            if key == 'v0':
                trades, equity = module.run_detailed_backtest()
            elif key == 'v1':
                trades, equity = module.run_3layer_backtest()
            elif key == 'v2':
                trades, equity = module.run_v2_backtest()
            elif key in ['v3', 'v3i']:
                use_impulse = info.get('impulse', False)
                result = module.run_backtest(use_impulse=use_impulse)
                trades = result.get('trades', [])
                equity = result.get('return', 0)

        output = f.getvalue()

        # 解析输出提取指标
        lines = output.split('\n')
        result = {'version': key, 'name': info['name']}

        for line in lines:
            if '总收益率' in line:
                result['return'] = float(line.split(':')[1].strip().replace('%', '').replace('+', ''))
            elif '胜率' in line and '交易统计' not in line:
                result['win_rate'] = float(line.split(':')[1].strip().replace('%', ''))
            elif '盈亏比' in line:
                result['profit_factor'] = float(line.split(':')[1].strip())
            elif '最大回撤' in line:
                result['max_dd'] = float(line.split(':')[1].strip().replace('%', ''))
            elif '交易次数' in line:
                result['trades'] = int(line.split(':')[1].strip())

        results.append(result)

    # 输出对比表
    print("\n" + "=" * 80)
    print("四版本回测对比 (2024-01 ~ 2026-06, 棉花CF0)")
    print("=" * 80)
    print(f"\n{'版本':<6} {'名称':<20} {'收益率':>8} {'胜率':>6} {'盈亏比':>6} {'最大回撤':>8} {'交易次数':>6}")
    print("-" * 80)

    for r in results:
        print(f"{r['version']:<6} {r['name']:<20} {r.get('return','?'):>7.2f}% "
              f"{r.get('win_rate','?'):>5.1f}% {r.get('profit_factor','?'):>5.2f}  "
              f"{r.get('max_dd','?'):>7.2f}% {r.get('trades','?'):>6}")

    print("\n推荐:")
    best_return = max(results, key=lambda x: x.get('return', -999))
    # 最大回撤是负值，找最接近0的（即 max_dd 最大）
    best_dd = max(results, key=lambda x: x.get('max_dd', -999))
    print(f"  最高收益: {best_return['version']} ({best_return['name']})")
    print(f"  最小回撤: {best_dd['version']} ({best_dd['name']})")
    print("=" * 80)


if __name__ == '__main__':
    args = sys.argv[1:] if len(sys.argv) > 1 else ['compare']

    if len(args) == 0 or args[0] in ['help', '-h', '--help']:
        print(__doc__)
        sys.exit(0)

    version = args[0].lower()

    if version == 'compare':
        compare_versions()
    elif version == 'all':
        for k in ['v0', 'v1', 'v2', 'v3', 'v3i']:
            run_version(k)
            print("\n" + "=" * 60)
    elif version in STRATEGIES:
        run_version(version)
    else:
        print(f"未知版本: {version}")
        print("支持: v0, v1, v2, v3, v3i, compare, all")
        sys.exit(1)
