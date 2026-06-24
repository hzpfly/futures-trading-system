"""
Web dashboard for tick data — powered by DuckDB.

Replaces triple_screen/tick_dashboard.py file-scanning backend.
Same ECharts frontend, same HTML.

用法:
  python -m web.dashboard 5070
"""
import os
import sys
from datetime import datetime, timezone, timedelta

import pandas as pd
from flask import Flask, jsonify, request, send_from_directory

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from core.database import DatabaseManager

app = Flask(__name__)
TZ_BEIJING = timezone(timedelta(hours=8))
HTML_DIR = os.path.join(REPO, "triple_screen")
DB = None


def get_db():
    global DB
    if DB is None:
        DB = DatabaseManager()
    return DB


def get_best_date():
    db = get_db()
    today = datetime.now(TZ_BEIJING).strftime("%Y-%m-%d")
    yesterday = (datetime.now(TZ_BEIJING) - timedelta(days=1)).strftime("%Y-%m-%d")
    dates = db.list_dates()
    if today in dates:
        return today
    if yesterday in dates:
        return yesterday
    return dates[-1] if dates else today


@app.route("/")
def index():
    return send_from_directory(HTML_DIR, "tick_dashboard.html")


@app.route("/api/products")
def api_products():
    date_str = request.args.get("date") or get_best_date()
    db = get_db()

    try:
        products = db.get_daily_products(date_str)
    except Exception:
        return jsonify({"date": date_str, "products": {}, "count": 0})

    result = {}
    for p in products:
        name = p["product"]
        day = p.get("day")
        night = p.get("night")
        result[name] = {
            "day_files": 1 if day else 0,
            "night_files": 1 if night else 0,
        }
        if day:
            result[name]["day"] = day
        if night:
            result[name]["night"] = night

    return jsonify({"date": date_str, "products": result, "count": len(result)})


@app.route("/api/summary/<product>")
def api_summary(product):
    date_str = request.args.get("date") or get_best_date()
    db = get_db()

    try:
        summary = db.get_tick_summary(product, date_str)
    except Exception:
        return jsonify({"error": "query failed"}), 500

    result = {}
    for _, row in summary.iterrows():
        session = row["session"]
        chg = 0
        if row["open"] and row["open"] > 0:
            chg = (row["close"] - row["open"]) / row["open"] * 100

        result[session] = {
            "first": str(row["first_time"]).split(" ")[-1][:8],
            "last": str(row["last_time"]).split(" ")[-1][:8],
            "open": float(row["open"]) if row["open"] else 0,
            "high": float(row["high"]) if row["high"] else 0,
            "low": float(row["low"]) if row["low"] else 0,
            "last": float(row["close"]) if row["close"] else 0,
            "volume": float(row["volume"]) if row["volume"] else 0,
            "open_interest": float(row["open_interest"]) if row["open_interest"] else 0,
            "ticks": int(row["ticks"]),
            "change_pct": round(chg, 2),
        }

    return jsonify(result)


@app.route("/api/ticks/<product>/<session>")
def api_ticks(product, session):
    """Return tick data for ECharts."""
    date_str = request.args.get("date") or get_best_date()
    try:
        max_points = int(request.args.get("max", "2000"))
    except Exception:
        max_points = 2000

    db = get_db()

    try:
        df = db.get_ticks(product, date_str, session)
    except Exception:
        return jsonify({"error": "query failed"}), 500

    if df.empty:
        return jsonify({"ticks": []})

    # Smart sampling for large datasets
    if len(df) > max_points:
        step = len(df) // max_points
        df = df.iloc[::step]

    # ECharts format: {datetime, price, ask, bid, volume}
    ticks = []
    for _, row in df.iterrows():
        t = row["datetime"]
        ts = str(t)
        ticks.append({
            "t": ts.split(" ")[-1][:12] if " " in ts else ts[:12],
            "p": round(float(row["last_price"]), 2) if row["last_price"] else 0,
            "a": round(float(row["ask_price1"]), 2) if row["ask_price1"] else None,
            "b": round(float(row["bid_price1"]), 2) if row["bid_price1"] else None,
            "v": int(row["volume"]) if row["volume"] else 0,
        })

    return jsonify({"ticks": ticks})


@app.route("/api/dates")
def api_dates():
    db = get_db()
    return jsonify({"dates": db.list_dates()})


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("port", nargs="?", type=int, default=5070)
    args = parser.parse_args()

    print(f"Starting dashboard on http://127.0.0.1:{args.port}")
    print(f"DB: {get_db().db_path}")
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
