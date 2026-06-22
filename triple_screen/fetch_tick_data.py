#!/usr/bin/env python3
"""
每日主力合约 Tick 数据采集器
============================
通过 TqSdk 实时采集主力合约的逐笔 tick 数据，按品种+日期保存为 Parquet 文件。
支持自动识别主力合约、定时刷新缓冲区、断线重连。

 用法:
  python fetch_tick_data.py               # 交互模式，采集直到手动停止
  python fetch_tick_data.py --check        # 检查连通性和当前主力合约
  python fetch_tick_data.py --duration 60  # 采集指定分钟数后自动退出
  python fetch_tick_data.py --session day  # 日盘 (文件名: 2026-06-22_day.parquet)
  python fetch_tick_data.py --session night # 夜盘 (文件名: 2026-06-22_night.parquet)
  python fetch_tick_data.py --session auto # 自动识别 (8-17点→day, 20-3点→night)

依赖:
  tqsdk, pandas, pyarrow (parquet 写入)
"""

import os, sys, time, signal, json
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional, Dict, List
from collections import defaultdict

import numpy as np
import pandas as pd

# ============================================================
# 配置
# ============================================================

# 监控品种：{显示名: (交易所ID, 品种代码)}
# 覆盖五大期货交易所主力合约（共 50+ 品种）
PRODUCTS = {
    # ─── 郑商所 CZCE ───
    "棉花":     ("CZCE", "CF"),
    "白糖":     ("CZCE", "SR"),
    "PTA":      ("CZCE", "TA"),
    "甲醇":     ("CZCE", "MA"),
    "菜粕":     ("CZCE", "RM"),
    "菜油":     ("CZCE", "OI"),
    "玻璃":     ("CZCE", "FG"),
    "纯碱":     ("CZCE", "SA"),
    "尿素":     ("CZCE", "UR"),
    "短纤":     ("CZCE", "PF"),
    "棉纱":     ("CZCE", "CY"),
    "硅铁":     ("CZCE", "SF"),
    "锰硅":     ("CZCE", "SM"),
    "苹果":     ("CZCE", "AP"),
    "花生":     ("CZCE", "PK"),
    "红枣":     ("CZCE", "CJ"),
    # ─── 大商所 DCE ───
    "铁矿石":   ("DCE",  "i"),
    "玉米":     ("DCE",  "c"),
    "豆粕":     ("DCE",  "m"),
    "豆油":     ("DCE",  "y"),
    "棕榈油":   ("DCE",  "p"),
    "豆一":     ("DCE",  "a"),
    "豆二":     ("DCE",  "b"),
    "聚乙烯":   ("DCE",  "l"),
    "PVC":      ("DCE",  "v"),
    "聚丙烯":   ("DCE",  "pp"),
    "焦炭":     ("DCE",  "j"),
    "焦煤":     ("DCE",  "jm"),
    "乙二醇":   ("DCE",  "eg"),
    "苯乙烯":   ("DCE",  "eb"),
    "液化气":   ("DCE",  "pg"),
    "生猪":     ("DCE",  "lh"),
    "鸡蛋":     ("DCE",  "jd"),
    "玉米淀粉": ("DCE",  "cs"),
    # ─── 上期所 SHFE ───
    "螺纹钢":   ("SHFE", "rb"),
    "热卷":     ("SHFE", "hc"),
    "沪铜":     ("SHFE", "cu"),
    "沪铝":     ("SHFE", "al"),
    "沪锌":     ("SHFE", "zn"),
    "沪铅":     ("SHFE", "pb"),
    "沪镍":     ("SHFE", "ni"),
    "沪锡":     ("SHFE", "sn"),
    "黄金":     ("SHFE", "au"),
    "白银":     ("SHFE", "ag"),
    "橡胶":     ("SHFE", "ru"),
    "纸浆":     ("SHFE", "sp"),
    "沥青":     ("SHFE", "bu"),
    "燃料油":   ("SHFE", "fu"),
    "不锈钢":   ("SHFE", "ss"),
    # ─── 能源中心 INE ───
    "原油":     ("INE",  "sc"),
    "20号胶":   ("INE",  "nr"),
    "低硫燃油": ("INE",  "lu"),
    "国际铜":   ("INE",  "bc"),
    # ─── 中金所 CFFEX ───
    "沪深300":  ("CFFEX", "IF"),
    "中证500":  ("CFFEX", "IC"),
    "上证50":   ("CFFEX", "IH"),
    "中证1000": ("CFFEX", "IM"),
    "10年国债": ("CFFEX", "T"),
    "5年国债":  ("CFFEX", "TF"),
    "2年国债":  ("CFFEX", "TS"),
    "30年国债": ("CFFEX", "TL"),
}

# 输出目录
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "ticks"

# 缓冲区刷新间隔（秒）
FLUSH_INTERVAL = 60

# TqSdk 连接超时（秒）
CONNECT_TIMEOUT = 15

# session 元信息文件名
META_FILE = "session_meta.json"

# 优雅退出标志
_shutdown_requested = False


# ============================================================
# 工具函数
# ============================================================

def load_tqsdk_config() -> tuple:
    """从 ~/.futures_config.toml 加载 TqSdk 凭据"""
    config_path = Path.home() / ".futures_config.toml"
    if not config_path.exists():
        print(f"[ERROR] 配置文件不存在: {config_path}")
        print("        请参考 triple_screen/config.toml.example 创建 ~/.futures_config.toml")
        sys.exit(1)

    try:
        import tomllib
    except ImportError:
        import tomli as tomllib

    with open(config_path, "rb") as f:
        cfg = tomllib.load(f)

    tq = cfg.get("tqsdk", {})
    user = tq.get("username", "")
    pwd = tq.get("password", "")
    if not user or not pwd:
        print("[ERROR] TqSdk 账号密码未配置，请编辑 ~/.futures_config.toml")
        sys.exit(1)
    return user, pwd


def make_kqm_symbol(exchange: str, product: str) -> str:
    """构造主力连续合约代码，如 KQ.m@CZCE.CF"""
    return f"KQ.m@{exchange}.{product}"


def nan_to_none(val):
    """NaN 转 None (JSON 序列化用)"""
    if isinstance(val, float) and np.isnan(val):
        return None
    return val


# ============================================================
# Tick 采集器
# ============================================================

class TickCollector:
    """主力合约 Tick 数据实时采集器"""

    def __init__(self, duration_minutes: int = 0, session: str = "auto"):
        self.username, self.password = load_tqsdk_config()
        self.duration = duration_minutes
        self.api = None
        self.session = session  # "day", "night", or "auto"

        # 缓冲区: {product_name: [list of tick dicts]}
        self.buffers: Dict[str, List[dict]] = defaultdict(list)

        # 合约映射: {product_name: actual_contract_code}
        self.contracts: Dict[str, str] = {}

        # 品种信息: {product_name: (exchange, product_code)}
        self.product_info: Dict[str, tuple] = {}

        # tick_serial 引用
        self.tick_refs: Dict[str, pd.DataFrame] = {}

        self.start_time = None
        self.last_flush = None
        self.tick_counts: Dict[str, int] = defaultdict(int)

        DATA_DIR.mkdir(parents=True, exist_ok=True)

    # -------- 连接 --------

    def connect(self) -> bool:
        """连接 TqSdk 服务器。成功返回 True"""
        from tqsdk import TqApi, TqAuth
        import threading

        connected = [False]
        error_msg = [None]

        def _do_connect():
            try:
                api = TqApi(auth=TqAuth(self.username, self.password))
                self.api = api
                connected[0] = True
            except Exception as e:
                error_msg[0] = str(e)

        thread = threading.Thread(target=_do_connect, daemon=True)
        thread.start()
        thread.join(timeout=CONNECT_TIMEOUT)

        if not connected[0]:
            msg = error_msg[0] or "连接超时"
            print(f"[INFO] TqSdk 连接失败: {msg}")
            print("       当前可能非交易时段，请在交易时段运行此脚本。")
            return False

        print(f"[OK] TqSdk 已连接")
        return True

    # -------- 主力合约识别 --------

    def detect_main_contracts(self) -> bool:
        """通过 KQ.m@ 判断每个品种的主力合约"""
        print("\n[检测] 识别主力合约...")
        found_any = False

        for name, (exchange, product_code) in PRODUCTS.items():
            self.product_info[name] = (exchange, product_code)
            kqm = make_kqm_symbol(exchange, product_code)
            try:
                quote = self.api.get_quote(kqm)
                # 需要一次 wait_update 让数据就绪
                self.api.wait_update(deadline=time.time() + 5)
                underlying = quote.get("underlying_symbol", "")
                if underlying:
                    self.contracts[name] = underlying
                    print(f"  {name}: {underlying}")
                    found_any = True
                else:
                    # fallback: try KQ.m@ directly for tick subscription
                    self.contracts[name] = kqm
                    print(f"  {name}: {kqm} (无具体合约，使用主连)")
                    found_any = True
            except Exception as e:
                print(f"  {name}: 查询失败 ({e})")
                self.contracts[name] = kqm  # fallback

        if not found_any:
            print("[WARN] 未找到任何主力合约")
            return False
        return True

    # -------- 订阅 --------

    def subscribe_all(self):
        """订阅所有主力合约的 tick 序列"""
        print("\n[订阅] 开始接收 tick 数据...")
        for name, contract in self.contracts.items():
            try:
                tick_df = self.api.get_tick_serial(contract)
                self.tick_refs[name] = tick_df
                print(f"  {name} ({contract}): 已订阅")
            except Exception as e:
                print(f"  {name} ({contract}): 订阅失败 ({e})")

    # -------- 主循环 --------

    def run(self):
        """主采集循环"""
        if not self.connect():
            return

        if not self.detect_main_contracts():
            self.api.close()
            return

        self.subscribe_all()

        self.start_time = time.time()
        self.last_flush = time.time()

        print("\n" + "=" * 50)
        print("[运行] 开始采集 tick 数据")
        print(f"       缓冲区刷新间隔: {FLUSH_INTERVAL}s")
        if self.duration > 0:
            print(f"       计划运行: {self.duration} 分钟")
        else:
            print(f"       按 Ctrl+C 停止")
        print("=" * 50 + "\n")

        # 注册信号处理
        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

        # 上次打印统计的时间
        last_stats_time = time.time()

        try:
            while not _shutdown_requested:
                # 等待行情更新（带1秒超时以便检查退出标志）
                try:
                    self.api.wait_update(deadline=time.time() + 1)
                except Exception as e:
                    print(f"\n[WARN] wait_update 异常: {e}")
                    time.sleep(1)
                    continue

                # 检查每个品种的 tick 更新
                for name, tick_df in self.tick_refs.items():
                    try:
                        if self.api.is_changing(tick_df):
                            new_tick = tick_df.iloc[-1].to_dict()
                            new_tick["product"] = name
                            new_tick["contract"] = self.contracts[name]
                            self.buffers[name].append(new_tick)
                            self.tick_counts[name] += 1
                    except Exception:
                        pass  # 单个品种异常不影响其他

                # 定时刷新缓冲区
                if time.time() - self.last_flush >= FLUSH_INTERVAL:
                    self._flush_all()
                    self.last_flush = time.time()

                # 定时打印统计
                if time.time() - last_stats_time >= 60:
                    self._print_stats()
                    last_stats_time = time.time()

                # 检查运行时长
                if self.duration > 0:
                    elapsed = (time.time() - self.start_time) / 60
                    if elapsed >= self.duration:
                        print(f"\n[完成] 已达到设定的 {self.duration} 分钟采集时长")
                        break

        except KeyboardInterrupt:
            print("\n[INFO] 收到中断信号")

        finally:
            self._shutdown()

    def _tick_path(self, name: str, date_str: str) -> Path:
        """构建 tick 数据文件路径: data/ticks/{品种}/{交易所}.{代码}/{日期}_{session}.parquet"""
        exchange, product_code = self.product_info.get(name, ("UNKNOWN", "UNKNOWN"))
        subdir = DATA_DIR / name / f"{exchange}.{product_code}"
        subdir.mkdir(parents=True, exist_ok=True)
        session_label = self._resolve_session()
        return subdir / f"{date_str}_{session_label}.parquet"

    def _resolve_session(self) -> str:
        """解析当前时段标签: day / night"""
        if self.session in ("day", "night"):
            return self.session
        # auto: 根据当前时间判断
        hour = datetime.now().hour
        if 8 <= hour < 18:
            return "day"
        else:
            return "night"

    def _flush_all(self):
        """将所有缓冲区写入磁盘（Parquet 优先，无 pyarrow 时用 CSV.gz）"""
        date_str = date.today().isoformat()
        total_written = 0
        for name, ticks in list(self.buffers.items()):
            if not ticks:
                continue
            try:
                df = pd.DataFrame(ticks)
                if df.empty:
                    self.buffers[name] = []
                    continue
                if "datetime" in df.columns:
                    df["datetime"] = pd.to_datetime(df["datetime"], unit="ns")
                # 尝试 Parquet，失败则用 CSV.gz
                try:
                    fpath = self._tick_path(name, date_str)
                    df.to_parquet(fpath, index=False)
                except ImportError:
                    fpath = self._tick_path(name, date_str).with_suffix(".csv.gz")
                    df.to_csv(fpath, index=False, compression="gzip")
                total_written += len(ticks)
                self.buffers[name] = []
            except Exception as e:
                print(f"  [ERROR] 写入 {name} 失败: {e}")
        if total_written > 0:
            print(f"  [FLUSH] 写入 {total_written} 条 tick")

    def _print_stats(self):
        """打印采集统计"""
        elapsed = (time.time() - self.start_time) / 60
        total = sum(self.tick_counts.values())
        parts = [f"{n}:{c}" for n, c in self.tick_counts.items() if c > 0]
        detail = ", ".join(parts) if parts else "暂无数据"
        print(f"  [STATS] 运行 {elapsed:.1f}min | 累计 {total} ticks | {detail}")

    def _on_signal(self, signum, frame):
        """信号处理"""
        global _shutdown_requested
        print(f"\n[INFO] 收到退出信号 ({signum})")
        _shutdown_requested = True

    def _shutdown(self):
        """清理资源"""
        print("\n[清理] 正在保存剩余数据...")
        try:
            self._flush_all()
        except Exception as e:
            print(f"  [WARN] 保存数据时出错: {e}")
        try:
            self._save_summary()
        except Exception as e:
            print(f"  [WARN] 保存汇总时出错: {e}")

        if self.api:
            try:
                self.api.close()
                time.sleep(0.5)  # 让 TqSdk 清理异步任务
            except Exception:
                pass
            print("[OK] TqSdk 已断开")

        total = sum(self.tick_counts.values())
        elapsed = (time.time() - self.start_time) / 60 if self.start_time else 0
        if total == 0:
            print(f"\n[INFO] 采集结束。当前非交易时段，未收到 tick 数据。")
            print(f"       请在交易时段运行此脚本以采集实时 tick。")
        else:
            print(f"\n[完成] 采集结束。总 tick 数: {total}, 运行 {elapsed:.1f} 分钟")
        print(f"       数据目录: {DATA_DIR}")

    def _save_summary(self):
        """保存本次采集的汇总信息（存放在全局 data/ticks 目录下）"""
        date_str = date.today().isoformat()
        total = sum(self.tick_counts.values())
        summary = {
            "date": date_str,
            "total_ticks": int(total),
            "duration_seconds": time.time() - self.start_time if self.start_time else 0,
            "contracts": self.contracts,
            "tick_counts": {k: int(v) for k, v in self.tick_counts.items()},
        }
        summary["duration_seconds"] = int(summary["duration_seconds"])
        # 全局汇总文件
        fpath = DATA_DIR / f"_summary_{date_str}.json"
        with open(fpath, "w") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"  汇总已保存: {fpath.name}")
        # 同时写一份到每个品种的目录下
        for name in self.tick_counts:
            if self.tick_counts[name] > 0 and name in self.product_info:
                subdir = self._tick_path(name, date_str).parent
                with open(subdir / f"_summary_{date_str}.json", "w") as f:
                    json.dump(summary, f, ensure_ascii=False, indent=2)


# ============================================================
# --check 模式：仅测试连接
# ============================================================

def check_mode():
    """检查 TqSdk 连通性和主力合约"""
    from tqsdk import TqApi, TqAuth
    import threading

    user, pwd = load_tqsdk_config()
    print(f"[检测] 正在连接 TqSdk 服务器...")

    api_container = [None]
    error_msg = [None]

    def _try_connect():
        try:
            api_container[0] = TqApi(auth=TqAuth(user, pwd))
        except Exception as e:
            error_msg[0] = str(e)

    thread = threading.Thread(target=_try_connect, daemon=True)
    thread.start()
    thread.join(timeout=CONNECT_TIMEOUT)

    api = api_container[0]
    if api is None:
        msg = error_msg[0] or "连接超时"
        print(f"[失败] 无法连接 TqSdk: {msg}")
        print("       可能原因: 非交易时段 / 网络问题 / 账号密码错误")
        return

    print("[OK] TqSdk 连接成功！\n")

    print("主力合约信息:")
    print("-" * 50)
    for name, (exchange, code) in PRODUCTS.items():
        kqm = make_kqm_symbol(exchange, code)
        try:
            quote = api.get_quote(kqm)
            api.wait_update(deadline=time.time() + 5)
            underlying = quote.get("underlying_symbol", "未知")
            last_price = quote.get("last_price", 0)
            volume = quote.get("volume", 0)
            print(f"  {name:6s} | {kqm:18s} → {underlying:16s} | 最新价: {last_price:>8.1f} | 成交量: {volume:>10.0f}")
        except Exception as e:
            print(f"  {name:6s} | {kqm:18s} | 查询失败: {e}")

    api.close()
    print("\n[完成] 检查结束。")


# ============================================================
# --backtest 模式：使用回测引擎采集历史 tick
# ============================================================

def backtest_mode(target_date: date, products: Optional[List[str]] = None):
    """
    使用 TqSdk TqBacktest 引擎回放指定交易日，采集 tick 数据。

    Args:
        target_date: 目标回放日期
        products: 要采集的品种列表 (key from PRODUCTS)，None=全部
    """
    from tqsdk import TqApi, TqAuth, TqBacktest, BacktestFinished

    user, pwd = load_tqsdk_config()
    date_str = target_date.isoformat()

    # 确定品种列表
    if products is None:
        products = list(PRODUCTS.keys())

    # 回测时间范围：目标日期一天
    end_dt = date(target_date.year, target_date.month, min(target_date.day + 1, 28))

    print(f"[回放] 目标日期: {date_str}")
    print(f"[回放] 品种: {', '.join(products)}")
    print()

    # 第一步：用短回测识别目标日期的主力合约
    print("[步骤1] 识别主力合约...")
    contract_map = {}

    try:
        api = TqApi(
            backtest=TqBacktest(start_dt=target_date, end_dt=end_dt),
            auth=TqAuth(user, pwd)
        )

        for name in products:
            if name in PRODUCTS:
                exchange, code = PRODUCTS[name]
                kqm = make_kqm_symbol(exchange, code)
                try:
                    q = api.get_quote(kqm)
                    api.wait_update()
                    # 等待 underlying_symbol 出现
                    start = time.time()
                    while time.time() - start < 10:
                        api.wait_update()
                        ul = q.get("underlying_symbol", "")
                        if ul and ul.startswith(f"{exchange}."):
                            contract_map[name] = ul
                            print(f"  {name}: {kqm} → {ul}")
                            break
                    if name not in contract_map:
                        contract_map[name] = kqm  # fallback
                        print(f"  {name}: {kqm} (无法解析主力合约)")
                except Exception as e:
                    print(f"  {name}: 查询失败 ({e})")
                    contract_map[name] = kqm

        api.close()
    except Exception as e:
        print(f"[ERROR] 获取主力合约失败: {e}")
        return

    if not contract_map:
        print("[ERROR] 未找到任何合约")
        return

    # 第二步：用回测引擎重放，采集 tick
    print(f"\n[步骤2] 开始回放采集 tick ({date_str})...")

    all_ticks: Dict[str, List[dict]] = {name: [] for name in contract_map}
    tick_serials: Dict[str, pd.DataFrame] = {}
    total_ticks = 0

    try:
        api = TqApi(
            backtest=TqBacktest(start_dt=target_date, end_dt=end_dt),
            auth=TqAuth(user, pwd)
        )

        # 订阅所有合约的 tick 序列
        for name, contract in contract_map.items():
            try:
                tick_df = api.get_tick_serial(contract)
                tick_serials[name] = tick_df
                print(f"  {name} ({contract}): 已订阅 tick")
            except Exception as e:
                print(f"  {name} ({contract}): tick订阅失败 ({e})")

        if not tick_serials:
            print("[ERROR] 没有成功订阅任何 tick")
            api.close()
            return

        print(f"\n[采集] 逐tick回放中...")

        try:
            while True:
                api.wait_update()

                for name, tick_df in tick_serials.items():
                    if api.is_changing(tick_df):
                        new_tick = tick_df.iloc[-1].to_dict()
                        new_tick["product"] = name
                        new_tick["contract"] = contract_map[name]
                        all_ticks[name].append(new_tick)
                        total_ticks += 1

        except BacktestFinished:
            pass

        api.close()

    except Exception as e:
        print(f"[ERROR] 回放过程出错: {e}")
        import traceback
        traceback.print_exc()
        return

    # 第三步：保存数据
    print(f"\n[保存] 写入数据文件...")

    saved_files = []
    grand_total = 0

    for name, ticks in all_ticks.items():
        if not ticks:
            print(f"  {name}: 无数据 (市场可能休市)")
            continue

        df = pd.DataFrame(ticks)

        if "datetime" in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"], unit="ns")

        # 子目录结构: data/ticks/{品种}/{交易所}.{代码}/{日期}.parquet
        exchange, product_code = PRODUCTS.get(name, ("UNKNOWN", "UNKNOWN"))
        subdir = DATA_DIR / name / f"{exchange}.{product_code}"
        subdir.mkdir(parents=True, exist_ok=True)
        fpath = subdir / f"{date_str}_day.parquet"
        df.to_parquet(fpath, index=False)
        saved_files.append(str(fpath))
        grand_total += len(ticks)
        print(f"  {name}: {len(ticks)} ticks → {fpath.relative_to(DATA_DIR)}")

    # 保存汇总
    summary = {
        "date": date_str,
        "mode": "backtest",
        "contracts": contract_map,
        "tick_counts": {k: len(v) for k, v in all_ticks.items()},
        "total_ticks": grand_total,
        "files": saved_files,
    }
    sfpath = DATA_DIR / f"_summary_{date_str}.json"
    with open(sfpath, "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"  汇总: {sfpath.name}")

    print(f"\n[完成] 回放采集结束。总计 {grand_total} ticks, {len(all_ticks)} 品种")
    print(f"       数据目录: {DATA_DIR}")


# ============================================================
# 入口
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="每日主力合约 Tick 数据采集器")
    parser.add_argument("--check", action="store_true", help="仅检查连接和主力合约")
    parser.add_argument("--duration", type=int, default=0,
                        help="采集时长（分钟），0 表示直到手动停止")
    parser.add_argument("--backtest", type=str, default=None,
                        help="使用回测引擎回放指定日期 (YYYY-MM-DD) 并采集 tick")
    parser.add_argument("--session", type=str, default="auto",
                        choices=["day", "night", "auto"],
                        help="时段: day=日盘, night=夜盘, auto=自动识别 (默认auto)")
    args = parser.parse_args()

    if args.check:
        check_mode()
    elif args.backtest:
        bt_date = date.fromisoformat(args.backtest)
        backtest_mode(bt_date)
    else:
        collector = TickCollector(duration_minutes=args.duration, session=args.session)
        collector.run()


if __name__ == "__main__":
    main()
