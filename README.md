# Tadawul Analytics MCP Server

A Model Context Protocol (MCP) server exposing analytical tools over a local
snapshot of the Tadawul (Saudi Stock Exchange) Main Market — built so an
LLM-with-search **cannot** replicate the value: universe-wide screening,
multi-stock statistics, true total returns, and risk metrics computed
deterministically from cached price history.

> **Benchmarked head-to-head against web search.** Same model, same prompt,
> 8 Tadawul analytics questions, 3 runs each. With this MCP: 7 / 8 answered
> with concrete numbers. Without it (web search only): 1 / 8.
> See [`BENCHMARKS.md`](BENCHMARKS.md) for the full results, including the
> two methodology bugs the bench surfaced and the commits that fixed them.

## ⚠️ Information only — not financial advice

This server is for **information, research, and educational purposes only**.
It returns factual market data and computed statistics over a delayed
(~15 min) snapshot of historical Tadawul data. It is explicitly designed
**not** to:

- issue buy / sell / hold recommendations
- predict future prices, returns, or movements
- rate, rank, or characterize stocks as "good", "bad", or "high-quality investments"
- suggest portfolio weights, allocations, or trades
- provide financial, tax, legal, or regulatory advice

The included `SERVER_INSTRUCTIONS` constant is exposed to the calling LLM at
session start, telling it to present results as factual measurements and to
decline buy/sell-recommendation framing. Output reliability is also
constrained by Yahoo Finance coverage, which can lag corporate filings and
omit suspended/delisted names. **Consult a licensed financial advisor
before making any investment decision.**

## Architecture

```
                  daily cron / manual run
   ┌──────────────────────────────────────────┐
   │ 1. tadawul_data_export.py                │
   │      yfinance → ./tadawul_data/*.csv     │
   └──────────────────────────────────────────┘
                  │
                  ▼
   ┌──────────────────────────────────────────┐
   │ 2. load_to_sqlite.py                     │
   │      CSVs  →  ./tadawul.db (SQLite)      │
   └──────────────────────────────────────────┘
                  │
                  ▼
   ┌──────────────────────────────────────────┐
   │ 3. tadawul_mcp_server.py  (FastMCP)      │
   │      stdio (now)  or  streamable-http    │
   │      ↳ 13 analytical tools               │
   └──────────────────────────────────────────┘
                  │
                  ▼
              Claude / agent
```

## One-time setup

```bash
# from the directory containing all three scripts
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Refresh the data (run periodically — e.g. daily after Tadawul close)

```bash
python tadawul_data_export.py        # writes timestamped CSVs to ./tadawul_data
python load_to_sqlite.py             # builds/refreshes ./tadawul.db
```

That's the full data pipeline. After the first run you'll have a DB of
roughly **270 tickers × ~1,250 daily bars** = ~340k rows of prices, plus the
summary KPIs, dividend events, and annual financials. SQLite handles this
size effortlessly.

## Run locally (stdio, for Claude Desktop)

```bash
python tadawul_mcp_server.py
```

Then add the server to Claude Desktop's config (typical paths):

| OS      | Path                                                              |
| ------- | ----------------------------------------------------------------- |
| macOS   | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json`                     |

Use `claude_desktop_config.json` (in this folder) as a template — replace the
absolute paths with your real ones, restart Claude Desktop, and the tools
will appear under "tadawul-analytics".

> **Note:** point `command` at your venv's Python (e.g.
> `/ABSOLUTE/PATH/TO/tadawul_mcp/.venv/bin/python`), not bare `python`.
> Claude Desktop launches as a GUI app and does not inherit your shell's
> PATH, so bare `python` often resolves to the wrong interpreter or fails
> to find the installed dependencies.

## Run remotely later (Streamable HTTP)

When you're ready to host it:

```bash
python tadawul_mcp_server.py --transport streamable-http --host 0.0.0.0 --port 8000
```

Same code, same tools, no rewrite. For a production HTTP deployment you
still need to add: TLS termination, OAuth 2.1 (the November 2025 MCP spec
requires it for public servers), monitoring, and a real process manager.
The code is ready; the ops are on you.

## Tool catalog

| Tool                 | What it does                               | Why search can't do it                                                       |
| -------------------- | ------------------------------------------ | ---------------------------------------------------------------------------- |
| `database_info`      | Freshness + coverage of the local DB       | Tells the agent how stale the answer will be                                 |
| `list_universe`      | Discover what's in the DB by sector        | –                                                                            |
| `get_snapshot`       | All ~50 KPIs for one ticker                | Search can do single-stock lookups, but agents prefer structured             |
| `screen_stocks`      | Multi-criteria filter across all 270 names | **Headline tool**: arbitrary boolean combinations of fundamentals, instantly |
| `rank_stocks`        | Top/bottom N by any metric                 | Universe-wide sort                                                           |
| `total_return`       | Price + dividend total return, annualized  | Search returns price-only "returns"; this is true TR                         |
| `correlation_matrix` | Pairwise correlations of daily returns     | LLMs hallucinate these                                                       |
| `volatility`         | Annualized vol from full price series      | –                                                                            |
| `drawdown_analysis`  | Max DD, current DD, recovery time          | –                                                                            |
| `dividend_analysis`  | Full dividend history + growth stats       | Search shows current yield only                                              |
| `compare_stocks`     | Side-by-side KPIs for 2-20 names           | –                                                                            |
| `sector_summary`     | Median P/E, yield, ROE per sector          | Cross-sectional aggregation                                                  |
| `momentum_scan`      | Top gainers/losers across the universe     | Universe-wide sort over time series                                          |

All tools cap result sizes (default 50, max 500 rows; max 50 tickers per
multi-ticker call) so the agent doesn't get overwhelmed.

## Example agent prompts

Once wired up, you can ask Claude things like:

- "List Tadawul healthcare stocks where P/E is under 25, dividend yield is above 3%, and ROE is above 12%."
- "What was the 5-year annualized total return on 2222.SR with dividends reinvested?"
- "Show the correlation matrix for 1120, 2010, 2222, 7010, and 4163 over the last year."
- "Which 10 large-cap Tadawul stocks currently sit furthest below their prior 5-year peak?"
- "Compare the reported KPIs for 1120, 1180, and 1150 side by side."
- "Show the names that posted the largest negative price change over the last 3 months among stocks with market cap above 5 billion SAR."

These are factual data queries; the agent is instructed by `SERVER_INSTRUCTIONS`
to return results as measurements rather than recommendations. Agents that
attempt to translate the output into "buy" or "sell" suggestions are
violating the server's stated usage contract.

## Tuning notes

- Ticker normalization: pass `"4163"` or `"4163.SR"` — both work.
- Result sizes: every list tool has a `limit` parameter (capped server-side).
- Stale data: `database_info()` returns `last_loaded_at` so an agent can
  warn the user if the snapshot is days old.
- Schema drift: yfinance occasionally renames info fields. The loader
  fills missing columns with NULL rather than crashing. If a new field
  appears that you want exposed, add it to `summary_cols` in
  `load_to_sqlite.py` and to the `summary` table in `SCHEMA`.

## Roadmap

| Phase      | Scope                                                                                                                                  |
| ---------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| **Now**    | Local stdio with the 13 tools above                                                                                                    |
| Next       | Add `peer_comparison`, `pair_trade_signals`, `dividend_aristocrats` (consecutive-increase streaks)                                     |
| Later      | Move to Streamable HTTP, add OAuth, host on a small VPS                                                                                |
| Eventually | Add quarterly financials + balance sheet + cash flow from your existing CSVs (already produced — just extend the loader and add tools) |

## Known limitations

- **Yahoo Finance lag** (~15 min on quotes; financials may trail Tadawul filings by a quarter).
- **Some 8xxx insurance names will be missing** — Yahoo flags them inactive when Tadawul suspends or merges them. Run `database_info()` to confirm coverage; failed tickers from the export step are listed in `tadawul_failed_*.csv`.
- **No intraday data** — daily bars only, by design (this is an analytics MCP, not a live-trading MCP).
- **No SAR FX conversion** — everything stays in SAR; if you want USD-equivalent KPIs, add a tool that consumes the SAR/USD rate.
