"""
SQL-based K-line resampling using DuckDB.

Replaces the pandas-based resample_klines.py.
All aggregation runs inside DuckDB — zero Python overhead, 100x faster.

用法:
  python -m core.klines                             # 处理所有产品的所有历史数据
  python -m core.klines --product 棉花                # 单品种
  python -m core.klines --product 棉花 --start 2026-06-01  # 指定起始日期
"""
import os
import sys
import argparse

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from core.database import DatabaseManager
import pandas as pd

TIMEFRAMES = ["1min", "5min", "15min", "60min", "day", "week"]


def resample_all(db: DatabaseManager, product: str = None, start_date: str = None):
    """Resample all products (or single) across all timeframes.

    Uses DuckDB's time_bucket() for lightning-fast aggregation.
    """
    products = [product] if product else [p["product"] for p in db.list_products()]
    if not products:
        print("[INFO] No tick data in database.")
        return

    print(f"Products: {len(products)}")
    print(f"Timeframes: {TIMEFRAMES}")
    print()

    totals = {tf: 0 for tf in TIMEFRAMES}

    for prod in products:
        print(f"  {prod}...", end=" ")
        count = 0
        for tf in TIMEFRAMES:
            try:
                df = db.resample_klines(prod, tf, start_date)
                if not df.empty:
                    df["timeframe"] = tf
                    db.upsert_klines(df, tf)
                    totals[tf] += len(df)
                    count += 1
            except Exception as e:
                print(f"  ⚠️ {tf}: {e}", end="")
        print(f"done ({count}/{len(TIMEFRAMES)} timeframes)")

    print("\n" + "=" * 50)
    print("K-line 生成汇总:")
    for tf in TIMEFRAMES:
        print(f"  {tf:>6}: {totals[tf]:>8} 根")
    total_bars = sum(totals.values())
    print(f"  {'总计':>6}: {total_bars:>8} 根")
    print("=" * 50)


def show_summary(db: DatabaseManager, product: str = None):
    """Show kline summary for inspection."""
    products = [product] if product else [p["product"] for p in db.list_products()]

    for prod in products[:5]:  # limit
        for tf in TIMEFRAMES:
            try:
                df = db.get_klines(prod, tf)
                if not df.empty:
                    print(f"  {prod:6s} {tf:>6} | {len(df):>6} bars | "
                          f"{df['datetime'].min().strftime('%m/%d')} ~ "
                          f"{df['datetime'].max().strftime('%m/%d')} | "
                          f"last close: {df['close'].iloc[-1]:.0f}")
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="DuckDB K-line resampler")
    parser.add_argument("--product", type=str, default=None, help="单个品种名")
    parser.add_argument("--start", type=str, default=None, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--summary", action="store_true", help="显示现有K线概况")
    args = parser.parse_args()

    db = DatabaseManager()

    if args.summary:
        show_summary(db, args.product)
    else:
        print(f"DB: {db.db_path}")
        resample_all(db, args.product, args.start)

    db.close()


if __name__ == "__main__":
    main()
