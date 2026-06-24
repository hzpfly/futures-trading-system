"""
One-shot migration: import all existing Parquet tick files → DuckDB.

Reads from data/ticks/{品种}/{交易所}.{代码}/{date}_{session}_*.parquet
Writes to data/futures.db

用法:
  python scripts/migrate.py                       # 迁移所有
  python scripts/migrate.py --product 棉花          # 单品种
  python scripts/migrate.py --date 2026-06-24      # 单日
  python scripts/migrate.py --dry-run              # 预览不写入
"""
import os
import sys
import glob
import argparse
import time
from datetime import datetime

import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from core.database import DatabaseManager

TICK_DIR = os.path.join(REPO, "data", "ticks")


def parse_filename(fname: str) -> dict:
    """Parse {date}_{session}_{tag}_{seq}.parquet or {date}_{session}.parquet"""
    base = os.path.splitext(fname)[0]
    parts = base.split("_")
    if len(parts) >= 2:
        return {"date": parts[0], "session": parts[1]}
    return {"date": "unknown", "session": "unknown"}


def migrate(db: DatabaseManager, product: str = None, date_str: str = None, dry_run: bool = False):
    """Scan tick directories and import into DuckDB."""
    pattern = os.path.join(TICK_DIR, "**", "*.parquet")
    if product:
        pattern = os.path.join(TICK_DIR, product, "**", "*.parquet")
    if date_str:
        pattern = pattern.replace("*.parquet", f"{date_str}_*.parquet")

    files = sorted(glob.glob(pattern, recursive=True))
    print(f"Found {len(files)} Parquet files")
    if dry_run:
        print("[DRY RUN] No data will be written.\n")

    # Group files by (product, exchange, symbol, date)
    # Path: data/ticks/{品种}/{交易所}.{代码}/{date}_...parquet
    file_groups = {}
    for fpath in files:
        rel = os.path.relpath(fpath, TICK_DIR)
        parts = rel.split(os.sep)
        if len(parts) < 3:
            continue
        prod = parts[0]
        excode = parts[1]  # e.g. CZCE.CF
        fname = parts[2]
        meta = parse_filename(fname)

        key = (prod, excode, meta["date"], meta["session"])
        if key not in file_groups:
            file_groups[key] = {"session": meta["session"], "files": []}
        file_groups[key]["files"].append(fpath)

    print(f"Grouped into {len(file_groups)} (product, exchange, date, session) groups\n")

    total_rows = 0
    total_groups = len(file_groups)
    skipped = 0

    for i, ((prod, excode, fdate, fsession), group) in enumerate(sorted(file_groups.items())):
        # Parse exchange.code
        excode_parts = excode.split(".", 1)
        exchange = excode_parts[0]
        symbol = excode_parts[1] if len(excode_parts) > 1 else excode

        # Check if already exists in DB
        if not dry_run:
            existing = db.conn.execute(
                "SELECT COUNT(*) FROM ticks WHERE product = ? AND symbol = ? AND session = ? AND CAST(datetime AS DATE) = CAST(? AS DATE)",
                [prod, symbol, fsession, fdate],
            ).fetchone()[0]
            if existing > 0:
                skipped += 1
                continue

        sessions = set()
        dfs = []
        for f in group["files"]:
            try:
                df = pd.read_parquet(f)
                dfs.append(df)
            except Exception as e:
                print(f"  ⚠️ skip {os.path.basename(f)}: {e}")

        if not dfs:
            continue

        df = pd.concat(dfs, ignore_index=True)

        if "datetime" in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"])

        # Build output DataFrame
        out = pd.DataFrame()
        out["datetime"] = df["datetime"]
        out["product"] = prod
        out["exchange"] = exchange
        out["symbol"] = symbol
        out["session"] = fsession
        out["last_price"] = df.get("last_price", 0)
        out["volume"] = df.get("volume", 0)
        out["open_interest"] = df.get("open_interest", 0)
        out["bid_price1"] = df.get("bid_price1", 0)
        out["bid_volume1"] = df.get("bid_volume1", 0)
        out["ask_price1"] = df.get("ask_price1", 0)
        out["ask_volume1"] = df.get("ask_volume1", 0)

        out = out[out["last_price"] > 0]

        if dry_run:
            print(f"  [{i+1}/{total_groups}] {prod}/{excode} {fdate} | {len(out)} ticks (DRY)")
        else:
            db.insert_ticks(out)
            total_rows += len(out)
            print(f"  [{i+1}/{total_groups}] {prod}/{excode} {fdate} | {len(out)} ticks ✓")

    print(f"\n{'='*50}")
    if dry_run:
        print(f"Preview: {total_groups} groups, would write {total_rows} ticks")
    else:
        print(f"Imported {total_rows} ticks ({total_groups - skipped} groups); skipped {skipped} existing")
        # Show DB stats
        product_count = db.conn.execute("SELECT COUNT(DISTINCT product) FROM ticks").fetchone()[0]
        tick_count = db.conn.execute("SELECT COUNT(*) FROM ticks").fetchone()[0]
        date_count = db.conn.execute("SELECT COUNT(DISTINCT CAST(datetime AS DATE)) FROM ticks").fetchone()[0]
        db_size = os.path.getsize(db.db_path) / 1024 / 1024
        print(f"DB stats: {tick_count:,} ticks, {product_count} products, {date_count} dates, {db_size:.1f}MB")
    print("=" * 50)


def main():
    parser = argparse.ArgumentParser(description="Parquet → DuckDB 迁移")
    parser.add_argument("--product", type=str, default=None)
    parser.add_argument("--date", type=str, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    t0 = time.time()
    db = DatabaseManager()
    migrate(db, args.product, args.date, args.dry_run)
    elapsed = time.time() - t0
    print(f"\n耗时: {elapsed:.1f}s")
    db.close()


if __name__ == "__main__":
    main()
