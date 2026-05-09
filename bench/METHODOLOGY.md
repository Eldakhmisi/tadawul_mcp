# Bench methodology

This bench compares OpenAI `gpt-5.1` answering the same Tadawul question
two ways:

- **MCP path** — model + the 13 tools exposed by `tadawul_mcp_server.py` over stdio
- **Web path** — model + OpenAI's built-in `web_search` tool

The goal is to measure whether tying the model to a deterministic local
data snapshot produces materially better answers than letting it wander
the open web.

## Fairness controls

Both paths are controlled to differ in **exactly one variable: the
toolbox**. Everything else is held identical:

| Variable | Value | Where |
|---|---|---|
| Model | `gpt-5.1` (or `OPENAI_MODEL` env var) | both paths |
| System prompt | `BENCH_SYSTEM` from `prompts.py` (same wording) | both paths |
| User question | identical text from `questions.yaml` | both paths |
| Temperature | `0.2` (overridable via `--temperature`) | both paths |
| Repeats per question | `--repeats N` (default 1, recommend 3) | both paths |

The system prompt does **not** hint at which world the model is in. It
tells the model: use whatever tools you have, try multiple queries if the
first attempt is thin, refuse only after honest effort.

## Known asymmetries that cannot be eliminated

These are documented rather than fixed:

1. **Different OpenAI APIs.**
   - MCP path uses **Chat Completions** because the local stdio MCP cannot
     be reached from OpenAI's cloud. The bench shuttles tool calls between
     the model and the local server manually.
   - Web path uses the **Responses API**, where `web_search` is a
     first-class built-in tool that OpenAI runs internally.
   - Token counts therefore are not strictly apples-to-apples. Chat
     Completions re-bills the full message history on every loop iteration;
     Responses API hides the internal loop and reports one total. Expect
     the MCP path to look more expensive on tokens even when the work
     done is comparable.

2. **Tool surface size.**
   - MCP path exposes 13 highly specific Tadawul analytics tools.
   - Web path exposes one general-purpose `web_search` tool.
   - This is the **point** of the bench, not a flaw — but it does mean
     the gap measured here is the value of having the right tool, not the
     value of MCP-as-a-protocol.

3. **Data sources differ in methodology.**
   - The local snapshot ingests Yahoo Finance via `yfinance`.
   - Web search lands the model on Sahm Screener, Argaam, Investing.com,
     TradingView, Simply Wall St, Stock Analysis, and others, each with
     its own definitions for ROE, P/E, dividend yield, total return.
   - Differences of 1–3 percentage points on bank ROE between Yahoo and
     Sahm are normal and reflect different ROE formulas (TTM vs latest
     annual, total equity vs average equity, with or without minority
     interests). The bench treats the MCP's answer as "deterministic from
     one named source" rather than "objectively correct."

## What is logged per run

For each (question, path, repeat) triple, the JSONL row contains:

- `tool_call_count` — number of tool invocations
- `elapsed_seconds` — wall-clock time end-to-end
- `input_tokens` / `output_tokens` / `total_tokens`
- `answer` — the model's final natural-language reply
- `tool_calls` (MCP path only) — the actual tool name + arguments for each call
- `search_queries` (web path only) — the queries OpenAI ran

## What is **not** logged

These are deliberately deferred until v2 of the bench:

- **Recall / precision against ground truth.** Scoring requires either a
  human-curated answer key per question, or treating the MCP's structured
  tool output as truth. Both approaches have failure modes. Manual review
  of the answer texts is the v1 verdict mechanism.
- **USD cost.** Token counts are logged; multiply by the model's
  per-token rates externally if you want a dollar figure.
- **Hallucinated tickers / numeric error.** Would require parsing the
  natural-language answers and looking up each ticker / number against
  a reference. Future work once a scoring helper is written.

## Reproducing a run

```bash
cd ~/Desktop/tadawul_mcp
source .venv/bin/activate
cd bench

# refresh the local data first if it's stale
cd ..
python tadawul_data_export.py
python load_to_sqlite.py
cd bench

# single repeat across all 8 questions
python bench.py

# 3 repeats per question (recommended for variance)
python bench.py --repeats 3

# one specific question, useful for debugging
python bench.py --only q1_bank_screen --repeats 3
```

Output lands in `bench/results/`:
- `run_<timestamp>.jsonl` — one JSON row per (question, path, repeat)
- `summary_<timestamp>.md` — table with `min / median / max` per cell when
  repeats > 1, plus all answer texts verbatim

## When to claim a finding

A finding is reportable when:

1. The same question has been run with `--repeats 3` or more
2. The MCP-vs-web outcome is consistent across repeats (not flipping
   based on temperature variance)
3. For numeric disagreements, the underlying DB has been spot-checked
   directly with the SQL query in the project root, and the result
   matches what the MCP returned
4. The data snapshot's `last_loaded_at` timestamp is within the last 7 days

If any of those four fail, treat the result as suggestive rather than
conclusive.
