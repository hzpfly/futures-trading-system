"""
DuckDB database layer for futures tick and kline data.

Single-file embedded database, no server needed.
Columnar storage optimized for time-series analytical queries.
"""
import os
import threading
from contextlib import contextmanager

import duckdb
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(PROJECT_ROOT, "data", "futures.db")

TICK_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS ticks (
    datetime    TIMESTAMP   NOT NULL,
    product     VARCHAR     NOT NULL,   -- 棉花, 豆粕, ...
    exchange    VARCHAR     NOT NULL,   -- CZCE, DCE, ...
    symbol      VARCHAR     NOT NULL,   -- CF, m, i, ...
    session     VARCHAR     NOT NULL,   -- day, night
    last_price  DOUBLE,
    volume      BIGINT,
    open_interest BIGINT,
    bid_price1  DOUBLE,
    bid_volume1 BIGINT,
    ask_price1  DOUBLE,
    ask_volume1 BIGINT,
)
"""

KLINE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS klines (
    datetime    TIMESTAMP   NOT NULL,
    product     VARCHAR     NOT NULL,
    symbol      VARCHAR     NOT NULL,
    timeframe   VARCHAR     NOT NULL,   -- 1min, 5min, 15min, 60min, day, week
    open        DOUBLE,
    high        DOUBLE,
    low         DOUBLE,
    close       DOUBLE,
    volume      DOUBLE,
    open_interest DOUBLE,
)
"""

TICK_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ticks_product ON ticks(product, datetime);",
    "CREATE INDEX IF NOT EXISTS idx_ticks_date ON ticks(CAST(datetime AS DATE), product);",
    "CREATE INDEX IF NOT EXISTS idx_ticks_session ON ticks(product, CAST(datetime AS DATE), session);",
]

KLINE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_klines_lookup ON klines(product, timeframe, datetime);",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_klines_unique ON klines(product, symbol, timeframe, datetime);",
]


class DatabaseManager:
    """Singleton DuckDB connection manager."""

    _instance = None
    _lock = threading.Lock()

    def __init__(self, db_path: str = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._conn = duckdb.connect(self.db_path)
        self._conn.execute("PRAGMA threads=4;")
        self._conn.execute("PRAGMA memory_limit='512MB';")
        self._init_schema()

    def _init_schema(self):
        """Create tables and indexes if not exist."""
        self._conn.execute(TICK_TABLE_DDL)
        self._conn.execute(KLINE_TABLE_DDL)
        for idx in TICK_INDEXES + KLINE_INDEXES:
            self._conn.execute(idx)

    def __new__(cls, db_path: str = None):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            DatabaseManager._instance = None

    # ── Tick operations ──

    def insert_ticks(self, df: pd.DataFrame):
        """Bulk insert ticks from DataFrame."""
        if df.empty:
            return 0
        self._conn.execute("INSERT INTO ticks SELECT * FROM df")
        return len(df)

    def get_ticks(
        self,
        product: str,
        date: str,
        session: str = None,
    ) -> pd.DataFrame:
        """Query ticks for a product on a given date, optionally by session."""
        params = [product, date]
        sql = """
            SELECT * FROM ticks
            WHERE product = ? AND CAST(datetime AS DATE) = CAST(? AS DATE)
        """
        if session:
            sql += " AND session = ?"
            params.append(session)
        sql += " ORDER BY datetime"
        return self._conn.execute(sql, params).df()

    def get_tick_date_range(self, product: str) -> tuple:
        """Get min/max date for a product's tick data."""
        row = self._conn.execute("""
            SELECT MIN(CAST(datetime AS DATE)), MAX(CAST(datetime AS DATE))
            FROM ticks WHERE product = ?
        """, [product]).fetchone()
        return (row[0], row[1]) if row else (None, None)

    def get_tick_summary(self, product: str, date: str = None) -> pd.DataFrame:
        """Aggregated daily summary for a product."""
        sql = """
            SELECT 
                CAST(datetime AS DATE) AS date,
                session,
                MIN(datetime) AS first_time,
                MAX(datetime) AS last_time,
                FIRST(last_price) AS open,
                MAX(last_price) AS high,
                MIN(last_price) AS low,
                LAST(last_price) AS close,
                MAX(volume) AS volume,
                MAX(open_interest) AS open_interest,
                COUNT(*) AS ticks
            FROM ticks
            WHERE product = ?
        """
        params = [product]
        if date:
            sql += " AND CAST(datetime AS DATE) = CAST(? AS DATE)"
            params.append(date)
        sql += " GROUP BY CAST(datetime AS DATE), session ORDER BY date, session"
        return self._conn.execute(sql, params).df()

    def list_dates(self, product: str = None) -> list:
        """List all dates with tick data, optionally filtered by product."""
        if product:
            rows = self._conn.execute("""
                SELECT DISTINCT CAST(datetime AS DATE) AS dt
                FROM ticks WHERE product = ? ORDER BY dt
            """, [product]).fetchall()
        else:
            rows = self._conn.execute("""
                SELECT DISTINCT CAST(datetime AS DATE) AS dt
                FROM ticks ORDER BY dt
            """).fetchall()
        return [str(r[0]) for r in rows]

    def list_products(self) -> list[dict]:
        """List all products with file/tick counts."""
        rows = self._conn.execute("""
            SELECT 
                product,
                COUNT(*) AS total_ticks,
                MIN(CAST(datetime AS DATE)) AS start_date,
                MAX(CAST(datetime AS DATE)) AS end_date,
                COUNT(DISTINCT CAST(datetime AS DATE)) AS trading_days
            FROM ticks
            GROUP BY product
            ORDER BY product
        """).fetchall()

        return [
            {
                "product": r[0],
                "ticks": r[1],
                "start_date": str(r[2]),
                "end_date": str(r[3]),
                "trading_days": r[4],
            }
            for r in rows
        ]

    def get_daily_products(self, date: str) -> list[dict]:
        """Get per-product session stats for a single date."""
        rows = self._conn.execute("""
            SELECT 
                product,
                session,
                COUNT(*) AS file_count,
                MIN(datetime) AS first_time,
                MAX(datetime) AS last_time,
                MIN(last_price) AS first_price,
                MAX(last_price) AS high,
                MIN(last_price) AS low,
                LAST(last_price) AS last_price,
                MAX(volume) AS volume,
                COUNT(*) AS tick_count
            FROM ticks
            WHERE CAST(datetime AS DATE) = CAST(? AS DATE)
            GROUP BY product, session
            ORDER BY product, session
        """, [date]).fetchall()

        products = {}
        for r in rows:
            p = r[0]
            if p not in products:
                products[p] = {"product": p, "day": None, "night": None}
            products[p][r[1]] = {
                "ticks": r[9],
                "first": str(r[3]).split(" ")[-1][:5] if r[3] else "",
                "last": str(r[4]).split(" ")[-1][:5] if r[4] else "",
                "open": r[5],
                "high": r[6],
                "low": r[7],
                "close": r[8],
                "volume": r[9],
            }
        return list(products.values())

    # ── Kline operations ──

    def upsert_klines(self, df: pd.DataFrame, timeframe: str):
        """Insert or replace klines for a timeframe."""
        if df.empty:
            return 0
        # Delete existing entries for these products and timeframe
        products = df["product"].unique().tolist()
        placeholders = ", ".join(["?"] * len(products))
        self._conn.execute(
            f"DELETE FROM klines WHERE timeframe = ? AND product IN ({placeholders})",
            [timeframe] + products,
        )
        # Ensure column order matches table schema
        cols = ["datetime", "product", "symbol", "timeframe",
                "open", "high", "low", "close", "volume", "open_interest"]
        df = df[cols]
        self._conn.execute("INSERT INTO klines SELECT * FROM df")
        return len(df)

    def get_klines(
        self,
        product: str,
        timeframe: str,
        start_date: str = None,
        end_date: str = None,
    ) -> pd.DataFrame:
        """Query klines for backtesting."""
        params = [product, timeframe]
        sql = """
            SELECT * FROM klines
            WHERE product = ? AND timeframe = ?
        """
        if start_date:
            sql += " AND datetime >= ?"
            params.append(start_date)
        if end_date:
            sql += " AND datetime <= ?"
            params.append(end_date)
        sql += " ORDER BY datetime"
        return self._conn.execute(sql, params).df()

    def resample_klines(self, product: str, timeframe: str, start_date: str = None) -> pd.DataFrame:
        """Resample ticks to klines using DuckDB SQL (zero Python overhead)."""
        time_bucket = {
            "1min": "1 minute",
            "5min": "5 minutes",
            "15min": "15 minutes",
            "60min": "1 hour",
            "day": "1 day",
            "week": "1 week",
        }
        bucket = time_bucket.get(timeframe)
        if not bucket:
            raise ValueError(f"Unknown timeframe: {timeframe}")

        params = [product]
        sql = f"""
            SELECT 
                time_bucket(INTERVAL '{bucket}', datetime) AS datetime,
                product,
                FIRST(symbol) AS symbol,
                FIRST(last_price) AS open,
                MAX(last_price) AS high,
                MIN(last_price) AS low,
                LAST(last_price) AS close,
                -- Volume is cumulative in tick data, use LAST - FIRST per bar
                GREATEST(LAST(volume) - FIRST(volume), 0) AS volume,
                LAST(open_interest) AS open_interest
            FROM ticks
            WHERE product = ? AND last_price > 0
        """
        if start_date:
            sql += " AND CAST(datetime AS DATE) >= CAST(? AS DATE)"
            params.append(start_date)
        sql += """
            GROUP BY time_bucket(INTERVAL '{}', datetime), product
            ORDER BY datetime
        """.format(bucket)

        return self._conn.execute(sql, params).df()
