"""Tool-level tests against the seed DB."""
from __future__ import annotations


def test_database_info_includes_disclaimer(server):
    info = server.database_info()
    assert "disclaimer" in info
    assert "NOT FINANCIAL ADVICE" in info["disclaimer"]
    assert info["ticker_count"] == 3
    assert info["last_loaded_at"] == "2026-05-01T00:00:00+00:00"


def test_list_universe_filters_by_sector(server):
    healthcare = server.list_universe(sector="Healthcare", limit=10)
    tickers = {r["ticker"] for r in healthcare}
    assert tickers == {"1111.SR", "3333.SR"}
    energy = server.list_universe(sector="Energy", limit=10)
    assert {r["ticker"] for r in energy} == {"2222.SR"}


def test_get_snapshot_known_ticker(server):
    snap = server.get_snapshot("1111")
    assert snap["ticker"] == "1111.SR"
    assert snap["longName"] == "Alpha Co"
    assert snap["sector"] == "Healthcare"


def test_get_snapshot_unknown_ticker(server):
    snap = server.get_snapshot("9999")
    assert "error" in snap


def test_screen_stocks_min_dividend_yield(server):
    # Yields: Alpha=6%, Beta=4%, Gamma=1% — filter at 5% should leave only Alpha.
    rows = server.screen_stocks(min_dividend_yield=0.05)
    tickers = {r["ticker"] for r in rows}
    assert tickers == {"1111.SR"}


def test_screen_stocks_max_pe(server):
    # PEs: Alpha=10, Beta=18, Gamma=30 — filter <=15 leaves only Alpha.
    rows = server.screen_stocks(max_pe=15.0)
    assert {r["ticker"] for r in rows} == {"1111.SR"}


def test_screen_stocks_combined(server):
    # ROE>=10% AND debt/equity<=1: Alpha (ROE 20, D/E 0.3) and Beta (25, 0.5).
    rows = server.screen_stocks(min_roe=0.10, max_debt_to_equity=1.0)
    assert {r["ticker"] for r in rows} == {"1111.SR", "2222.SR"}


def test_rank_stocks_top_by_market_cap(server):
    rows = server.rank_stocks(metric="marketCap", n=2)
    assert [r["ticker"] for r in rows] == ["2222.SR", "1111.SR"]


def test_rank_stocks_rejects_unknown_metric(server):
    rows = server.rank_stocks(metric="not_a_real_field")
    assert "error" in rows[0]


def test_total_return_alpha_positive(server):
    # Alpha drifts +0.05%/day → strongly positive 1-year total return.
    res = server.total_return("1111.SR", years=1.0)
    assert "error" not in res
    assert res["ticker"] == "1111.SR"
    assert res["total_return_pct"] > 0
    assert res["dividend_events"] >= 1
    assert res["annualized_cagr_pct"] is not None


def test_correlation_matrix_diagonal_is_one(server):
    res = server.correlation_matrix(["1111", "2222"], days=200)
    assert "matrix" in res
    assert res["matrix"]["1111.SR"]["1111.SR"] == 1.0
    assert res["matrix"]["2222.SR"]["2222.SR"] == 1.0


def test_correlation_matrix_requires_two(server):
    res = server.correlation_matrix(["1111"], days=200)
    assert "error" in res


def test_volatility_returns_annualized(server):
    res = server.volatility("1111.SR", days=252)
    assert "error" not in res
    assert res["annualized_vol_pct"] is not None
    assert res["observations"] > 100


def test_drawdown_analysis_alpha(server):
    # A monotonically rising series has zero drawdown.
    res = server.drawdown_analysis("1111.SR", years=1.0)
    assert "error" not in res
    assert res["max_drawdown_pct"] <= 0  # always non-positive
    assert res["current_drawdown_pct"] <= 0


def test_dividend_analysis_alpha_has_history(server):
    res = server.dividend_analysis("1111.SR")
    assert res["events"] >= 3
    assert res["total_paid_in_history"] > 0
    assert "current_snapshot" in res


def test_dividend_analysis_no_history(server):
    res = server.dividend_analysis("3333.SR")
    assert res["events"] == 0


def test_compare_stocks_returns_one_row_per_ticker(server):
    res = server.compare_stocks(["1111", "2222", "3333"])
    assert len(res) == 3
    tickers = {r["ticker"] for r in res}
    assert tickers == {"1111.SR", "2222.SR", "3333.SR"}


def test_compare_stocks_rejects_unknown_metric(server):
    res = server.compare_stocks(["1111"], metrics=["not_a_real_field"])
    assert "error" in res[0]


def test_sector_summary_aggregates(server):
    rows = server.sector_summary()
    sectors = {r["sector"] for r in rows}
    assert sectors == {"Healthcare", "Energy"}
    healthcare = next(r for r in rows if r["sector"] == "Healthcare")
    assert healthcare["constituents"] == 2


def test_momentum_scan_returns_sorted(server):
    rows = server.momentum_scan(lookback_days=60, top_n=3)
    assert len(rows) <= 3
    # default ascending=False → largest gainers first
    pcts = [r["change_pct"] for r in rows]
    assert pcts == sorted(pcts, reverse=True)
