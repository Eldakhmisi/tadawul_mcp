"""Run all questions through both paths, log per-run KPIs to JSONL, write summary.md.

Each question can be repeated N times via --repeats; the summary then shows
min / median / max for latency, tokens, and tool-call count, plus all N
answer texts so the reader can eyeball whether the model is consistent.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

from mcp_runner import run as run_mcp  # noqa: E402
from web_runner import run as run_web  # noqa: E402

QUESTIONS_FILE = HERE / "questions.yaml"
RESULTS_DIR = HERE / "results"
RESULTS_DIR.mkdir(exist_ok=True)


def stat_cell(values: list[float], integer: bool = False) -> str:
    if not values:
        return "—"
    if len(values) == 1:
        v = values[0]
        return f"{int(v)}" if integer else f"{v:.1f}"
    fmt = (lambda v: str(int(v))) if integer else (lambda v: f"{v:.1f}")
    return f"{fmt(min(values))} / {fmt(statistics.median(values))} / {fmt(max(values))}"


def write_summary(stamp: str, by_qid: dict, repeats: int) -> Path:
    md: list[str] = [
        f"# Bench run {stamp}",
        "",
        f"Repeats per question: **{repeats}**. Cells in the table show "
        + ("`min / median / max`" if repeats > 1 else "single-run values")
        + ".",
        "",
        "| Question | tool calls (mcp / web) | latency s (mcp / web) | tokens (mcp / web) |",
        "|---|---|---|---|",
    ]

    grand_totals_mcp = {"calls": 0, "secs": 0.0, "tokens": 0}
    grand_totals_web = {"calls": 0, "secs": 0.0, "tokens": 0}

    for qid in by_qid:
        mcp_runs = by_qid[qid]["mcp_runs"]
        web_runs = by_qid[qid]["web_runs"]

        mcp_calls = [r.get("tool_call_count", 0) for r in mcp_runs]
        mcp_secs = [r.get("elapsed_seconds", 0.0) for r in mcp_runs]
        mcp_toks = [r.get("total_tokens", 0) for r in mcp_runs]

        web_calls = [r.get("tool_call_count", 0) for r in web_runs]
        web_secs = [r.get("elapsed_seconds", 0.0) for r in web_runs]
        web_toks = [r.get("total_tokens", 0) for r in web_runs]

        md.append(
            f"| {qid} "
            f"| {stat_cell(mcp_calls, integer=True)} / {stat_cell(web_calls, integer=True)} "
            f"| {stat_cell(mcp_secs)} / {stat_cell(web_secs)} "
            f"| {stat_cell(mcp_toks, integer=True)} / {stat_cell(web_toks, integer=True)} |"
        )

        grand_totals_mcp["calls"] += sum(mcp_calls)
        grand_totals_mcp["secs"] += sum(mcp_secs)
        grand_totals_mcp["tokens"] += sum(mcp_toks)
        grand_totals_web["calls"] += sum(web_calls)
        grand_totals_web["secs"] += sum(web_secs)
        grand_totals_web["tokens"] += sum(web_toks)

    md.append(
        f"| **GRAND TOTAL across all repeats** "
        f"| {grand_totals_mcp['calls']} / {grand_totals_web['calls']} "
        f"| {grand_totals_mcp['secs']:.1f} / {grand_totals_web['secs']:.1f} "
        f"| {grand_totals_mcp['tokens']} / {grand_totals_web['tokens']} |"
    )
    md.append("")
    md.append("## Per-question detail (all repeats shown verbatim)")
    md.append("")

    for qid, payload in by_qid.items():
        question = payload["question"]
        mcp_runs = payload["mcp_runs"]
        web_runs = payload["web_runs"]

        md.append(f"### {qid}")
        md.append("")
        md.append(f"> {question}")
        md.append("")

        for path_label, runs in (("MCP path", mcp_runs), ("Web search path", web_runs)):
            md.append(f"**{path_label}**")
            md.append("")
            for idx, r in enumerate(runs):
                md.append(
                    f"_run {idx + 1} / {len(runs)} — "
                    f"{r.get('tool_call_count', 0)} tool calls · "
                    f"{r.get('elapsed_seconds', 0):.1f}s · "
                    f"{r.get('total_tokens', 0)} tokens_"
                )
                md.append("")
                md.append("```")
                md.append((r.get("answer") or "(empty)")[:4000])
                md.append("```")
                md.append("")

    out = RESULTS_DIR / f"summary_{stamp}.md"
    out.write_text("\n".join(md))
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="Run only this question id")
    parser.add_argument("--repeats", type=int, default=1, help="Repeats per question per path (default 1)")
    parser.add_argument("--skip-web", action="store_true")
    parser.add_argument("--skip-mcp", action="store_true")
    parser.add_argument("--temperature", type=float, default=0.2)
    args = parser.parse_args()

    questions = yaml.safe_load(QUESTIONS_FILE.read_text())
    if args.only:
        questions = [q for q in questions if q["id"] == args.only]
        if not questions:
            print(f"No question matched id={args.only!r}", file=sys.stderr)
            sys.exit(1)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    jsonl_out = RESULTS_DIR / f"run_{stamp}.jsonl"
    print(f"Writing per-run JSONL → {jsonl_out}")
    print(f"Repeats per question: {args.repeats}")

    by_qid: dict[str, dict] = {}

    with jsonl_out.open("w") as f:
        for q in questions:
            qid = q["id"]
            text = q["text"]
            print(f"\n=== [{qid}] {text}")
            by_qid[qid] = {"question": text, "type": q.get("type"), "mcp_runs": [], "web_runs": []}

            for repeat_idx in range(args.repeats):
                print(f"  -- repeat {repeat_idx + 1} / {args.repeats}")

                if not args.skip_mcp:
                    print("     MCP path…", end=" ", flush=True)
                    try:
                        mcp_result = run_mcp(text, temperature=args.temperature)
                    except Exception as e:  # noqa: BLE001
                        mcp_result = {
                            "error": str(e), "answer": "", "tool_call_count": 0,
                            "elapsed_seconds": 0, "total_tokens": 0,
                            "input_tokens": 0, "output_tokens": 0, "path": "mcp",
                        }
                        print(f"ERROR: {e}")
                    else:
                        print(
                            f"{mcp_result['tool_call_count']} calls · "
                            f"{mcp_result['elapsed_seconds']:.1f}s · "
                            f"{mcp_result['total_tokens']} tokens"
                        )
                    mcp_result["repeat_index"] = repeat_idx
                    by_qid[qid]["mcp_runs"].append(mcp_result)
                    f.write(json.dumps({"qid": qid, "question": text, "repeat": repeat_idx, "result": mcp_result}, default=str) + "\n")
                    f.flush()

                if not args.skip_web:
                    print("     web search path…", end=" ", flush=True)
                    try:
                        web_result = run_web(text, temperature=args.temperature)
                    except Exception as e:  # noqa: BLE001
                        web_result = {
                            "error": str(e), "answer": "", "tool_call_count": 0,
                            "elapsed_seconds": 0, "total_tokens": 0,
                            "input_tokens": 0, "output_tokens": 0, "path": "web",
                        }
                        print(f"ERROR: {e}")
                    else:
                        print(
                            f"{web_result['tool_call_count']} searches · "
                            f"{web_result['elapsed_seconds']:.1f}s · "
                            f"{web_result['total_tokens']} tokens"
                        )
                    web_result["repeat_index"] = repeat_idx
                    by_qid[qid]["web_runs"].append(web_result)
                    f.write(json.dumps({"qid": qid, "question": text, "repeat": repeat_idx, "result": web_result}, default=str) + "\n")
                    f.flush()

    summary = write_summary(stamp, by_qid, args.repeats)
    print(f"\nSummary → {summary}")


if __name__ == "__main__":
    main()
