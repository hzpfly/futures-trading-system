"""
数据加载模块 - 通过 tushare 获取期货日线行情
支持:
  - 主力连续合约行情拉取 (fut_daily)
  - 主力合约映射查询 (fut_mapping)
  - 本地 CSV 缓存（减少 API 调用）
"""
import os
import time
import logging
import hashlib
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


def _get_pro():
    """获取 tushare pro API 实例（懒加载）"""
    import tushare as ts
    from config.settings import TUSHARE_TOKEN
    token = TUSHARE_TOKEN or os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise EnvironmentError(
            "未找到 TUSHARE_TOKEN，请设置环境变量:\n"
            "  export TUSHARE_TOKEN='your_token_here'"
        )
    return ts.pro_api(token)


def _cache_path(key: str, cache_dir: str) -> str:
    h = hashlib.md5(key.encode()).hexdigest()[:8]
    return os.path.join(cache_dir, f"{key.replace('.', '_')}_{h}.csv")


def get_continuous_daily(
    ts_code: str,
    start_date: str,
    end_date: str,
    cache_dir: str = "cache",
    force_refresh: bool = False,
    retry: int = 3,
    sleep_sec: float = 0.35,
) -> pd.DataFrame:
    """
    获取期货主力连续合约日线行情。

    参数:
        ts_code   : 主力连续合约代码，如 'C.DCE'
        start_date: 开始日期 YYYYMMDD
        end_date  : 结束日期 YYYYMMDD
        cache_dir : 本地缓存目录
        force_refresh: 强制刷新忽略缓存

    返回 DataFrame，columns:
        trade_date, open, high, low, close, settle, vol, amount, oi, oi_chg
    索引: DatetimeIndex (升序)
    """
    os.makedirs(cache_dir, exist_ok=True)
    cache_key = f"{ts_code}_{start_date}_{end_date}"
    cpath = _cache_path(cache_key, cache_dir)

    if not force_refresh and os.path.exists(cpath):
        df = pd.read_csv(cpath, parse_dates=["trade_date"], index_col="trade_date")
        logger.debug(f"[缓存命中] {ts_code} rows={len(df)}")
        return df

    pro = _get_pro()
    for attempt in range(retry):
        try:
            df = pro.fut_daily(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                fields="ts_code,trade_date,open,high,low,close,settle,vol,amount,oi,oi_chg",
            )
            break
        except Exception as e:
            logger.warning(f"[重试 {attempt+1}/{retry}] {ts_code}: {e}")
            time.sleep(sleep_sec * (attempt + 1))
    else:
        raise RuntimeError(f"无法获取 {ts_code} 行情数据，已重试 {retry} 次")

    if df is None or df.empty:
        logger.warning(f"[空数据] {ts_code} {start_date}~{end_date}")
        return pd.DataFrame()

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values("trade_date").set_index("trade_date")
    df = df.drop(columns=["ts_code"], errors="ignore")

    # 前向填充（跳过非交易日可能产生的空值）
    df = df.ffill()

    df.to_csv(cpath)
    logger.info(f"[已拉取] {ts_code} rows={len(df)} -> {cpath}")
    time.sleep(sleep_sec)
    return df


def load_all_symbols(
    symbol_configs: dict,
    start_date: str,
    end_date: str,
    cache_dir: str = "cache",
    force_refresh: bool = False,
) -> dict:
    """
    批量加载多品种日线行情。

    参数:
        symbol_configs: 来自 settings.AGRI_FUTURES 的配置字典
        start_date / end_date: 日期范围 YYYYMMDD

    返回 {品种名: DataFrame}
    """
    result = {}
    for name, cfg in symbol_configs.items():
        code = cfg["code"]
        logger.info(f"正在加载 {name} ({code}) ...")
        try:
            df = get_continuous_daily(
                ts_code=code,
                start_date=start_date,
                end_date=end_date,
                cache_dir=cache_dir,
                force_refresh=force_refresh,
            )
            if not df.empty:
                result[name] = df
        except Exception as e:
            logger.error(f"[跳过] {name}: {e}")

    logger.info(f"共加载 {len(result)}/{len(symbol_configs)} 个品种")
    return result


# ── 生成模拟数据（用于无 Token 时测试）─────────────────────────────────
def generate_mock_data(
    symbol: str = "玉米",
    start_date: str = "20200101",
    end_date: str = "20260101",
    seed: int = 42,
) -> pd.DataFrame:
    """
    生成高质量模拟期货日线数据（GBM + 季节性 + 跳跃）。
    当没有 tushare Token 时用于测试和演示。
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start=start_date, end=end_date)
    n = len(dates)

    # 基准价格
    price_map = {
        "玉米": 2600, "豆粕": 3500, "棉花": 15000,
        "白糖": 6000, "菜粕": 3200, "生猪": 20000, "鸡蛋": 5000,
    }
    base = price_map.get(symbol, 3000)

    # GBM + 季节性噪声
    dt = 1 / 252
    mu = 0.03
    sigma = 0.18
    seasonal = 0.08 * np.sin(2 * np.pi * np.arange(n) / 252)
    returns = (mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * rng.standard_normal(n)
    # 随机跳跃（约每季度一次）
    jump_idx = rng.choice(n, size=n // 60, replace=False)
    jumps = np.zeros(n)
    jumps[jump_idx] = rng.normal(0, 0.03, size=len(jump_idx))
    returns += jumps

    close = base * np.exp(np.cumsum(returns) + seasonal)
    daily_range = close * rng.uniform(0.005, 0.025, size=n)
    high = close + daily_range * rng.uniform(0.3, 0.7, size=n)
    low = close - daily_range * rng.uniform(0.3, 0.7, size=n)
    open_ = close * (1 + rng.uniform(-0.01, 0.01, size=n))

    vol = (rng.lognormal(10.5, 0.5, size=n)).astype(int)
    oi = np.maximum(5000, (rng.lognormal(11, 0.3, size=n)).astype(int))

    df = pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "settle": close * (1 + rng.uniform(-0.001, 0.001, size=n)),
        "vol": vol,
        "amount": (close * vol * 10 / 10000).round(2),
        "oi": oi,
        "oi_chg": np.diff(oi, prepend=oi[0]),
    }, index=dates)
    df.index.name = "trade_date"
    df = df.round(2)
    return df
