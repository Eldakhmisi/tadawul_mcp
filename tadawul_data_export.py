"""
tadawul_data_export.py
======================
Fetch comprehensive data for Tadawul (Saudi Stock Exchange) listed companies
via yfinance and save KPIs, historical prices, dividends, and financials to CSV.

Requirements
------------
    pip install yfinance pandas

Usage
-----
    python tadawul_data_export.py

Outputs (in OUTPUT_DIR, timestamped)
------------------------------------
    tadawul_summary_<ts>.csv     One row per ticker: current snapshot + KPIs
    tadawul_prices_<ts>.csv      Long-format daily OHLCV history
    tadawul_dividends_<ts>.csv   Long-format dividend / profit-distribution history
    tadawul_financials_<ts>.csv  Annual income-statement items, long format
    tadawul_failed_<ts>.csv      Tickers that failed (so you can retry)

Notes
-----
* Yahoo Finance data is delayed ~15 min; live trading data needs a paid feed.
* Tadawul Main Market tickers carry the .SR suffix on Yahoo / yfinance.
* `info` fields can be None for thinly-covered names — handle in your analysis.
* Ticker list below = 270 unique Tadawul Main Market codes (user-supplied).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

# =================== CONFIG ===================
OUTPUT_DIR = Path("./tadawul_data")
PERIOD = "5y"             # "1y" | "5y" | "10y" | "max"
INTERVAL = "1d"           # "1d" | "1wk" | "1mo"
SLEEP_BETWEEN_CALLS = 0.6 # seconds; bump up if you see rate-limit errors
MAX_RETRIES = 2

# 270 Tadawul Main Market tickers (user-supplied)
TADAWUL_TICKERS = [
    "2230.SR", "1211.SR", "2084.SR", "4337.SR", "8250.SR", "4320.SR",
    "2130.SR", "3040.SR", "4291.SR", "4145.SR", "4051.SR", "7202.SR",
    "2282.SR", "6020.SR", "2090.SR", "3080.SR", "4005.SR", "4011.SR",
    "4260.SR", "1111.SR", "1303.SR", "2190.SR", "6060.SR", "4019.SR",
    "3092.SR", "1320.SR", "6040.SR", "2030.SR", "1832.SR", "2010.SR",
    "2210.SR", "8210.SR", "4323.SR", "4082.SR", "4331.SR", "2310.SR",
    "8310.SR", "4280.SR", "4008.SR", "4340.SR", "3060.SR", "4031.SR",
    "8230.SR", "7211.SR", "2110.SR", "2250.SR", "4148.SR", "4194.SR",
    "4002.SR", "6017.SR", "1214.SR", "4300.SR", "3020.SR", "2290.SR",
    "8190.SR", "2170.SR", "8170.SR", "2050.SR", "2382.SR", "5110.SR",
    "1835.SR", "2001.SR", "4165.SR", "2150.SR", "4142.SR", "8150.SR",
    "4071.SR", "1180.SR", "4334.SR", "2070.SR", "4191.SR", "4240.SR",
    "2270.SR", "8270.SR", "1323.SR", "3003.SR", "8313.SR", "7020.SR",
    "4080.SR", "1140.SR", "4220.SR", "6018.SR", "2082.SR", "2380.SR",
    "4003.SR", "4349.SR", "2288.SR", "4017.SR", "6015.SR", "1810.SR",
    "4143.SR", "8030.SR", "4180.SR", "4192.SR", "8010.SR", "3090.SR",
    "2222.SR", "2320.SR", "4335.SR", "7040.SR", "1183.SR", "3004.SR",
    "6001.SR", "1020.SR", "1212.SR", "4200.SR", "4100.SR", "1830.SR",
    "4338.SR", "4146.SR", "1120.SR", "4292.SR", "4014.SR", "4263.SR",
    "4346.SR", "7203.SR", "2285.SR", "2340.SR", "4006.SR", "4160.SR",
    "1060.SR", "2360.SR", "4020.SR", "6012.SR", "1080.SR", "4140.SR",
    "8050.SR", "4163.SR", "7200.SR", "1833.SR", "8070.SR", "1321.SR",
    "3007.SR", "4040.SR", "4332.SR", "4326.SR", "8311.SR", "4083.SR",
    "2040.SR", "4270.SR", "6070.SR", "8160.SR", "2280.SR", "2180.SR",
    "1831.SR", "8300.SR", "3030.SR", "7204.SR", "8200.SR", "4061.SR",
    "4264.SR", "4015.SR", "4310.SR", "4347.SR", "1201.SR", "2286.SR",
    "1301.SR", "2240.SR", "2140.SR", "4009.SR", "2100.SR", "8100.SR",
    "4164.SR", "2220.SR", "4141.SR", "1834.SR", "4072.SR", "6013.SR",
    "4021.SR", "4321.SR", "4333.SR", "1324.SR", "2120.SR", "8120.SR",
    "2020.SR", "4084.SR", "4327.SR", "6050.SR", "1210.SR", "2160.SR",
    "4290.SR", "8280.SR", "3010.SR", "4344.SR", "8180.SR", "2300.SR",
    "2200.SR", "2283.SR", "2083.SR", "4250.SR", "4261.SR", "6019.SR",
    "7201.SR", "4012.SR", "4350.SR", "8240.SR", "1304.SR", "4161.SR",
    "4018.SR", "6010.SR", "6016.SR", "2080.SR", "4144.SR", "2223.SR",
    "6090.SR", "8260.SR", "4324.SR", "2060.SR", "6004.SR", "4081.SR",
    "4330.SR", "3050.SR", "1213.SR", "2370.SR", "4147.SR", "4170.SR",
    "4339.SR", "4193.SR", "1050.SR", "2284.SR", "4001.SR", "4013.SR",
    "4262.SR", "4345.SR", "2330.SR", "4007.SR", "7010.SR", "4090.SR",
    "1150.SR", "4162.SR", "4190.SR", "4070.SR", "3008.SR", "8012.SR",
    "1322.SR", "1030.SR", "7030.SR", "8020.SR", "4325.SR", "3002.SR",
    "1820.SR", "8040.SR", "4150.SR", "2350.SR", "4342.SR", "4265.SR",
    "4050.SR", "2081.SR", "4110.SR", "4004.SR", "4016.SR", "4348.SR",
    "2281.SR", "1302.SR", "2381.SR", "1202.SR", "1010.SR", "2287.SR",
    "4210.SR", "6014.SR", "3005.SR", "3091.SR", "4336.SR", "4322.SR",
    "8060.SR", "4130.SR", "4030.SR", "1182.SR", "6002.SR", "4230.SR",
]
# De-dupe while preserving order (defensive)
TADAWUL_TICKERS = list(dict.fromkeys(TADAWUL_TICKERS))

# KPI fields pulled from yfinance .info  (NB: not all populate for every name)
INFO_FIELDS = [
    "longName", "sector", "industry", "currency",
    # price snapshot
    "currentPrice", "regularMarketPrice", "previousClose",
    "open", "dayHigh", "dayLow",
    "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
    "fiftyDayAverage", "twoHundredDayAverage",
    # market structure
    "marketCap", "enterpriseValue",
    "sharesOutstanding", "floatShares",
    "averageVolume", "averageVolume10days",
    # valuation
    "trailingPE", "forwardPE", "priceToBook",
    "priceToSalesTrailing12Months",
    "trailingEps", "forwardEps", "bookValue",
    # profitability
    "totalRevenue", "revenuePerShare", "revenueGrowth",
    "grossProfits", "ebitda", "netIncomeToCommon",
    "profitMargins", "grossMargins", "operatingMargins", "ebitdaMargins",
    "returnOnAssets", "returnOnEquity",
    # balance sheet / liquidity
    "totalCash", "totalDebt", "debtToEquity",
    "currentRatio", "quickRatio",
    "operatingCashflow", "freeCashflow",
    # dividends / profit distributions
    "dividendRate", "dividendYield", "payoutRatio",
    "fiveYearAvgDividendYield",
    "exDividendDate", "lastDividendValue", "lastDividendDate",
    # risk
    "beta",
]

# =================== LOGGING ===================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("tadawul")


# =================== FETCHER ===================
def fetch_one(symbol: str) -> dict | None:
    """Fetch info, history, dividends, and annual financials for one ticker."""
    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            tk = yf.Ticker(symbol)

            info = {}
            try:
                info = tk.info or {}
            except Exception as e:
                log.debug(f"  {symbol} .info failed: {e}")

            # OHLCV history
            hist = tk.history(period=PERIOD, interval=INTERVAL, auto_adjust=False)
            if hist is not None and not hist.empty:
                hist = hist.reset_index()
                hist["ticker"] = symbol
            else:
                hist = pd.DataFrame()

            # Dividends (profit distributions)
            divs = tk.dividends
            if divs is not None and not divs.empty:
                divs = divs.reset_index()
                divs.columns = ["date", "dividend"]
                divs["ticker"] = symbol
            else:
                divs = pd.DataFrame()

            # Annual income statement (long format)
            fin_long = pd.DataFrame()
            try:
                fin = tk.income_stmt
                if fin is not None and not fin.empty:
                    fin = fin.reset_index().melt(
                        id_vars="index", var_name="period", value_name="value"
                    ).rename(columns={"index": "metric"})
                    fin["ticker"] = symbol
                    fin_long = fin
            except Exception as e:
                log.debug(f"  {symbol} financials failed: {e}")

            # Skip ticker if we got literally nothing
            if not info and hist.empty and divs.empty and fin_long.empty:
                raise RuntimeError("no data returned")

            return {
                "info": info,
                "history": hist,
                "dividends": divs,
                "financials": fin_long,
            }

        except Exception as e:
            last_err = e
            log.warning(f"  attempt {attempt}/{MAX_RETRIES} failed for {symbol}: {e}")
            time.sleep(2 * attempt)

    log.error(f"  {symbol} hard-failed: {last_err}")
    return None


# =================== ORCHESTRATOR ===================
def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    summary_rows: list[dict] = []
    all_prices: list[pd.DataFrame] = []
    all_divs: list[pd.DataFrame] = []
    all_fin: list[pd.DataFrame] = []
    failed: list[dict] = []

    total = len(TADAWUL_TICKERS)
    log.info(f"Fetching {total} Tadawul tickers (period={PERIOD}, interval={INTERVAL})...")

    for i, sym in enumerate(TADAWUL_TICKERS, 1):
        log.info(f"[{i:>3}/{total}] {sym}")
        result = fetch_one(sym)

        if result is None:
            failed.append({"ticker": sym, "reason": "hard_fail"})
            time.sleep(SLEEP_BETWEEN_CALLS)
            continue

        # Summary row
        info = result["info"]
        row = {"ticker": sym}
        for f in INFO_FIELDS:
            row[f] = info.get(f)
        summary_rows.append(row)

        if not result["history"].empty:
            all_prices.append(result["history"])
        if not result["dividends"].empty:
            all_divs.append(result["dividends"])
        if not result["financials"].empty:
            all_fin.append(result["financials"])

        time.sleep(SLEEP_BETWEEN_CALLS)

    # ===== Persist =====
    stamp = datetime.now().strftime("%Y%m%d_%H%M")

    if summary_rows:
        path = OUTPUT_DIR / f"tadawul_summary_{stamp}.csv"
        pd.DataFrame(summary_rows).to_csv(path, index=False)
        log.info(f"Wrote {path} ({len(summary_rows)} rows)")

    if all_prices:
        path = OUTPUT_DIR / f"tadawul_prices_{stamp}.csv"
        pd.concat(all_prices, ignore_index=True).to_csv(path, index=False)
        log.info(f"Wrote {path}")

    if all_divs:
        path = OUTPUT_DIR / f"tadawul_dividends_{stamp}.csv"
        pd.concat(all_divs, ignore_index=True).to_csv(path, index=False)
        log.info(f"Wrote {path}")

    if all_fin:
        path = OUTPUT_DIR / f"tadawul_financials_{stamp}.csv"
        pd.concat(all_fin, ignore_index=True).to_csv(path, index=False)
        log.info(f"Wrote {path}")

    if failed:
        path = OUTPUT_DIR / f"tadawul_failed_{stamp}.csv"
        pd.DataFrame(failed).to_csv(path, index=False)
        log.info(f"Wrote {path} ({len(failed)} failed)")

    log.info("Done.")


if __name__ == "__main__":
    main()
