"""
tadawul_mcp_server.py
=====================
MCP server exposing analytical tools over the local Tadawul SQLite DB.

PURPOSE
-------
Provide factual, computed data and statistical analysis over historical
Tadawul (Saudi Stock Exchange) market data. Output is for INFORMATION and
RESEARCH ONLY.

THIS SERVER DOES NOT AND MUST NOT:
  * issue buy / sell / hold recommendations
  * predict future prices
  * rate or rank stocks by "investment quality"
  * suggest portfolio allocations
  * give tax, legal, regulatory, or financial advice

Every tool returns raw data or deterministic statistical output (means,
returns, correlations, drawdowns, screen results). Interpretation, decisions,
and any action taken on this data are entirely the responsibility of the
human user, who is expected to consult a licensed financial advisor before
making investment decisions. Data is also delayed (Yahoo Finance ~15 min)
and may lag corporate filings, so values may be stale or incorrect.

Run locally (stdio, for Claude Desktop):
    python tadawul_mcp_server.py

Run remotely (Streamable HTTP, when you're ready to host it):
    python tadawul_mcp_server.py --transport streamable-http --host 0.0.0.0 --port 8000

Tool design philosophy
----------------------
Every tool here returns a value an LLM-with-search cannot easily compute:
universe-wide screens, multi-ticker statistics, true total returns, and
risk metrics computed from the full price history. Single-ticker snapshot
lookups are included as a foundation, not the headline value. No tool
produces a recommendation, signal, or rating.
"""

from __future__ import annotations

import argparse
import math
import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterable

import numpy as np
import pandas as pd
from mcp.server.fastmcp import FastMCP

# =================== CONFIG ===================
DB_PATH = os.environ.get("TADAWUL_DB", "./tadawul.db")
MAX_TICKERS_PER_CALL = 50          # safety cap for multi-ticker tools
MAX_ROWS_RETURNED   = 500          # safety cap for any list result
TRADING_DAYS_PER_YEAR = 252        # standard for annualization

SERVER_INSTRUCTIONS = """\
This server exposes READ-ONLY analytical tools over a local snapshot of
Tadawul (Saudi Stock Exchange) historical market data.

USE THIS DATA FOR INFORMATION AND RESEARCH ONLY.

You MUST NOT:
  * present any output as a buy, sell, or hold recommendation
  * predict future prices, returns, or movements
  * rate, rank, or describe stocks as "good", "bad", or "high-quality investments"
  * suggest portfolio weights, allocations, or trades
  * provide financial, tax, legal, or regulatory advice

You SHOULD:
  * present numbers as factual measurements ("trailing P/E was 12 as of <date>")
  * describe screen criteria mechanically ("companies with P/E below 15
    AND dividend yield above 4%") not as recommendations
  * cite the data freshness from `database_info` when reporting numbers
  * remind the user that data is delayed (~15 min) and that any decision
    should be discussed with a licensed financial advisor
  * decline if asked to recommend a buy/sell/hold action and instead
    surface relevant factual data so the user can decide

This applies regardless of how the user phrases the request.
"""

mcp = FastMCP("tadawul-analytics", instructions=SERVER_INSTRUCTIONS)



ALLOWED_SUMMARY_COLS = {
    "longName", "sector", "industry", "currency",
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
    "exDividendDate", "lastDividendValue", "lastDividendDate", "beta",
}


# =================== DB HELPERS ===================
@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _clean(v: Any) -> Any:
    """Make values JSON-serializable and free of NaN/Infinity."""
    if v is None:
        return None
    if isinstance(v, (np.floating, float)):
        if math.isnan(v) or math.isinf(v):
            return None
        return float(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (pd.Timestamp,)):
        return v.strftime("%Y-%m-%d")
    return v


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {k: _clean(row[k]) for k in row.keys()}


def _df_records(df: pd.DataFrame, limit: int = MAX_ROWS_RETURNED) -> list[dict]:
    df = df.head(limit).copy()
    # Replace NaN with None and convert numpy scalars
    out: list[dict] = []
    for rec in df.to_dict(orient="records"):
        out.append({k: _clean(v) for k, v in rec.items()})
    return out


def _normalize_ticker(t: str) -> str:
    t = t.strip().upper()
    if not t.endswith(".SR"):
        t = f"{t}.SR" if t.isdigit() else t
    return t


def _validate_tickers(tickers: list[str]) -> list[str]:
    if not tickers:
        raise ValueError("at least one ticker required")
    if len(tickers) > MAX_TICKERS_PER_CALL:
        raise ValueError(f"too many tickers ({len(tickers)}); max {MAX_TICKERS_PER_CALL}")
    return [_normalize_ticker(t) for t in tickers]


# =================== TOOLS ===================
@mcp.tool()
def database_info() -> dict:
    """
    Return data freshness and coverage of the local Tadawul database.

    Use this first if you need to know how current the data is, or how many
    tickers/sectors are available before running other queries.

    Returns:
        Dict with last load timestamp, ticker count, sector breakdown,
        price-history coverage window, and dividend/financial counts.
    """
    with db() as conn:
        meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM metadata")}
        n_tickers = conn.execute("SELECT COUNT(*) AS n FROM summary").fetchone()["n"]
        sector_rows = conn.execute(
            "SELECT sector, COUNT(*) AS n FROM summary WHERE sector IS NOT NULL "
            "GROUP BY sector ORDER BY n DESC"
        ).fetchall()
        price_range = conn.execute(
            "SELECT MIN(date) AS min_d, MAX(date) AS max_d, COUNT(*) AS n FROM prices"
        ).fetchone()
        n_divs = conn.execute("SELECT COUNT(*) AS n FROM dividends").fetchone()["n"]
        n_fin = conn.execute("SELECT COUNT(*) AS n FROM financials").fetchone()["n"]

    return {
        "disclaimer": (
            "INFORMATION ONLY — NOT FINANCIAL ADVICE. This server returns "
            "factual market data and computed statistics over a delayed "
            "(~15 min) snapshot. No output should be presented as a buy/"
            "sell/hold recommendation, price prediction, or investment "
            "rating. Users should consult a licensed financial advisor "
            "before making any investment decision."
        ),
        "last_loaded_at": meta.get("last_loaded_at"),
        "ticker_count": n_tickers,
        "sectors": [{"sector": r["sector"], "count": r["n"]} for r in sector_rows],
        "price_history": {
            "earliest_date": price_range["min_d"],
            "latest_date": price_range["max_d"],
            "row_count": price_range["n"],
        },
        "dividend_event_count": n_divs,
        "financial_metric_count": n_fin,
        "source_files": {k: meta.get(f"source_{k}") for k in
                          ("summary", "prices", "dividends", "financials")},
    }


@mcp.tool()
def list_universe(sector: str | None = None, limit: int = 100) -> list[dict]:
    """
    List Tadawul tickers in the database with basic identifying info.

    Args:
        sector: Optional sector filter (e.g. "Healthcare", "Financial Services").
                Use database_info() first to see the exact sector strings.
        limit:  Max rows to return (1-500, default 100).

    Returns:
        List of {ticker, longName, sector, industry, marketCap}.
    """
    limit = max(1, min(int(limit), MAX_ROWS_RETURNED))
    sql = ("SELECT ticker, longName, sector, industry, marketCap "
           "FROM summary WHERE 1=1")
    params: list[Any] = []
    if sector:
        sql += " AND sector = ?"
        params.append(sector)
    sql += " ORDER BY marketCap DESC LIMIT ?"
    params.append(limit)
    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


@mcp.tool()
def get_snapshot(ticker: str) -> dict:
    """
    Return the full current snapshot (~50 KPIs) for a single Tadawul ticker.

    Args:
        ticker: Tadawul code; the ".SR" suffix is added automatically if missing
                (e.g. "4163" or "4163.SR" both work).

    Returns:
        Dict with all populated fields (price, valuation, profitability,
        balance-sheet, dividend, and risk metrics).
    """
    sym = _normalize_ticker(ticker)
    with db() as conn:
        row = conn.execute("SELECT * FROM summary WHERE ticker = ?", (sym,)).fetchone()
    if row is None:
        return {"error": f"ticker {sym!r} not found in database"}
    return _row_to_dict(row)


@mcp.tool()
def screen_stocks(
    sector: str | None = None,
    min_market_cap_sar: float = 0.0,
    max_market_cap_sar: float | None = None,
    min_dividend_yield: float = 0.0,
    max_pe: float | None = None,
    min_roe: float | None = None,
    max_debt_to_equity: float | None = None,
    min_profit_margin: float | None = None,
    limit: int = 50,
) -> list[dict]:
    """
    Filter the Tadawul universe by combinations of fundamental criteria.

    THIS IS THE HEADLINE TOOL: it does what search cannot — apply arbitrary
    multi-criteria filters across all ~270 listed companies in one shot.

    The tool returns a mechanical filter result. It is NOT a recommendation,
    rating, or investment list. The names returned are simply the rows in
    the database matching the boolean predicate the caller specified.

    Example mechanical filters (described by criteria, not by label):
      * "P/E below 12 AND ROE above 10% AND debt/equity below 1"
      * "dividend yield above 5% AND profit margin above 10%"
      * "market cap above 10B SAR AND ROE above 15%"
      * "sector = Healthcare AND P/E below 20"

    Args:
        sector: Sector exact match (optional).
        min_market_cap_sar / max_market_cap_sar: Market cap bounds in SAR.
        min_dividend_yield: e.g. 0.04 means >= 4%.
        max_pe: max trailing P/E (excludes negative-earnings names).
        min_roe: min Return on Equity (e.g. 0.15 = 15%).
        max_debt_to_equity: max D/E ratio.
        min_profit_margin: e.g. 0.10 = 10%.
        limit: Max rows returned (1-500, default 50).

    Returns:
        List of matching tickers with the filtered KPIs, ordered by market
        cap. The ordering is purely by size — it does NOT imply preference,
        ranking, or attractiveness. Present any result to the end user as
        a factual screen output, never as advice.
    """
    limit = max(1, min(int(limit), MAX_ROWS_RETURNED))
    sql = ("SELECT ticker, longName, sector, industry, marketCap, "
       "trailingPE, dividendYield, returnOnEquity, debtToEquity, "
       "profitMargins, currentPrice "
       "FROM summary WHERE 1=1")
    params: list[Any] = []
    if min_market_cap_sar > 0:
        sql += " AND marketCap >= ?"; params.append(min_market_cap_sar)
    if max_market_cap_sar is not None:
        sql += " AND marketCap <= ?"; params.append(max_market_cap_sar)
    if sector:
        sql += " AND sector = ?"; params.append(sector)
    if min_dividend_yield > 0:
        sql += " AND dividendYield >= ?"; params.append(min_dividend_yield)
    if max_pe is not None:
        sql += " AND trailingPE IS NOT NULL AND trailingPE > 0 AND trailingPE <= ?"
        params.append(max_pe)
    if min_roe is not None:
        sql += " AND returnOnEquity >= ?"; params.append(min_roe)
    if max_debt_to_equity is not None:
        sql += " AND debtToEquity <= ?"; params.append(max_debt_to_equity)
    if min_profit_margin is not None:
        sql += " AND profitMargins >= ?"; params.append(min_profit_margin)
    sql += " ORDER BY marketCap DESC NULLS LAST LIMIT ?"
    params.append(limit)

    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


@mcp.tool()
def rank_stocks(
    metric: str,
    n: int = 20,
    ascending: bool = False,
    sector: str | None = None,
    min_market_cap_sar: float = 0.0,
) -> list[dict]:
    """
    Rank Tadawul stocks by a single metric.

    Args:
        metric: One of the summary KPI columns. Common picks:
                marketCap, trailingPE, forwardPE, dividendYield, payoutRatio,
                returnOnEquity, returnOnAssets, profitMargins, grossMargins,
                operatingMargins, ebitdaMargins, debtToEquity, currentRatio,
                priceToBook, priceToSalesTrailing12Months, beta,
                revenueGrowth, totalRevenue, ebitda, freeCashflow.
        n: Number of names to return (1-100, default 20).
        ascending: False = top N highest, True = bottom N (lowest).
        sector: Optional sector filter.
        min_market_cap_sar: Optional min market cap to remove micro-caps.

    Returns:
        Ranked list with the chosen metric and contextual fields.
    """
    allowed = {
        "marketCap", "trailingPE", "forwardPE", "dividendYield", "payoutRatio",
        "returnOnEquity", "returnOnAssets", "profitMargins", "grossMargins",
        "operatingMargins", "ebitdaMargins", "debtToEquity", "currentRatio",
        "quickRatio", "priceToBook", "priceToSalesTrailing12Months", "beta",
        "revenueGrowth", "totalRevenue", "ebitda", "freeCashflow",
        "operatingCashflow", "trailingEps", "forwardEps", "fiveYearAvgDividendYield",
    }
    if metric not in allowed:
        return [{"error": f"metric must be one of {sorted(allowed)}"}]

    n = max(1, min(int(n), 100))
    direction = "ASC" if ascending else "DESC"
    sql = (f"SELECT ticker, longName, sector, marketCap, {metric} "
           f"FROM summary WHERE {metric} IS NOT NULL AND marketCap >= ?")
    params: list[Any] = [min_market_cap_sar]
    if sector:
        sql += " AND sector = ?"; params.append(sector)
    sql += f" ORDER BY {metric} {direction} LIMIT ?"; params.append(n)

    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


@mcp.tool()
def total_return(ticker: str, years: float = 5.0,
                 reinvest_dividends: bool = True) -> dict:
    """
    Compute historical total return for a Tadawul stock — price change PLUS
    dividends. By default, dividends are reinvested at the close price on
    the ex-date (the standard "total return index" methodology).

    This is a backward-looking factual measurement of what the stock did.
    Past performance does not predict future returns and this output is
    NOT a recommendation. Search engines often quote price-only returns;
    this tool returns the true total-return figure.

    Args:
        ticker: Tadawul code (e.g. "2222.SR").
        years: Lookback window in years (0.25 to 20). Defaults to 5.
        reinvest_dividends: True = total-return index methodology (default),
                            False = simple sum of (price change + cash dividends).

    Returns:
        Dict with start/end dates, prices, dividends paid in window,
        total return %, and annualized (CAGR) %. All values describe past
        observations only.
    """
    sym = _normalize_ticker(ticker)
    if not (0.25 <= years <= 20):
        return {"error": "years must be between 0.25 and 20"}
    cutoff = (pd.Timestamp.today() - pd.Timedelta(days=int(years * 365.25))) \
        .strftime("%Y-%m-%d")

    with db() as conn:
        px = pd.read_sql(
            "SELECT date, close FROM prices WHERE ticker = ? AND date >= ? ORDER BY date",
            conn, params=(sym, cutoff))
        dv = pd.read_sql(
            "SELECT date, dividend FROM dividends WHERE ticker = ? AND date >= ?",
            conn, params=(sym, cutoff))
    if px.empty:
        return {"error": f"no price data for {sym} in window"}
    px["date"] = pd.to_datetime(px["date"])
    if not dv.empty:
        dv["date"] = pd.to_datetime(dv["date"])

    p0 = float(px["close"].iloc[0]); p1 = float(px["close"].iloc[-1])
    d_total = float(dv["dividend"].sum()) if not dv.empty else 0.0
    n_divs = int(dv.shape[0]) if not dv.empty else 0
    days = (px["date"].iloc[-1] - px["date"].iloc[0]).days
    actual_years = days / 365.25 if days > 0 else 0

    if reinvest_dividends and not dv.empty:
        # build total-return index by reinvesting each cash div at that day's close
        merged = px.merge(dv, on="date", how="left").fillna({"dividend": 0.0})
        units = 1.0
        for _, row in merged.iterrows():
            if row["dividend"] > 0 and row["close"] > 0:
                units += (units * row["dividend"]) / row["close"]
        end_value = units * p1
        total_ret = end_value / p0 - 1.0
    else:
        total_ret = (p1 - p0 + d_total) / p0

    cagr = (1.0 + total_ret) ** (1.0 / actual_years) - 1.0 if actual_years > 0 else None

    coverage = actual_years / years if years > 0 else 0.0
    truncated = coverage < 0.85
    note = None
    if truncated:
        note = (
            f"Window truncated: requested {years}y but only {actual_years:.2f}y of data "
            f"available (coverage {coverage:.0%}). The annualized CAGR is computed over "
            f"the actual {actual_years:.2f}y window and should NOT be presented as a "
            f"{years}-year return. For ranking against full-history names, exclude this "
            f"ticker or compare like-for-like windows."
        )

    return {
        "ticker": sym,
        "start_date": px["date"].iloc[0].strftime("%Y-%m-%d"),
        "end_date":   px["date"].iloc[-1].strftime("%Y-%m-%d"),
        "years_requested": years,
        "years_actual": _clean(actual_years),
        "window_coverage_ratio": _clean(coverage),
        "window_truncated": truncated,
        "window_note": note,
        "start_price": _clean(p0),
        "end_price": _clean(p1),
        "dividends_paid_in_window": _clean(d_total),
        "dividend_events": n_divs,
        "reinvested": reinvest_dividends,
        "total_return_pct": _clean(total_ret * 100.0),
        "annualized_cagr_pct": _clean(cagr * 100.0) if cagr is not None else None,
    }


@mcp.tool()
def correlation_matrix(tickers: list[str], days: int = 252) -> dict:
    """
    Compute the pairwise correlation matrix of daily log returns over the
    most recent N trading days.

    Useful for portfolio diversification analysis and pair-trading research —
    not something an LLM with search can compute correctly.

    Args:
        tickers: 2-50 Tadawul codes.
        days: Lookback in trading days (30-2500, default 252 ≈ 1 year).

    Returns:
        Dict with the symmetric matrix, the lookback window, and the
        observation count actually used.
    """
    syms = _validate_tickers(tickers)
    if len(syms) < 2:
        return {"error": "need at least 2 tickers"}
    days = max(30, min(int(days), 2500))

    with db() as conn:
        placeholders = ",".join("?" * len(syms))
        df = pd.read_sql(
            f"SELECT ticker, date, close FROM prices WHERE ticker IN ({placeholders}) "
            f"ORDER BY ticker, date",
            conn, params=syms)
    if df.empty:
        return {"error": "no price data for the given tickers"}

    df["date"] = pd.to_datetime(df["date"])
    wide = df.pivot_table(index="date", columns="ticker", values="close").sort_index()
    wide = wide.tail(days + 1)              # +1 because returns lose one row
    rets = np.log(wide / wide.shift(1)).dropna(how="all")
    rets = rets.dropna(axis=1, how="all")
    corr = rets.corr().round(4)

    matrix = {t: {u: _clean(corr.loc[t, u]) for u in corr.columns} for t in corr.index}
    return {
        "lookback_days": days,
        "observations": int(rets.dropna().shape[0]),
        "tickers": list(corr.index),
        "matrix": matrix,
    }


@mcp.tool()
def volatility(ticker: str, days: int = 252) -> dict:
    """
    Compute annualized volatility (std-dev of daily log returns × sqrt(252))
    for a single Tadawul stock over a lookback window.

    Args:
        ticker: Tadawul code.
        days: Lookback in trading days (30-2500, default 252).

    Returns:
        Dict with annualized volatility %, daily volatility %, observation
        count, and the date range used.
    """
    sym = _normalize_ticker(ticker)
    days = max(30, min(int(days), 2500))
    with db() as conn:
        df = pd.read_sql(
            "SELECT date, close FROM prices WHERE ticker = ? ORDER BY date DESC LIMIT ?",
            conn, params=(sym, days + 1))
    if df.empty or len(df) < 30:
        return {"error": f"insufficient price data for {sym}"}
    df = df.iloc[::-1]                       # back to chronological
    df["date"] = pd.to_datetime(df["date"])
    rets = np.log(df["close"] / df["close"].shift(1)).dropna()
    daily_vol = float(rets.std())
    return {
        "ticker": sym,
        "lookback_days": days,
        "observations": int(len(rets)),
        "start_date": df["date"].iloc[0].strftime("%Y-%m-%d"),
        "end_date":   df["date"].iloc[-1].strftime("%Y-%m-%d"),
        "daily_vol_pct": _clean(daily_vol * 100),
        "annualized_vol_pct": _clean(daily_vol * math.sqrt(TRADING_DAYS_PER_YEAR) * 100),
    }


@mcp.tool()
def drawdown_analysis(ticker: str, years: float = 5.0) -> dict:
    """
    Compute drawdown statistics (max drawdown, current drawdown, recovery
    time) for a Tadawul stock — risk metrics search engines won't give you.

    Args:
        ticker: Tadawul code.
        years: Lookback in years (0.25 to 20, default 5).

    Returns:
        Dict with maximum drawdown % and dates, current drawdown %, and
        days-to-recovery from the worst trough.
    """
    sym = _normalize_ticker(ticker)
    if not (0.25 <= years <= 20):
        return {"error": "years must be between 0.25 and 20"}
    cutoff = (pd.Timestamp.today() - pd.Timedelta(days=int(years * 365.25))) \
        .strftime("%Y-%m-%d")
    with db() as conn:
        df = pd.read_sql(
            "SELECT date, close FROM prices WHERE ticker = ? AND date >= ? ORDER BY date",
            conn, params=(sym, cutoff))
    if df.empty:
        return {"error": f"no price data for {sym}"}
    df["date"] = pd.to_datetime(df["date"])
    px = df.set_index("date")["close"]

    running_max = px.cummax()
    dd = (px / running_max) - 1.0
    max_dd = float(dd.min())
    trough_date = dd.idxmin()
    peak_date = px.loc[:trough_date].idxmax()
    recovery_idx = dd.loc[trough_date:].ge(0).idxmax() if (dd.loc[trough_date:] >= 0).any() else None
    days_to_recover = (recovery_idx - trough_date).days if recovery_idx is not None else None
    current_dd = float(dd.iloc[-1])

    return {
        "ticker": sym,
        "window_years": years,
        "max_drawdown_pct": _clean(max_dd * 100),
        "peak_date": peak_date.strftime("%Y-%m-%d"),
        "trough_date": trough_date.strftime("%Y-%m-%d"),
        "days_peak_to_trough": int((trough_date - peak_date).days),
        "recovered": recovery_idx is not None,
        "recovery_date": recovery_idx.strftime("%Y-%m-%d") if recovery_idx is not None else None,
        "days_to_recover": days_to_recover,
        "current_drawdown_pct": _clean(current_dd * 100),
    }


@mcp.tool()
def dividend_analysis(ticker: str) -> dict:
    """
    Full dividend / profit-distribution history and statistics for a
    Tadawul stock. This goes beyond "current yield" — it shows the
    consistency, growth, and sustainability picture.

    Args:
        ticker: Tadawul code.

    Returns:
        Dict with full payment history, total events, total paid,
        latest dividend, year-over-year growth, and current snapshot
        (yield, payout ratio).
    """
    sym = _normalize_ticker(ticker)
    with db() as conn:
        dv = pd.read_sql(
            "SELECT date, dividend FROM dividends WHERE ticker = ? ORDER BY date",
            conn, params=(sym,))
        snap = conn.execute(
            "SELECT dividendYield, payoutRatio, fiveYearAvgDividendYield, "
            "lastDividendValue, lastDividendDate FROM summary WHERE ticker = ?",
            (sym,)).fetchone()
    if dv.empty:
        return {"ticker": sym, "events": 0, "message": "no dividend history on record"}

    dv["date"] = pd.to_datetime(dv["date"])
    dv["year"] = dv["date"].dt.year
    annual = dv.groupby("year")["dividend"].sum().reset_index()
    annual["yoy_growth_pct"] = annual["dividend"].pct_change() * 100

    yrs = annual["year"].tolist()
    avg_growth = float(annual["yoy_growth_pct"].dropna().mean()) \
        if annual["yoy_growth_pct"].notna().any() else None

    return {
        "ticker": sym,
        "events": int(len(dv)),
        "total_paid_in_history": _clean(float(dv["dividend"].sum())),
        "first_payment_date": dv["date"].iloc[0].strftime("%Y-%m-%d"),
        "last_payment_date":  dv["date"].iloc[-1].strftime("%Y-%m-%d"),
        "annual_summary": _df_records(annual),
        "avg_yoy_growth_pct": _clean(avg_growth),
        "years_with_dividends": int(annual["year"].nunique()),
        "years_span": (yrs[-1] - yrs[0] + 1) if yrs else 0,
        "current_snapshot": _row_to_dict(snap) if snap else None,
        "all_payments": _df_records(dv[["date", "dividend"]]
                                    .assign(date=dv["date"].dt.strftime("%Y-%m-%d"))),
    }


@mcp.tool()
def compare_stocks(tickers: list[str],
                   metrics: list[str] | None = None) -> list[dict]:
    """
    Side-by-side comparison of 2-20 Tadawul stocks across selected KPIs.

    Args:
        tickers: List of Tadawul codes.
        metrics: List of summary-table column names. If None, a sensible
                 default set is used (price, market cap, P/E, yield,
                 ROE, margins, debt/equity, beta).

    Returns:
        List of dicts, one per ticker, with the chosen metrics.
    """
    syms = _validate_tickers(tickers)
    if len(syms) > 20:
        return [{"error": "max 20 tickers for comparison"}]
    default_metrics = ["currentPrice", "marketCap", "trailingPE", "dividendYield",
                       "returnOnEquity", "profitMargins", "debtToEquity", "beta"]
    cols = metrics or default_metrics
    bad = [c for c in cols if c not in ALLOWED_SUMMARY_COLS]
    if bad:
        return [{"error": f"unknown metric(s): {bad}"}]
    cols = ["ticker", "longName", "sector"] + [c for c in cols if c not in
                                                 ("ticker", "longName", "sector")]
    placeholders = ",".join("?" * len(syms))
    sql = f"SELECT {','.join(cols)} FROM summary WHERE ticker IN ({placeholders})"
    with db() as conn:
        rows = conn.execute(sql, syms).fetchall()
    return [_row_to_dict(r) for r in rows]


@mcp.tool()
def sector_summary(sector: str | None = None) -> list[dict]:
    """
    Aggregate KPIs by sector — median P/E, median yield, total market cap,
    constituent count.

    Args:
        sector: Optional single sector to focus on. If None, return one row
                per sector.

    Returns:
        List of sector aggregates.
    """
    base = """
        SELECT sector,
               COUNT(*) AS constituents,
               SUM(marketCap)              AS total_market_cap,
               AVG(trailingPE)             AS avg_pe,
               AVG(dividendYield)          AS avg_dividend_yield,
               AVG(returnOnEquity)         AS avg_roe,
               AVG(profitMargins)          AS avg_profit_margin,
               AVG(debtToEquity)           AS avg_debt_to_equity
          FROM summary
         WHERE sector IS NOT NULL
    """
    params: list[Any] = []
    if sector:
        base += " AND sector = ?"; params.append(sector)
    base += " GROUP BY sector ORDER BY total_market_cap DESC"
    with db() as conn:
        rows = conn.execute(base, params).fetchall()
    return [_row_to_dict(r) for r in rows]


@mcp.tool()
def momentum_scan(lookback_days: int = 60, top_n: int = 20,
                  ascending: bool = False, min_market_cap_sar: float = 0.0) -> list[dict]:
    """
    Sort Tadawul stocks by realized percentage price change over a lookback
    window. Computed from the local price history.

    This is a descriptive sort of what already happened — it is NOT a
    momentum signal, trade idea, or recommendation. A name appearing at
    the top of the list only means it had the largest measured % price
    change in the window; it does not mean the stock will continue to
    rise (or fall).

    Args:
        lookback_days: Trading-day lookback (5-2500, default 60 ≈ 3 months).
        top_n: Number of names to return (1-100, default 20).
        ascending: False = largest % gainers, True = largest % losers.
        min_market_cap_sar: Optional floor to remove micro-caps from results.

    Returns:
        Sorted list with start price, end price, % change, and basic info.
        All values describe historical price changes only.
    """
    lookback_days = max(5, min(int(lookback_days), 2500))
    top_n = max(1, min(int(top_n), 100))

    with db() as conn:
        # Use only tickers passing the market cap floor
        cap_filter = ""
        params: list[Any] = []
        if min_market_cap_sar > 0:
            cap_filter = "WHERE ticker IN (SELECT ticker FROM summary WHERE marketCap >= ?)"
            params.append(min_market_cap_sar)
        df = pd.read_sql(
            f"SELECT ticker, date, close FROM prices {cap_filter} ORDER BY ticker, date",
            conn, params=params)
        snap = pd.read_sql("SELECT ticker, longName, sector FROM summary", conn)

    if df.empty:
        return [{"error": "no price data"}]

    df["date"] = pd.to_datetime(df["date"])
    # for each ticker, take last lookback_days+1 closes
    out = []
    for tkr, grp in df.groupby("ticker"):
        grp = grp.sort_values("date").tail(lookback_days + 1)
        if len(grp) < 2:
            continue
        p0 = float(grp["close"].iloc[0]); p1 = float(grp["close"].iloc[-1])
        if p0 <= 0:
            continue
        out.append({
            "ticker": tkr,
            "start_date": grp["date"].iloc[0].strftime("%Y-%m-%d"),
            "end_date":   grp["date"].iloc[-1].strftime("%Y-%m-%d"),
            "start_price": p0,
            "end_price": p1,
            "change_pct": (p1 / p0 - 1) * 100,
        })
    res = pd.DataFrame(out)
    if res.empty:
        return [{"error": "no usable price series"}]
    res = res.merge(snap, on="ticker", how="left")
    res = res.sort_values("change_pct", ascending=ascending).head(top_n)
    return _df_records(res)


# =================== ENTRYPOINT ===================
def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--transport", default="stdio",
                   choices=["stdio", "streamable-http", "sse"],
                   help="MCP transport (default: stdio for Claude Desktop)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()

    if args.transport == "stdio":
        mcp.run()
    else:
        # FastMCP picks up host/port from settings; pass via the run kwargs
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
