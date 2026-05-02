"""Smoke tests: module imports, MCP object exists, tools are registered."""
from __future__ import annotations

import asyncio


def test_module_imports(server):
    assert server is not None
    assert hasattr(server, "mcp")
    assert server.mcp.name == "tadawul-analytics"


def test_server_instructions_contain_disclaimer(server):
    # The instructions are surfaced to the LLM at session start; they MUST
    # tell the model not to issue buy/sell recommendations.
    instr = server.SERVER_INSTRUCTIONS.lower()
    assert "buy" in instr and "sell" in instr
    assert "recommendation" in instr


def test_all_tools_registered(server):
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    expected = {
        "database_info", "list_universe", "get_snapshot",
        "screen_stocks", "rank_stocks", "total_return",
        "correlation_matrix", "volatility", "drawdown_analysis",
        "dividend_analysis", "compare_stocks", "sector_summary",
        "momentum_scan",
    }
    assert names == expected, f"missing: {expected - names}, extra: {names - expected}"


def test_normalize_ticker(server):
    assert server._normalize_ticker("4163") == "4163.SR"
    assert server._normalize_ticker("4163.SR") == "4163.SR"
    assert server._normalize_ticker(" 4163 ") == "4163.SR"
    assert server._normalize_ticker("4163.sr") == "4163.SR"
