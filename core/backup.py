"""
Database backup module.

Export DuckDB to compressed archive for cloud backup.
Single file → much simpler than 17,851 Parquet files.

用法:
  python -m core.backup --local              # 本地备份
  python -m core.backup --github             # 上传到 GitHub Release
  python -m core.backup --github --push      # 备份+自动推送
"""
import os
import sys
import gzip
import shutil
import argparse
from datetime import datetime, timezone, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from core.database import DatabaseManager

BACKUP_DIR = os.path.join(REPO, "backups")


def backup_local():
    """Compress DuckDB file to gzipped backup."""
    db_path = os.path.join(REPO, "data", "futures.db")
    if not os.path.exists(db_path):
        print("[ERROR] No database file found.")
        return None

    os.makedirs(BACKUP_DIR, exist_ok=True)

    date_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    backup_name = f"futures_{date_str}.db.gz"
    backup_path = os.path.join(BACKUP_DIR, backup_name)

    print(f"[备份] 压缩 {os.path.getsize(db_path) / 1024 / 1024:.1f}MB 数据库...")
    t0 = datetime.now()

    with open(db_path, "rb") as f_in:
        with gzip.open(backup_path, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out, length=16 * 1024 * 1024)

    elapsed = (datetime.now() - t0).total_seconds()
    size = os.path.getsize(backup_path) / 1024 / 1024
    print(f"  完成: {backup_path} ({size:.1f}MB, {elapsed:.1f}s)")
    return backup_path


def backup_github():
    """Upload backup to GitHub Release."""
    gh = shutil.which("gh")
    if not gh:
        print("[ERROR] gh CLI not found.")
        return

    # First create local backup
    backup_path = backup_local()
    if not backup_path:
        return

    date_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    tag = f"data-{date_str}"

    # Check if auth works
    result = os.system("gh auth status > /dev/null 2>&1")
    if result != 0:
        print("[ERROR] gh not authenticated.")
        return

    print(f"\n[上传] GitHub Release: {tag}")

    # Create tag and release
    os.system(f"cd {REPO} && git tag -f {tag} > /dev/null 2>&1")
    os.system(f"cd {REPO} && git push origin {tag} -f > /dev/null 2>&1")

    cmd = (
        f'cd {REPO} && gh release create "{tag}" '
        f'"{backup_path}" '
        f'--title "Tick Data {date_str}" '
        f'--notes "DuckDB backup: 61 products, all timeframes" '
        f'2>&1'
    )
    result = os.popen(cmd).read()
    print(f"  {result}")


def show_stats():
    """Show database statistics."""
    db = DatabaseManager()
    products = db.list_products()
    total_ticks = sum(p["ticks"] for p in products)
    dates = db.list_dates()
    kline_count = db.conn.execute("SELECT COUNT(*) FROM klines").fetchone()[0]

    db_size = os.path.getsize(db.db_path) / 1024 / 1024
    print(f"Database: {db.db_path} ({db_size:.1f}MB)")
    print(f"Products: {len(products)}")
    print(f"Ticks: {total_ticks:,}")
    print(f"K-lines: {kline_count:,}")
    print(f"Dates: {len(dates)} ({dates[0]} ~ {dates[-1]})")

    # Per-product tick counts
    print(f"\nTop 10 by ticks:")
    for p in sorted(products, key=lambda x: x["ticks"], reverse=True)[:10]:
        print(f"  {p['product']:8s}: {p['ticks']:>12,} ticks, {p['trading_days']} days")

    db.close()


def main():
    parser = argparse.ArgumentParser(description="DuckDB backup")
    parser.add_argument("--local", action="store_true", help="Local gzip backup")
    parser.add_argument("--github", action="store_true", help="Upload to GitHub Release")
    parser.add_argument("--stats", action="store_true", help="Show DB stats")
    args = parser.parse_args()

    if args.stats:
        show_stats()
    elif args.github:
        backup_github()
    else:
        # Default: local backup
        backup_local()


if __name__ == "__main__":
    main()
