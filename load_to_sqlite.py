"""
load_to_sqlite.py
=================
Ingest the latest CSVs produced by `tadawul_data_export.py` into a single
SQLite database used by the MCP server.

Usage
-----
    python load_to_sqlite.py                       # auto-detect newest CSVs in ./tadawul_data
    python load_to_sqlite.py --data-dir /some/dir  # custom location
    python load_to_sqlite.py --db ./tadawul.db     # custom output DB

Idempotent: re-running drops & rebuilds the rows from the source CSVs.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("loader")


SCHEMA = """
CREATE TABLE IF NOT EXISTS summary (
    ticker TEXT PRIMARY KEY,
    longName TEXT, sector TEXT, industry TEXT, currency TEXT,
    currentPrice REAL, regularMarketPrice REAL, previousClose REAL,
    open REAL, dayHigh REAL, dayLow REAL,
    fiftyTwoWeekHigh REAL, fiftyTwoWeekLow REAL,
    fiftyDayAverage REAL, twoHundredDayAverage REAL,
    marketCap REAL, enterpriseValue REAL,
    sharesOutstanding REAL, floatShares REAL,
    averageVolume REAL, averageVolume10days REAL,
    trailingPE REAL, forwardPE REAL, priceToBook REAL,
    priceToSalesTrailing12Months REAL,
    trailingEps REAL, forwardEps REAL, bookValue REAL,
    totalRevenue REAL, revenuePerShare REAL, revenueGrowth REAL,
    grossProfits REAL, ebitda REAL, netIncomeToCommon REAL,
    profitMargins REAL, grossMargins REAL, operatingMargins REAL, ebitdaMargins REAL,
    returnOnAssets REAL, returnOnEquity REAL,
    totalCash REAL, totalDebt REAL, debtToEquity REAL,
    currentRatio REAL, quickRatio REAL,
    operatingCashflow REAL, freeCashflow REAL,
    dividendRate REAL, dividendYield REAL, payoutRatio REAL,
    fiveYearAvgDividendYield REAL,
    exDividendDate TEXT, lastDividendValue REAL, lastDividendDate TEXT,
    beta REAL,
    loaded_at TEXT
);

CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT, date TEXT,
    open REAL, high REAL, low REAL, close REAL, adj_close REAL, volume REAL,
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_prices_ticker ON prices(ticker);
CREATE INDEX IF NOT EXISTS idx_prices_date   ON prices(date);

CREATE TABLE IF NOT EXISTS dividends (
    ticker TEXT, date TEXT, dividend REAL,
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_dividends_ticker ON dividends(ticker);

CREATE TABLE IF NOT EXISTS financials (
    ticker TEXT, metric TEXT, period TEXT, value REAL,
    PRIMARY KEY (ticker, metric, period)
);
CREATE INDEX IF NOT EXISTS idx_financials_ticker ON financials(ticker);

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def latest(data_dir: Path, prefix: str) -> Path | None:
    """Return the newest file matching tadawul_<prefix>_*.csv in data_dir."""
    candidates = sorted(data_dir.glob(f"tadawul_{prefix}_*.csv"))
    return candidates[-1] if candidates else None


def to_iso_date(s: pd.Series) -> pd.Series:
    """Coerce any datetime-ish series to YYYY-MM-DD strings."""
    return pd.to_datetime(s, errors="coerce", utc=True).dt.strftime("%Y-%m-%d")


def load_summary(conn: sqlite3.Connection, path: Path) -> int:
    df = pd.read_csv(path)
    # Tolerate missing columns (yfinance fields shift over time)
    summary_cols = [
        "ticker", "longName", "sector", "industry", "currency",
        "currentPrice", "regularMarketPrice", "previousClose",
        "open", "dayHigh", "dayLow", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
        "fiftyDayAverage", "twoHundredDayAverage",
        "marketCap", "enterpriseValue", "sharesOutstanding", "floatShares",
        "averageVolume", "averageVolume10days",
        "trailingPE", "forwardPE", "priceToBook", "priceToSalesTrailing12Months",
        "trailingEps", "forwardEps", "bookValue",
        "totalRevenue", "revenuePerShare", "revenueGrowth",
        "grossProfits", "ebitda", "netIncomeToCommon",
        "profitMargins", "grossMargins", "operatingMargins", "ebitdaMargins",
        "returnOnAssets", "returnOnEquity",
        "totalCash", "totalDebt", "debtToEquity", "currentRatio", "quickRatio",
        "operatingCashflow", "freeCashflow",
        "dividendRate", "dividendYield", "payoutRatio", "fiveYearAvgDividendYield",
        "exDividendDate", "lastDividendValue", "lastDividendDate",
        "beta",
    ]
    for c in summary_cols:
        if c not in df.columns:
            df[c] = None
    df = df[summary_cols].copy()

    # Date columns: yfinance returns either ISO strings or Unix timestamps.
    for c in ("exDividendDate", "lastDividendDate"):
        try:
            # numeric → epoch seconds
            df[c] = pd.to_datetime(pd.to_numeric(df[c], errors="coerce"), unit="s",
                                   errors="coerce").dt.strftime("%Y-%m-%d")
        except Exception:
            df[c] = pd.to_datetime(df[c], errors="coerce").dt.strftime("%Y-%m-%d")

    df["loaded_at"] = _utcnow_iso()

    conn.execute("DELETE FROM summary")
    df.to_sql("summary", conn, if_exists="append", index=False)
    return len(df)


def load_prices(conn: sqlite3.Connection, path: Path) -> int:
    df = pd.read_csv(path)
    # yfinance history columns: Date, Open, High, Low, Close, Adj Close, Volume, ticker
    rename = {"Date": "date", "Open": "open", "High": "high", "Low": "low",
              "Close": "close", "Adj Close": "adj_close", "Volume": "volume"}
    df = df.rename(columns=rename)
    if "adj_close" not in df.columns:
        df["adj_close"] = df["close"]
    df["date"] = to_iso_date(df["date"])
    df = df.dropna(subset=["ticker", "date"])
    df = df[["ticker", "date", "open", "high", "low", "close", "adj_close", "volume"]]

    conn.execute("DELETE FROM prices")
    df.to_sql("prices", conn, if_exists="append", index=False)
    return len(df)


def load_dividends(conn: sqlite3.Connection, path: Path) -> int:
    df = pd.read_csv(path)
    df["date"] = to_iso_date(df["date"])
    df = df.dropna(subset=["ticker", "date"])
    df = df[["ticker", "date", "dividend"]]
    conn.execute("DELETE FROM dividends")
    df.to_sql("dividends", conn, if_exists="append", index=False)
    return len(df)


def load_financials(conn: sqlite3.Connection, path: Path) -> int:
    df = pd.read_csv(path)
    # Normalize period column
    df["period"] = pd.to_datetime(df["period"], errors="coerce").dt.strftime("%Y-%m-%d")
    df = df.dropna(subset=["ticker", "metric", "period"])
    df = df[["ticker", "metric", "period", "value"]]
    conn.execute("DELETE FROM financials")
    df.to_sql("financials", conn, if_exists="append", index=False)
    return len(df)


def upsert_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO metadata(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", default="./tadawul_data", type=Path)
    p.add_argument("--db", default="./tadawul.db", type=Path)
    args = p.parse_args()

    if not args.data_dir.exists():
        raise SystemExit(f"data dir not found: {args.data_dir}")

    files = {k: latest(args.data_dir, k) for k in ("summary", "prices", "dividends", "financials")}
    log.info("Resolved input CSVs:")
    for k, v in files.items():
        log.info(f"  {k:<11} -> {v}")

    if files["summary"] is None:
        raise SystemExit("no summary CSV found — run tadawul_data_export.py first")

    args.db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db)
    conn.executescript(SCHEMA)

    n_summary = load_summary(conn, files["summary"])
    log.info(f"summary:    {n_summary} rows")

    n_prices = load_prices(conn, files["prices"]) if files["prices"] else 0
    log.info(f"prices:     {n_prices} rows")

    n_divs = load_dividends(conn, files["dividends"]) if files["dividends"] else 0
    log.info(f"dividends:  {n_divs} rows")

    n_fin = load_financials(conn, files["financials"]) if files["financials"] else 0
    log.info(f"financials: {n_fin} rows")

    upsert_meta(conn, "last_loaded_at", _utcnow_iso())
    upsert_meta(conn, "source_summary",   str(files["summary"]) if files["summary"] else "")
    upsert_meta(conn, "source_prices",    str(files["prices"]) if files["prices"] else "")
    upsert_meta(conn, "source_dividends", str(files["dividends"]) if files["dividends"] else "")
    upsert_meta(conn, "source_financials",str(files["financials"]) if files["financials"] else "")

    conn.commit()
    conn.close()
    log.info(f"DB written: {args.db.resolve()}")


if __name__ == "__main__":
    main()
