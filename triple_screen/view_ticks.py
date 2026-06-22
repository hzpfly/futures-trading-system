#!/usr/bin/env python3
"""
查看 Tick 数据文件
==================
用法:
  python view_ticks.py                        # 列出所有文件，显示摘要
  python view_ticks.py 棉花                    # 查看棉花今日数据
  python view_ticks.py 棉花 2026-06-22         # 查看指定日期
  python view_ticks.py 棉花 2026-06-22 night   # 查看指定时段
  python view_ticks.py --all                   # 所有品种汇总统计
"""

import sys
import pandas as pd
from pathlib import Path
from datetime import datetime

TICKS_DIR = Path(__file__).resolve().parent.parent / "data" / "ticks"


def parse_filename(f: Path):
    """
    统一解析文件名，返回 (product, contract, date_str, session)
    支持新旧两种格式:
      新: 数据/ticks/棉花/CZCE.CF/2026-06-22_day.parquet
      旧: 数据/ticks/tick_棉花_CZCE.CF609_2026-06-22.parquet
    """
    rel = f.relative_to(TICKS_DIR)
    parts = rel.parts

    if f.name.startswith("tick_"):
        # 旧格式: tick_棉花_CZCE.CF609_2026-06-22.parquet
        stem = f.stem.replace("tick_", "")
        segs = stem.rsplit("_", 1)  # ["棉花_CZCE.CF609", "2026-06-22"]
        if len(segs) != 2:
            return None
        name_contract, date_str = segs
        idx = name_contract.find("_")
        if idx < 0:
            return None
        product = name_contract[:idx]
        contract = name_contract[idx+1:]
        return (product, contract, date_str, None)
    elif len(parts) >= 3:
        # 新格式: 棉花/CZCE.CF/2026-06-22_day.parquet
        product = parts[0]
        contract = parts[1]  # "CZCE.CF"
        stem = f.stem  # "2026-06-22_day" or "2026-06-22"
        if "_" in stem:
            date_str, session = stem.rsplit("_", 1)
        else:
            date_str, session = stem, None
        return (product, contract, date_str, session)
    return None


def find_files(product=None, date_str=None, session=None):
    """递归搜索所有 tick 文件"""
    files = []
    for f in sorted(TICKS_DIR.rglob("*.parquet")):
        info = parse_filename(f)
        if info is None:
            continue
        p, contract, dstr, sess = info
        if product and product not in p:
            continue
        if date_str and dstr != date_str:
            continue
        if session and sess != session:
            continue
        files.append(f)
    return files


def file_label(f):
    """文件的可读标签"""
    info = parse_filename(f)
    if info is None:
        return f.name
    product, contract, date_str, session = info
    label = f"{product} {contract} {date_str}"
    if session:
        label += f" [{session}]"
    return label


def print_summary(files):
    """打印所有文件摘要"""
    if not files:
        print("[无数据]")
        return

    print(f"{'品种':<10} {'合约':<18} {'日期':<12} {'时段':>6} {'Ticks':>8} {'价格区间':>20} {'大小':>8}")
    print("-" * 84)

    for f in files:
        try:
            df = pd.read_parquet(f)
            if df.empty:
                print(f"  [空文件] {f.name}")
                continue

            info = parse_filename(f)
            if info is None:
                continue
            product, contract, date_str, session = info

            price_min = df["last_price"].min()
            price_max = df["last_price"].max()
            size_kb = f.stat().st_size / 1024
            sess_label = session or "-"

            print(f"{product:<10} {contract:<18} {date_str:<12} {sess_label:>6} {len(df):>8} {price_min:.0f} ~ {price_max:<.0f} {size_kb:>6.0f}KB")
        except Exception as e:
            print(f"[错误] {f.name}: {e}")

    print("-" * 84)
    print(f"共 {len(files)} 个文件")


def print_detail(files):
    """打印单个文件的详细内容"""
    for f in files:
        df = pd.read_parquet(f)
        if df.empty:
            print(f"[空文件] {f.name}")
            continue

        info = parse_filename(f)
        if info is None:
            continue
        product, contract, date_str, session = info
        sess_label = f" [{session}]" if session else ""
        size_kb = f.stat().st_size / 1024

        print(f"\n{'='*80}")
        print(f"  {product} | {contract} | {date_str}{sess_label} | {len(df)} ticks | {size_kb:.0f}KB")
        print(f"  时间: {df['datetime'].min()} ~ {df['datetime'].max()}")
        print(f"  价格: {df['last_price'].min():.0f} ~ {df['last_price'].max():.0f}  均值: {df['last_price'].mean():.1f}")
        print(f"  成交量: {df['volume'].iloc[-1]:.0f}  持仓量: {df['open_interest'].iloc[-1]:.0f}")
        print(f"{'='*80}")

        # 打印前10和后5条
        print(f"\n  {'时间':<30} {'最新价':>8} {'买一价':>8} {'卖一价':>8} {'成交量':>10} {'持仓量':>10}")
        print("  " + "-" * 76)

        rows = min(10, len(df))
        for _, row in df.head(rows).iterrows():
            ts = str(row["datetime"])[:19]
            print(f"  {ts:<30} {row['last_price']:>8.0f} {row['bid_price1']:>8.0f} {row['ask_price1']:>8.0f} {row['volume']:>10.0f} {row['open_interest']:>10.0f}")

        if len(df) > 15:
            print(f"  ... (省略 {len(df)-15} 条) ...")

        tail_rows = min(5, len(df) - 10)
        if tail_rows > 0:
            for _, row in df.tail(tail_rows).iterrows():
                ts = str(row["datetime"])[:19]
                print(f"  {ts:<30} {row['last_price']:>8.0f} {row['bid_price1']:>8.0f} {row['ask_price1']:>8.0f} {row['volume']:>10.0f} {row['open_interest']:>10.0f}")


def print_all_summary():
    """所有品种汇总"""
    files = find_files()
    if not files:
        print("[无数据]")
        return

    by_product = {}
    for f in files:
        df = pd.read_parquet(f)
        if df.empty:
            continue
        product = df["product"].iloc[0]
        if product not in by_product:
            by_product[product] = {"files": 0, "ticks": 0, "min_price": float("inf"), "max_price": float("-inf"), "total_kb": 0}
        by_product[product]["files"] += 1
        by_product[product]["ticks"] += len(df)
        by_product[product]["min_price"] = min(by_product[product]["min_price"], df["last_price"].min())
        by_product[product]["max_price"] = max(by_product[product]["max_price"], df["last_price"].max())
        by_product[product]["total_kb"] += f.stat().st_size / 1024

    print(f"\n{'品种':<10} {'文件数':>6} {'总Ticks':>10} {'价格区间':>20} {'总大小':>10}")
    print("-" * 65)
    for pname, info in sorted(by_product.items()):
        print(f"{pname:<10} {info['files']:>6} {info['ticks']:>10} {info['min_price']:.0f} ~ {info['max_price']:<.0f} {info['total_kb']:>7.0f}KB")


def main():
    args = sys.argv[1:]

    if not args:
        files = find_files()
        print_summary(files)
        return

    if "--all" in args:
        print_all_summary()
        return

    # 解析参数: product [date] [session]
    product = None
    date_str = None
    session = None
    for a in args:
        if a.startswith("--"):
            continue
        if a in ("day", "night"):
            session = a
        elif "-" in a and len(a) >= 8:
            date_str = a
        else:
            product = a

    files = find_files(product=product, date_str=date_str, session=session)

    if product:
        print_detail(files)
    else:
        print_summary(files)


if __name__ == "__main__":
    main()
