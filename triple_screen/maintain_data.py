#!/usr/bin/env python3
"""
Tick 数据维护工具
==================
自动将 30 天前的每日 tick 文件合并为月度 Parquet，减少碎片化。

用法:
  python maintain_data.py list                  # 列出所有数据状态
  python maintain_data.py merge                 # 合并 30 天前的每日文件 → 月度文件
  python maintain_data.py clean                 # 删除已合并的每日原始文件
  python maintain_data.py merge --age 60        # 合并 60 天前的数据
  python maintain_data.py merge --product 棉花   # 仅处理指定品种

目录结构:
  data/ticks/                            ← 每日 tick（子目录按品种组织）
    ├── 棉花/
    │   └── CZCE.CF/
    │       ├── 2026-06-22.parquet       ← 每日文件（热数据，最近30天）
    │       └── 2026-06-21.parquet
    ├── 铁矿石/
    │   └── DCE.i/
    │       └── ...
    └── _archive/                        ← 月度归档（温数据，30天~2年）
        ├── 棉花_2026-05.parquet         ← 月度合并文件
        └── 棉花_2026-04.parquet
"""

import os, sys, json
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Set, Tuple
from collections import defaultdict

import pandas as pd

# ============================================================
# 配置
# ============================================================

TICKS_DIR = Path(__file__).resolve().parent.parent / "data" / "ticks"
ARCHIVE_DIR = TICKS_DIR / "_archive"
DEFAULT_AGE_DAYS = 30  # 多少天前的数据可被归档


# ============================================================
# 扫描
# ============================================================

def scan_daily_files() -> Dict[str, Dict[str, List[Tuple[Path, date]]]]:
    """
    扫描所有每日 tick 文件。
    返回: {product_name: {contract_dir: [(path, date), ...]}}
    例如: {"棉花": {"CZCE.CF": [(Path(...), date(2026,6,1)), ...]}}
    """
    result: Dict[str, Dict[str, List[Tuple[Path, date]]]] = defaultdict(lambda: defaultdict(list))

    if not TICKS_DIR.exists():
        return result

    for product_dir in TICKS_DIR.iterdir():
        if not product_dir.is_dir():
            continue
        if product_dir.name.startswith("_"):
            continue  # 跳过 _archive, _summary 等

        product = product_dir.name

        for contract_dir in product_dir.iterdir():
            if not contract_dir.is_dir():
                continue

            con_name = contract_dir.name

            for f in sorted(contract_dir.iterdir()):
                if f.name.startswith("_"):
                    continue
                if f.suffix not in (".parquet", ".csv.gz"):
                    continue

                # 从文件名解析日期: "2026-06-22.parquet" → date(2026,6,22)
                stem = f.stem  # "2026-06-22"
                try:
                    file_date = date.fromisoformat(stem)
                except ValueError:
                    continue

                result[product][con_name].append((f, file_date))

    return result


def scan_archive_files() -> Dict[str, Dict[str, List[Tuple[Path, Tuple[int, int]]]]]:
    """
    扫描归档的月度文件。
    返回: {product: {contract: [(path, (year, month)), ...]}}
    """
    result: Dict[str, Dict[str, List]] = defaultdict(lambda: defaultdict(list))

    if not ARCHIVE_DIR.exists():
        return result

    for f in sorted(ARCHIVE_DIR.iterdir()):
        if not f.is_file():
            continue
        if f.suffix != ".parquet":
            continue

        # 从文件名解析: "棉花_2026-05.parquet" → product="棉花", (2026, 5)
        stem = f.stem
        try:
            # 格式: 品种名_YYYY-MM
            parts = stem.rsplit("_", 1)
            if len(parts) != 2:
                continue
            product, ym = parts
            year, month = map(int, ym.split("-"))
            result[product]["*"].append((f, (year, month)))
        except (ValueError, IndexError):
            continue

    return result


def age_days(file_date: date) -> int:
    """文件距今多少天"""
    return (date.today() - file_date).days


# ============================================================
# list 命令
# ============================================================

def cmd_list():
    """列出所有数据文件状态"""
    daily = scan_daily_files()
    archive = scan_archive_files()

    total_daily = 0
    total_archive = 0

    print(f"{'='*70}")
    print(f"Tick 数据文件状态")
    print(f"数据目录: {TICKS_DIR}")
    print(f"归档目录: {ARCHIVE_DIR}")
    print(f"当前日期: {date.today()}")
    print(f"{'='*70}")

    for product in sorted(set(list(daily.keys()) + list(archive.keys()))):
        daily_contracts = daily.get(product, {})
        archive_contracts = archive.get(product, {})

        # 收集每日文件信息
        daily_info = []
        for con, files in daily_contracts.items():
            dates = [d for _, d in files]
            if dates:
                ages = [age_days(d) for d in dates]
                daily_info.append(f"{con}: {len(files)}文件 ({min(dates)}~{max(dates)}, {min(ages)}~{max(ages)}天前)")

        # 收集归档信息
        archive_info = []
        for con, files in archive_contracts.items():
            if files:
                months = [f"{y}-{m:02d}" for _, (y, m) in files]
                archive_info.append(f"月度: {len(files)}文件 {months[0]}~{months[-1]}")

        product_total = sum(len(files) for con_files in daily_contracts.values() for files in [con_files])
        total_daily += product_total
        total_archive += sum(len(files) for con_files in archive_contracts.values() for files in [con_files])

        print(f"\n  📦 {product}  ({product_total} 每日文件)")
        for info in daily_info:
            print(f"      每日: {info}")
        for info in archive_info:
            print(f"      归档: {info}")

    print(f"\n{'='*70}")
    print(f"  总计: {total_daily} 个每日文件, {total_archive} 个月度归档文件")
    print(f"  建议: python maintain_data.py merge  (合并 {DEFAULT_AGE_DAYS} 天前的数据)")


# ============================================================
# merge 命令
# ============================================================

def cmd_merge(age: int = DEFAULT_AGE_DAYS, product_filter: str = None):
    """
    将 N 天前的每日 tick 合并为按月归档文件。
    不删除原始每日文件——需要单独执行 clean。
    """
    daily = scan_daily_files()

    if not daily:
        print("[INFO] 没有找到每日 tick 数据")
        return

    cutoff = date.today() - timedelta(days=age)
    print(f"[合并] 归档 {age} 天前的数据 (截止: {cutoff})")
    print(f"[合并] 输出目录: {ARCHIVE_DIR}\n")

    merged_count = 0

    for product, contracts in sorted(daily.items()):
        if product_filter and product != product_filter:
            continue

        for con, files in sorted(contracts.items()):
            # 筛选到期文件，按月份分组
            month_groups: Dict[str, List[Tuple[Path, date]]] = defaultdict(list)

            for fpath, file_date in files:
                if file_date <= cutoff:
                    month_key = file_date.strftime("%Y-%m")
                    month_groups[month_key].append((fpath, file_date))

            if not month_groups:
                continue

            for month_key, month_files in sorted(month_groups.items()):
                # 检查是否已有该月的归档
                out_path = ARCHIVE_DIR / f"{product}_{month_key}.parquet"
                if out_path.exists():
                    print(f"  [跳过] {out_path.name} (已存在)")
                    continue

                # 读取并合并
                print(f"  [合并] {product}/{con} → {product}_{month_key}.parquet ({len(month_files)} 天) ", end="", flush=True)

                try:
                    dfs = []
                    for fpath, _ in sorted(month_files, key=lambda x: x[1]):
                        df = pd.read_parquet(fpath)
                        dfs.append(df)

                    merged = pd.concat(dfs, ignore_index=True)
                    merged = merged.sort_values("datetime").reset_index(drop=True)

                    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
                    merged.to_parquet(out_path, index=False)

                    rows = len(merged)
                    size_kb = out_path.stat().st_size / 1024
                    print(f"→ {rows} ticks, {size_kb:.0f}KB ✓")
                    merged_count += 1

                except Exception as e:
                    print(f"✗ 失败: {e}")

    print(f"\n[完成] 共创建 {merged_count} 个月度归档文件")
    if merged_count > 0:
        print(f"       下一步: python maintain_data.py clean   (删除已归档的原始每日文件)")


# ============================================================
# clean 命令
# ============================================================

def cmd_clean(age: int = DEFAULT_AGE_DAYS, product_filter: str = None, dry_run: bool = False):
    """
    删除已有归档的每日 tick 原始文件。
    安全机制：只有当对应月份的归档文件存在时才删除。
    """
    daily = scan_daily_files()

    if not daily:
        print("[INFO] 没有找到每日 tick 数据")
        return

    cutoff = date.today() - timedelta(days=age)
    label = "[预览]" if dry_run else "[清理]"
    print(f"{label} 删除 {age} 天前的每日原始文件 (截止: {cutoff})")

    deleted = 0
    skipped = 0
    freed_bytes = 0

    for product, contracts in sorted(daily.items()):
        if product_filter and product != product_filter:
            continue

        for con, files in sorted(contracts.items()):
            for fpath, file_date in files:
                if file_date > cutoff:
                    continue

                month_key = file_date.strftime("%Y-%m")
                archive_file = ARCHIVE_DIR / f"{product}_{month_key}.parquet"

                if not archive_file.exists():
                    skipped += 1
                    continue  # 没有归档，不删

                size = fpath.stat().st_size
                freed_bytes += size

                if dry_run:
                    print(f"  {label} {fpath.parents[1].name}/{fpath.parent.name}/{fpath.name} ({size/1024:.0f}KB)")
                else:
                    try:
                        fpath.unlink()
                        print(f"  [删除] {fpath.parents[1].name}/{fpath.parent.name}/{fpath.name} ({size/1024:.0f}KB)")
                    except Exception as e:
                        print(f"  [失败] {fpath.name}: {e}")
                        skipped += 1
                        continue

                deleted += 1

    print(f"\n{label} 共删除 {deleted} 个文件, 跳过 {skipped} 个, 释放 {freed_bytes/1024/1024:.1f}MB")
    if dry_run:
        print("以上为预览，执行实际删除请运行: python maintain_data.py clean")

    # 清理空目录
    if not dry_run:
        for product_dir in sorted(TICKS_DIR.iterdir()):
            if not product_dir.is_dir() or product_dir.name.startswith("_"):
                continue
            for contract_dir in sorted(product_dir.iterdir()):
                if not contract_dir.is_dir():
                    continue
                remaining = list(contract_dir.iterdir())
                if not remaining:
                    contract_dir.rmdir()
            remaining = list(product_dir.iterdir())
            if not remaining:
                product_dir.rmdir()


# ============================================================
# 入口
# ============================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Tick 数据维护工具")
    sub = parser.add_subparsers(dest="command", help="命令")

    # list
    sub.add_parser("list", help="列出所有数据文件状态")

    # merge
    p_merge = sub.add_parser("merge", help="合并每日文件→月度归档")
    p_merge.add_argument("--age", type=int, default=DEFAULT_AGE_DAYS,
                         help=f"多少天前的数据可归档 (默认 {DEFAULT_AGE_DAYS})")
    p_merge.add_argument("--product", type=str, default=None,
                         help="仅处理指定品种 (如: 棉花)")

    # clean
    p_clean = sub.add_parser("clean", help="删除已归档的原始每日文件")
    p_clean.add_argument("--age", type=int, default=DEFAULT_AGE_DAYS,
                         help=f"多少天前的数据可清理 (默认 {DEFAULT_AGE_DAYS})")
    p_clean.add_argument("--product", type=str, default=None,
                         help="仅处理指定品种")
    p_clean.add_argument("--dry-run", action="store_true",
                         help="预览模式，不实际删除")

    # clean-preview
    p_preview = sub.add_parser("clean-preview", help="预览将被删除的文件 (等价于 clean --dry-run)")
    p_preview.add_argument("--age", type=int, default=DEFAULT_AGE_DAYS)
    p_preview.add_argument("--product", type=str, default=None)

    args = parser.parse_args()

    if args.command == "list":
        cmd_list()
    elif args.command == "merge":
        cmd_merge(age=args.age, product_filter=args.product)
    elif args.command == "clean":
        cmd_clean(age=args.age, product_filter=args.product, dry_run=False)
    elif args.command == "clean-preview":
        cmd_clean(age=args.age, product_filter=args.product, dry_run=True)
    else:
        # 默认显示帮助
        parser.print_help()


if __name__ == "__main__":
    main()
