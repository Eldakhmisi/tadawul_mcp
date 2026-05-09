# Benchmarks

How does an LLM with this MCP compare to the same LLM with web search only?

The full bench harness lives in [`bench/`](bench/). This page is the
human-readable summary. For methodology, biases, and reproduction steps
see [`bench/METHODOLOGY.md`](bench/METHODOLOGY.md).

## Setup

- **Model**: OpenAI `gpt-5.1`, temperature 0.2, identical system prompt on both paths
- **Path A (with MCP)**: Chat Completions API + the 13 tools in this repo over stdio
- **Path B (without MCP)**: Responses API + OpenAI's built-in `web_search` tool
- **Questions**: 8 Tadawul analytics questions, run 3 times each (24 LLM completions per path)
- **Snapshot date**: 2026-05-09

The only thing that differs between paths is the toolbox. Same model,
same prompt, same temperature, same questions.

## Headline result

| Metric | With MCP | Without MCP (web search) |
|---|---|---|
| Questions answered with concrete numbers | **7 / 8** | **1 / 8** |
| Questions where the model refused | 1 / 8 | 7 / 8 |
| Answer consistency across 3 runs | **8 / 8** | flips on the one it could attempt |
| Total wall-clock for 24 completions | 170s | 195s |

The single question both paths refused (q1 bank screen) is a real
"no rows match the filter" — both paths got it right by saying nothing
matches. The single question the web path could attempt (q3 Aramco
total return) it answered in 2 of 3 runs and refused in 1; the cited
figure (~3% CAGR) is in the ballpark of the MCP's deterministic
2.44%, but inferred from a third-party article rather than computed.

## Per-question breakdown

| # | Question | MCP | Web search |
|---|---|---|---|
| q1 | Banks with ROE>15% AND P/E<15 | refuse 3/3 (no matches in DB) | refuse 3/3 |
| q2 | Top 10 by 5-year total return, mcap > 10B SAR | answer 3/3, Maaden #1 | refuse 3/3 |
| q3 | Aramco 5-year total return | **answer 3/3, 2.44% CAGR identical** | answer 2/3 (~3% web-cited), refuse 1/3 |
| q4 | Correlation matrix of 1120, 2010, 2222, 7010 | **answer 3/3, identical matrix** | refuse 3/3 |
| q5 | Median P/E and yield, Energy vs Telecom | answer 3/3, real medians | refuse 3/3 |
| q6 | Top 5 gainers/losers, 3-month, mcap > 5B | answer 3/3 | refuse 3/3 |
| q7 | 5 large-caps furthest below 52-week high | answer 3/3 | refuse 3/3 |
| q8 | High yield + 5-year payment streak | answer 3/3 | refuse 3/3 |

## Why the web path can't answer most of these

The structural problem is not that the data doesn't exist on the open
web — it does, scattered across Investing.com, TradingView, Argaam,
Sahm Screener, Simply Wall St. The problem is that nobody publishes:

- A sortable cross-sectional table of "5-year total return with reinvested
  dividends" for every Tadawul listing
- Pairwise correlation matrices for arbitrary ticker subsets
- Sector-level distributions you can take medians from
- Universe-wide rankings on derived metrics like drawdown-from-52w-high

These require a single dataset where you can run the query end-to-end.
That's what the MCP provides: a deterministic, dated, locally-queryable
snapshot. The model becomes useful not because the MCP is smart but
because it can finally answer the question.

## What broke during testing

This bench surfaced two real bugs in the MCP. Both are fixed in the
current main branch and visible in the commit history:

1. **`total_return` annualized over short windows.** A stock with 1.9
   years of data was being ranked at 105% "5-year" CAGR in q2.
   Mathematically valid, methodologically misleading. The tool now
   returns `window_truncated`, `window_coverage_ratio`, and
   `years_requested` so the caller can exclude or footnote
   short-history names. See commit `ce0f8d3`.

2. **`sector_summary` returned averages but the docstring said "medians."**
   When q5 asked for medians, the model passed averages through and
   called them medians. Slipped 1 of 3 runs in v2. The tool now
   computes real medians and returns both `median_<metric>` and
   `mean_<metric>` with explicit names. See commit `c2ecd5e`.

After the fixes, q2 and q5 each pass 3/3 runs cleanly.

## Honest disclaimers

- **MCP data is from Yahoo Finance** via `yfinance`. ROE figures for
  Saudi banks differ from Sahm Screener / Argaam by 1–3 percentage points
  because of methodology differences (TTM net income vs latest annual,
  total equity vs average equity). The MCP returns deterministic answers
  *for the data it has*, not "objective truth."
- **Token counts are not strictly apples-to-apples** between the two
  paths. The MCP path uses Chat Completions, which re-bills the message
  history on every tool-loop iteration. The web path uses Responses
  API, which hides the internal loop and reports one total. See
  [`bench/METHODOLOGY.md`](bench/METHODOLOGY.md).
- **Single-day snapshot.** This bench reflects 2026-05-09 data. Re-running
  on a different date with different model variance could shift specific
  numbers, though the structural finding (web cannot answer cross-sectional
  Tadawul queries) is stable.

## Reproducing

```bash
git clone https://github.com/Eldakhmisi/tadawul_mcp.git
cd tadawul_mcp
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python tadawul_data_export.py    # build the snapshot
python load_to_sqlite.py
pip install -r bench/requirements.txt
cp bench/.env.example bench/.env # add your OpenAI key
cd bench
python bench.py --repeats 3
```

Output lands in `bench/results/`.
