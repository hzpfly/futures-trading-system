"""
Tick collector with DuckDB backend.

Refactored from triple_screen/fetch_tick_data.py.
Same TqSdk logic, but writes to DuckDB instead of Parquet files.

用法:
  python -m core.collector               # 交互模式
  python -m core.collector --check        # 检查连通性
  python -m core.collector --duration 60  # 采集指定分钟数
  python -m core.collector --session day  # 日盘
  python -m core.collector --session night # 夜盘
"""
import os
import sys
import time
import signal
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List
from collections import defaultdict

import pandas as pd

# Add repo root to path
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from core.database import DatabaseManager

# ============================================================
# 配置
# ============================================================

PRODUCTS = {
    # 郑商所 CZCE
    "棉花": ("CZCE", "CF"), "白糖": ("CZCE", "SR"), "PTA": ("CZCE", "TA"),
    "甲醇": ("CZCE", "MA"), "菜粕": ("CZCE", "RM"), "菜油": ("CZCE", "OI"),
    "玻璃": ("CZCE", "FG"), "纯碱": ("CZCE", "SA"), "尿素": ("CZCE", "UR"),
    "短纤": ("CZCE", "PF"), "棉纱": ("CZCE", "CY"), "硅铁": ("CZCE", "SF"),
    "锰硅": ("CZCE", "SM"), "苹果": ("CZCE", "AP"), "花生": ("CZCE", "PK"),
    "红枣": ("CZCE", "CJ"),
    # 大商所 DCE
    "铁矿石": ("DCE", "i"), "玉米": ("DCE", "c"), "豆粕": ("DCE", "m"),
    "豆油": ("DCE", "y"), "棕榈油": ("DCE", "p"), "豆一": ("DCE", "a"),
    "豆二": ("DCE", "b"), "聚乙烯": ("DCE", "l"), "PVC": ("DCE", "v"),
    "聚丙烯": ("DCE", "pp"), "焦炭": ("DCE", "j"), "焦煤": ("DCE", "jm"),
    "乙二醇": ("DCE", "eg"), "苯乙烯": ("DCE", "eb"), "液化气": ("DCE", "pg"),
    "生猪": ("DCE", "lh"), "鸡蛋": ("DCE", "jd"), "玉米淀粉": ("DCE", "cs"),
    # 上期所 SHFE
    "螺纹钢": ("SHFE", "rb"), "热卷": ("SHFE", "hc"), "沪铜": ("SHFE", "cu"),
    "沪铝": ("SHFE", "al"), "沪锌": ("SHFE", "zn"), "沪铅": ("SHFE", "pb"),
    "沪镍": ("SHFE", "ni"), "沪锡": ("SHFE", "sn"), "黄金": ("SHFE", "au"),
    "白银": ("SHFE", "ag"), "橡胶": ("SHFE", "ru"), "纸浆": ("SHFE", "sp"),
    "沥青": ("SHFE", "bu"), "燃料油": ("SHFE", "fu"), "不锈钢": ("SHFE", "ss"),
    # 能源中心 INE
    "原油": ("INE", "sc"), "20号胶": ("INE", "nr"), "低硫燃油": ("INE", "lu"),
    "国际铜": ("INE", "bc"),
    # 中金所 CFFEX
    "沪深300": ("CFFEX", "IF"), "中证500": ("CFFEX", "IC"),
    "上证50": ("CFFEX", "IH"), "中证1000": ("CFFEX", "IM"),
    "10年国债": ("CFFEX", "T"), "5年国债": ("CFFEX", "TF"),
    "2年国债": ("CFFEX", "TS"), "30年国债": ("CFFEX", "TL"),
}

FLUSH_INTERVAL = 60
CONNECT_TIMEOUT = 15

_shutdown_requested = False


def load_tqsdk_config() -> tuple:
    config_path = Path.home() / ".futures_config.toml"
    if not config_path.exists():
        print(f"[ERROR] 配置文件不存在: {config_path}")
        sys.exit(1)
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    with open(config_path, "rb") as f:
        cfg = tomllib.load(f)
    tq = cfg.get("tqsdk", {})
    user, pwd = tq.get("username", ""), tq.get("password", "")
    if not user or not pwd:
        print("[ERROR] TqSdk 账号密码未配置")
        sys.exit(1)
    return user, pwd


def make_kqm_symbol(exchange: str, product: str) -> str:
    return f"KQ.m@{exchange}.{product}"


class TickCollector:
    """TqSdk → DuckDB tick collector."""

    def __init__(self, duration_minutes: int = 0, session: str = "auto"):
        self.username, self.password = load_tqsdk_config()
        self.duration = duration_minutes
        self.session = session
        self.api = None

        # DuckDB layer
        self.db = DatabaseManager()

        # Buffers: {product: [tick dicts]}
        self.buffers: Dict[str, List[dict]] = defaultdict(list)

        # Contract maps
        self.contracts: Dict[str, str] = {}
        self.product_info: Dict[str, tuple] = {}

        # TqSdk tick references
        self.tick_refs: Dict[str, pd.DataFrame] = {}

        self.start_time = None
        self.last_flush = None
        self.tick_counts: Dict[str, int] = defaultdict(int)
        self.total_written = 0

    def _resolve_session(self) -> str:
        if self.session in ("day", "night"):
            return self.session
        hour = datetime.now().hour
        return "day" if 8 <= hour < 18 else "night"

    # ── Connection ──

    def connect(self) -> bool:
        from tqsdk import TqApi, TqAuth
        import threading

        connected = [False]
        error_msg = [None]

        def _do_connect():
            try:
                self.api = TqApi(auth=TqAuth(self.username, self.password))
                connected[0] = True
            except Exception as e:
                error_msg[0] = str(e)

        thread = threading.Thread(target=_do_connect, daemon=True)
        thread.start()
        thread.join(timeout=CONNECT_TIMEOUT)

        if not connected[0]:
            print(f"[INFO] TqSdk 连接失败: {error_msg[0] or '连接超时'}")
            return False
        print("[OK] TqSdk 已连接")
        return True

    def detect_main_contracts(self) -> bool:
        print("\n[检测] 识别主力合约...")
        for name, (exchange, code) in PRODUCTS.items():
            self.product_info[name] = (exchange, code)
            kqm = make_kqm_symbol(exchange, code)
            try:
                quote = self.api.get_quote(kqm)
                self.api.wait_update(deadline=time.time() + 5)
                underlying = quote.get("underlying_symbol", "")
                self.contracts[name] = underlying if underlying else kqm
                print(f"  {name}: {self.contracts[name]}")
            except Exception as e:
                self.contracts[name] = kqm
                print(f"  {name}: fallback ({e})")
        return len(self.contracts) > 0

    def subscribe_all(self):
        print("\n[订阅] 开始接收 tick 数据...")
        for name, contract in self.contracts.items():
            try:
                self.tick_refs[name] = self.api.get_tick_serial(contract)
                print(f"  {name} ({contract}): 已订阅")
            except Exception as e:
                print(f"  {name} ({contract}): 失败 ({e})")

    # ── Main loop ──

    def run(self):
        if not self.connect():
            return
        if not self.detect_main_contracts():
            self.api.close()
            return
        self.subscribe_all()

        self.start_time = time.time()
        self.last_flush = time.time()

        print("\n" + "=" * 50)
        print(f"[运行] Tick → DuckDB 采集")
        print(f"       DB: {self.db.db_path}")
        print(f"       Session: {self._resolve_session()}")
        print("=" * 50 + "\n")

        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

        last_stats = time.time()

        try:
            while not _shutdown_requested:
                try:
                    self.api.wait_update(deadline=time.time() + 1)
                except Exception:
                    time.sleep(1)
                    continue

                for name, tick_df in self.tick_refs.items():
                    try:
                        if self.api.is_changing(tick_df):
                            tick = tick_df.iloc[-1].to_dict()
                            tick["product"] = name
                            tick["contract"] = self.contracts[name]
                            self.buffers[name].append(tick)
                            self.tick_counts[name] += 1
                    except Exception:
                        pass

                if time.time() - self.last_flush >= FLUSH_INTERVAL:
                    self._flush_all()
                    self.last_flush = time.time()

                if time.time() - last_stats >= 60:
                    self._print_stats()
                    last_stats = time.time()

                if self.duration > 0:
                    elapsed = (time.time() - self.start_time) / 60
                    if elapsed >= self.duration:
                        print(f"\n[完成] 已达 {self.duration} 分钟")
                        break

        except KeyboardInterrupt:
            print("\n[INFO] 收到中断")
        finally:
            self._shutdown()

    # ── Flush: write buffer to DuckDB ──

    def _flush_all(self):
        """Write buffered ticks to DuckDB (single INSERT)."""
        session_label = self._resolve_session()
        total = 0
        for name, ticks in list(self.buffers.items()):
            if not ticks:
                continue
            try:
                df = pd.DataFrame(ticks)
                if df.empty:
                    self.buffers[name] = []
                    continue

                # Normalize datetime
                if "datetime" in df.columns:
                    df["datetime"] = pd.to_datetime(df["datetime"], unit="ns")

                exchange, code = self.product_info.get(name, ("UNKNOWN", "UNKNOWN"))

                # Select and rename columns for DuckDB schema
                out = pd.DataFrame()
                out["datetime"] = df["datetime"]
                out["product"] = name
                out["exchange"] = exchange
                out["symbol"] = code
                out["session"] = session_label
                out["last_price"] = df.get("last_price", 0)
                out["volume"] = df.get("volume", 0)
                out["open_interest"] = df.get("open_interest", 0)
                out["bid_price1"] = df.get("bid_price1", 0)
                out["bid_volume1"] = df.get("bid_volume1", 0)
                out["ask_price1"] = df.get("ask_price1", 0)
                out["ask_volume1"] = df.get("ask_volume1", 0)

                # Filter bad data
                out = out[out["last_price"] > 0]

                if not out.empty:
                    self.db.insert_ticks(out)
                    total += len(out)

                self.buffers[name] = []
            except Exception as e:
                print(f"  [ERROR] flush {name}: {e}")

        if total > 0:
            self.total_written += total
            print(f"  [FLUSH] {total} ticks (累计 {self.total_written})")

    def _print_stats(self):
        elapsed = (time.time() - self.start_time) / 60
        total = sum(self.tick_counts.values())
        parts = [f"{n}:{c}" for n, c in sorted(self.tick_counts.items()) if c > 0]
        top = parts[:6]
        if len(parts) > 6:
            top.append(f"...+{len(parts)-6}")
        print(f"  [{elapsed:.0f}min] {total} ticks | {', '.join(top)}")

    def _on_signal(self, signum, frame):
        global _shutdown_requested
        print(f"\n[INFO] 退出信号 ({signum})")
        _shutdown_requested = True

    def _shutdown(self):
        print("\n[清理] 刷新剩余数据到 DuckDB...")
        try:
            self._flush_all()
        except Exception as e:
            print(f"  [WARN] {e}")

        if self.api:
            try:
                self.api.close()
                time.sleep(0.5)
            except Exception:
                pass

        elapsed = (time.time() - self.start_time) / 60 if self.start_time else 0
        print(f"\n[完成] {self.total_written} ticks → DuckDB ({elapsed:.1f}min)")
        print(f"       DB: {self.db.db_path}")
        self.db.close()


# ── Check mode ──

def check_mode():
    from tqsdk import TqApi, TqAuth
    import threading

    user, pwd = load_tqsdk_config()
    print("[检测] 连接 TqSdk...")

    api_container = [None]
    def _try():
        try:
            api_container[0] = TqApi(auth=TqAuth(user, pwd))
        except Exception as e:
            print(f"连接失败: {e}")

    thread = threading.Thread(target=_try, daemon=True)
    thread.start()
    thread.join(timeout=CONNECT_TIMEOUT)

    api = api_container[0]
    if api is None:
        print("[失败] 无法连接")
        return

    print("[OK] 已连接\n")
    for name, (exchange, code) in PRODUCTS.items():
        kqm = make_kqm_symbol(exchange, code)
        try:
            q = api.get_quote(kqm)
            api.wait_update(deadline=time.time() + 5)
            u = q.get("underlying_symbol", "?")
            print(f"  {name:6s} | {kqm:18s} → {u:16s} | {q.get('last_price',0):>8.1f}")
        except Exception as e:
            print(f"  {name:6s} | {kqm:18s} | fail: {e}")

    api.close()
    print("\n[完成] 检查结束")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Tick → DuckDB 采集器")
    parser.add_argument("--check", action="store_true", help="仅检查连接")
    parser.add_argument("--duration", type=int, default=0, help="采集时长(分钟)")
    parser.add_argument("--session", type=str, default="auto",
                        choices=["day", "night", "auto"])
    args = parser.parse_args()

    if args.check:
        check_mode()
    else:
        collector = TickCollector(
            duration_minutes=args.duration,
            session=args.session,
        )
        collector.run()


if __name__ == "__main__":
    main()
