"""
一次性拉取今日所有主力合约 tick 数据。
策略: 一次性 get_tick_serial() 订阅所有 60+ symbol，只 wait_update 一次（行情全到本地），
然后逐个导出 parquet。比逐个 symbol wait 快 10x。

用法:
    python fetch_main_contracts_today.py              # 拉取今日数据
    python fetch_main_contracts_today.py --date 2026-06-23  # 拉取指定日期（需TqSdk回放支持）
"""
import sys
import time
import json
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_tick_data import PRODUCTS, load_tqsdk_config

from tqsdk import TqApi, TqAuth
import pandas as pd

WAIT_DEADLINE = 90   # 最长等 90 秒拿首批数据
WAIT_BATCH   = 5      # wait_update 单次最长阻塞 5 秒


def main():
    import argparse
    parser = argparse.ArgumentParser(description="批量拉取主力合约 tick")
    parser.add_argument("--date", type=str, default=date.today().isoformat(),
                        help="目标日期 (默认今天，格式 YYYY-MM-DD)")
    args = parser.parse_args()

    target_date = args.date
    archive_dir = Path(__file__).resolve().parent.parent / "data" / "archive" / target_date
    archive_dir.mkdir(parents=True, exist_ok=True)

    user, pwd = load_tqsdk_config()
    api = TqApi(auth=TqAuth(user, pwd))

    # 1) 一次性订阅所有主力合约 tick
    print(f"[{time.strftime('%H:%M:%S')}] 订阅 {len(PRODUCTS)} 个主力合约 tick ...")
    tick_handles = {}
    for name, (exchange, code) in PRODUCTS.items():
        symbol = f"KQ.m@{exchange}.{code}"
        try:
            tick_handles[name] = (symbol, api.get_tick_serial(symbol, data_length=8000))
        except Exception as e:
            print(f"  [WARN] {name} 订阅失败: {e}")

    # 2) 等所有数据就绪
    print(f"[{time.strftime('%H:%M:%S')}] 等待行情就绪 ...")
    deadline = time.time() + WAIT_DEADLINE
    last_progress = 0
    while time.time() < deadline:
        api.wait_update(deadline=time.time() + WAIT_BATCH)
        ready = sum(1 for h in tick_handles.values() if len(h[1]) > 0)
        if ready > last_progress:
            print(f"  [{time.strftime('%H:%M:%S')}] 就绪 {ready}/{len(tick_handles)}")
            last_progress = ready
        if ready == len(tick_handles):
            break
    print(f"[{time.strftime('%H:%M:%S')}] 等数据结束 (or 到达 deadline)")

    # 3) 逐个导出
    print(f"\n[{time.strftime('%H:%M:%S')}] 开始导出 parquet ...")
    summary = []
    for name, (symbol, ticks) in tick_handles.items():
        try:
            df = ticks.copy()
            if "datetime" in df.columns:
                df["datetime"] = pd.to_datetime(df["datetime"])
            exchange, code = PRODUCTS[name]
            out = archive_dir / f"{name}_{exchange}.{code}.parquet"
            df.to_parquet(out, index=False)
            if "datetime" in df.columns and len(df) > 0:
                tmin, tmax = df["datetime"].min(), df["datetime"].max()
                rng = f"{tmin} -> {tmax}"
            else:
                rng = "EMPTY"
            print(f"  [OK] {name:<6} {exchange}.{code:<6} {len(df):>5d} ticks  {rng}")
            summary.append({"name": name, "code": f"{exchange}.{code}", "rows": len(df)})
        except Exception as e:
            print(f"  [ERR] {name}: {e}")
            exchange, code = PRODUCTS[name]
            summary.append({"name": name, "code": f"{exchange}.{code}", "rows": 0, "err": str(e)[:60]})

    # 4) 写 manifest
    manifest = {
        "date": target_date,
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "method": "TqSdk.get_tick_serial (batch)",
        "product_count": len([s for s in summary if s.get("rows", 0) > 0]),
        "total_rows": sum(s.get("rows", 0) for s in summary),
        "products": summary,
    }
    (archive_dir / "_manifest_get_tick_serial.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2)
    )
    print(f"\n[{time.strftime('%H:%M:%S')}] 写入 {manifest['product_count']} 个品种, "
          f"总计 {manifest['total_rows']} ticks")
    api.close()


if __name__ == "__main__":
    main()
