"""
Test fixtures: build a tiny in-memory-shaped SQLite DB on disk so the
MCP server module (which reads TADAWUL_DB at import time via env var)
sees a deterministic 3-ticker universe.
"""
from __future__ import annotations

import importlib
import math
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCHEMA = """
CREATE TABLE summary (
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
CREATE TABLE prices (
    ticker TEXT, date TEXT,
    open REAL, high REAL, low REAL, close REAL, adj_close REAL, volume REAL,
    PRIMARY KEY (ticker, date)
);
CREATE TABLE dividends (
    ticker TEXT, date TEXT, dividend REAL,
    PRIMARY KEY (ticker, date)
);
CREATE TABLE financials (
    ticker TEXT, metric TEXT, period TEXT, value REAL,
    PRIMARY KEY (ticker, metric, period)
);
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
"""

# Three deterministic tickers with distinct profiles
SUMMARY_ROWS = [
    {
        "ticker": "1111.SR", "longName": "Alpha Co", "sector": "Healthcare",
        "industry": "Pharma", "currency": "SAR",
        "currentPrice": 100.0, "marketCap": 50_000_000_000,
        "trailingPE": 10.0, "dividendYield": 0.06, "returnOnEquity": 0.20,
        "debtToEquity": 0.30, "profitMargins": 0.15, "beta": 0.9,
    },
    {
        "ticker": "2222.SR", "longName": "Beta Corp", "sector": "Energy",
        "industry": "Oil & Gas", "currency": "SAR",
        "currentPrice": 30.0, "marketCap": 7_000_000_000_000,
        "trailingPE": 18.0, "dividendYield": 0.04, "returnOnEquity": 0.25,
        "debtToEquity": 0.50, "profitMargins": 0.30, "beta": 0.7,
    },
    {
        "ticker": "3333.SR", "longName": "Gamma Ltd", "sector": "Healthcare",
        "industry": "Devices", "currency": "SAR",
        "currentPrice": 50.0, "marketCap": 2_000_000_000,
        "trailingPE": 30.0, "dividendYield": 0.01, "returnOnEquity": 0.05,
        "debtToEquity": 1.50, "profitMargins": 0.03, "beta": 1.4,
    },
]


def _build_prices(ticker: str, start_price: float, daily_drift: float, n_days: int = 400) -> list[tuple]:
    """Generate a deterministic price series ending today, with a tiny
    deterministic oscillation so daily returns have non-zero variance
    (correlation/volatility are otherwise undefined on a monotonic series)."""
    today = datetime(2026, 5, 1, tzinfo=timezone.utc).date()
    rows = []
    price = start_price
    step = 0
    for i in range(n_days, 0, -1):
        d = today - timedelta(days=i)
        if d.weekday() >= 5:  # skip weekends
            continue
        # ticker-specific deterministic wiggle (not random — reproducible)
        wiggle = 0.01 * math.sin(step * 0.7 + (hash(ticker) % 100) * 0.1)
        price *= (1.0 + daily_drift + wiggle)
        rows.append((ticker, d.isoformat(), price, price * 1.01, price * 0.99,
                     price, price, 1_000_000))
        step += 1
    return rows


@pytest.fixture(scope="session")
def seed_db(tmp_path_factory) -> Path:
    db_path = tmp_path_factory.mktemp("db") / "tadawul.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    # Summary rows
    cols = list(SUMMARY_ROWS[0].keys())
    placeholders = ",".join("?" * len(cols))
    for row in SUMMARY_ROWS:
        conn.execute(
            f"INSERT INTO summary ({','.join(cols)}) VALUES ({placeholders})",
            [row[c] for c in cols],
        )

    # Prices: Alpha drifts +0.05%/day, Beta +0.02%, Gamma -0.01%
    for tkr, drift, start in [("1111.SR", 0.0005, 80.0),
                              ("2222.SR", 0.0002, 28.0),
                              ("3333.SR", -0.0001, 55.0)]:
        for r in _build_prices(tkr, start, drift):
            conn.execute(
                "INSERT INTO prices (ticker,date,open,high,low,close,adj_close,volume) "
                "VALUES (?,?,?,?,?,?,?,?)", r,
            )

    # Dividends: Alpha pays 2/yr, Beta pays 1/yr, Gamma none
    for tkr, dates, amount in [
        ("1111.SR", ["2025-03-15", "2025-09-15", "2026-03-15"], 2.5),
        ("2222.SR", ["2025-06-01", "2026-04-01"], 1.5),
    ]:
        for d in dates:
            conn.execute(
                "INSERT INTO dividends (ticker,date,dividend) VALUES (?,?,?)",
                (tkr, d, amount),
            )

    conn.execute("INSERT INTO metadata(key,value) VALUES('last_loaded_at','2026-05-01T00:00:00+00:00')")
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture(scope="session")
def server(seed_db):
    """Import the MCP server module with TADAWUL_DB pointed at the seed DB."""
    os.environ["TADAWUL_DB"] = str(seed_db)
    # Make project root importable
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    if "tadawul_mcp_server" in sys.modules:
        importlib.reload(sys.modules["tadawul_mcp_server"])
    import tadawul_mcp_server  # noqa: E402
    return tadawul_mcp_server
