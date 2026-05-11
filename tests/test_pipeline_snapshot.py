"""End-to-end pipeline snapshot test.

Runs the full pipeline against a synthetic multi-broker portfolio and
asserts specific output figures.  This is the backstop against silent
regressions: if a future refactor changes behaviour, at least one of
these assertions will fail even if every unit test still passes.

The synthetic portfolio is small enough to reason about by hand (see
tests/fixtures/build_synthetic_portfolio.py for what it contains) so
assertion values can be verified against first-principles math.  When
you legitimately change the algorithm, update the expected values
here with commit-message reasoning.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fixtures.build_synthetic_portfolio import build as build_portfolio


@pytest.fixture
def synthetic_pipeline(isolated_workdir, stub_prices):
    """Set up the synthetic fixtures, stub prices, run the pipeline,
    and return the exported JSON.  Stubbed prices are deterministic so
    the asserted dollar figures are reproducible.
    """
    build_portfolio(isolated_workdir)

    # Deterministic stub prices at today's date and at historical
    # sample dates.  These feed both the history walker and the
    # final "last_prices" lookup in main.py.
    stub_prices.set("AAPL", {
        "2023-01-15": 150.0, "2023-06-01": 180.0, "2023-11-01": 200.0,
        "2024-01-01": 190.0, "2024-10-15": 225.0, "2024-12-31": 250.0,
        "2025-01-01": 250.0,
    })
    stub_prices.set("TWTR", {"2024-01-01": 50.0, "2024-10-31": 54.2})
    stub_prices.set("XLNX", {"2024-05-01": 200.0, "2024-10-15": 150.0})
    stub_prices.set("AMD",  {"2024-10-15": 150.0, "2024-10-22": 150.0,
                              "2024-12-31": 160.0, "2025-01-01": 160.0})
    stub_prices.set("SPY",  {"2023-01-15": 400.0, "2024-12-31": 500.0,
                              "2025-01-01": 500.0})
    stub_prices.set("VWENX", {"2023-01-15": 75.0, "2023-12-31": 78.0,
                               "2025-01-01": 80.0})

    stub_prices.set_sector("AAPL", "Technology")
    stub_prices.set_sector("AMD",  "Technology")
    stub_prices.set_sector("SPY",  "ETFs")

    # Invoke the real pipeline entry point (skip-rename so we don't
    # try to rewrite file names).
    import sys
    argv_save = sys.argv
    sys.argv = ["fin", "--skip-rename"]
    try:
        from src.main import main
        main()
    finally:
        sys.argv = argv_save

    exports = isolated_workdir / "exports"
    json_path = exports / "transactions.json"
    assert json_path.exists(), "pipeline did not produce transactions.json"
    return json.loads(json_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Header / count invariants
# ---------------------------------------------------------------------------

class TestPipelineHeader:
    def test_emits_transactions_json(self, synthetic_pipeline):
        assert "transactions" in synthetic_pipeline
        assert synthetic_pipeline["count"] == len(synthetic_pipeline["transactions"])

    def test_action_catalog_embedded(self, synthetic_pipeline):
        """Phase 2 invariant — catalog travels in the JSON so the JS
        side can build its membership sets."""
        cat = synthetic_pipeline.get("action_catalog", {}).get("actions", [])
        assert len(cat) >= 20, "action catalog looks truncated"
        names = {a["name"] for a in cat}
        assert {"Buy", "Sell", "Contribution Reversal", "Spinoff"} <= names

    def test_emits_dashboard_html(self, isolated_workdir, synthetic_pipeline):
        html = (isolated_workdir / "exports" / "dashboard.html")
        assert html.exists()
        text = html.read_text(encoding="utf-8")
        # Markers substituted, no naked placeholder left behind
        assert "@@STYLES@@" not in text
        assert "@@APP_JS@@" not in text
        assert "__JSON_DATA__" not in text


# ---------------------------------------------------------------------------
# Per-symbol end-state assertions
# ---------------------------------------------------------------------------

class TestSymbolBalances:
    def test_aapl_balance_after_buys_and_sell(self, synthetic_pipeline):
        """Bought 10 + 5, sold 3 → final 12 shares."""
        aapl = _holding(synthetic_pipeline, "Robinhood", "AAPL")
        assert aapl is not None
        assert aapl["quantity"] == pytest.approx(12.0)

    def test_twtr_cleared_by_cash_merger(self, synthetic_pipeline):
        """MRGS + MRGC collapses to a Sell; balance ends at 0 (dust
        filter) so TWTR shouldn't appear as a holding."""
        assert _holding(synthetic_pipeline, "Robinhood", "TWTR") is None

    def test_xlnx_cleared_amd_holds_1(self, synthetic_pipeline):
        """Stock-for-stock: XLNX surrender + AMD receive (1 whole
        share) + AMD CIL for 0.723 fractional (never issued, income).
        End state: 0 XLNX, 1 AMD."""
        assert _holding(synthetic_pipeline, "Robinhood", "XLNX") is None
        amd = _holding(synthetic_pipeline, "Robinhood", "AMD")
        assert amd is not None
        assert amd["quantity"] == pytest.approx(1.0), \
            "AMD CIL must NOT subtract from balance — those shares " \
            "were never issued"

    def test_voya_contribution_reversal_nets_shares(self, synthetic_pipeline):
        """Three CONTRIBUTION rows + 1 reversal + 1 DIVIDEND reinvest
        = 3 - 1 + 0.01 = 2.01 shares."""
        wellington = _holding(synthetic_pipeline, "Rollover IRA",
                              "VANG WELLINGTON ADM")
        assert wellington is not None
        assert wellington["quantity"] == pytest.approx(2.01)


# ---------------------------------------------------------------------------
# Realized P&L
# ---------------------------------------------------------------------------

class TestRealizedGains:
    def test_aapl_partial_sell_realizes_fifo_basis(self, synthetic_pipeline):
        """Sold 3 AAPL from FIFO lots: 3 × $200 - 3 × $150 = $150."""
        totals = synthetic_pipeline["basis_methods"]["fifo"]["totals"]
        aapl_rg = _realized_for_symbol(synthetic_pipeline, "AAPL")
        assert aapl_rg == pytest.approx(150.0)
        # And the top-line realized should be AAPL's plus TWTR's merger gain
        # TWTR: 2 × $54.20 - 2 × $50 = $8.40 realized
        twtr_rg = _realized_for_symbol(synthetic_pipeline, "TWTR")
        assert twtr_rg == pytest.approx(8.40)
        # XLNX: stock-for-stock (Sell at $0 basis → -$200)
        xlnx_rg = _realized_for_symbol(synthetic_pipeline, "XLNX")
        assert xlnx_rg == pytest.approx(-200.0)
        # Option expired worthless: basis $500 realized as loss
        opt_rg = sum(t.get("realized_gain", 0) or 0
                     for t in synthetic_pipeline["transactions"]
                     if t.get("action") == "Option Expire")
        assert opt_rg == pytest.approx(-500.0)
        # Overall realized should match sum-of-parts within rounding
        expected_total = 150.0 + 8.40 + -200.0 + -500.0
        assert totals["realized_gain"] == pytest.approx(expected_total)


# ---------------------------------------------------------------------------
# Merger fractional handling
# ---------------------------------------------------------------------------

class TestMergerFractional:
    def test_cil_after_merger_is_dividend_not_sell(self, synthetic_pipeline):
        """The AMD CIL is within 30 days after the XLNX→AMD MRGS-receive
        — must emit as a Dividend on USD so balance doesn't go negative."""
        cil_divs = [t for t in synthetic_pipeline["transactions"]
                    if t.get("symbol") == "USD"
                    and t.get("action") == "Dividend"
                    and "Stock-for-stock CIL" in (t.get("description") or "")]
        assert len(cil_divs) == 1
        assert cil_divs[0]["amount"] == pytest.approx(108.45)


# ---------------------------------------------------------------------------
# Ticker rename layer
# ---------------------------------------------------------------------------

class TestTickerRenameEndToEnd:
    def test_rename_rules_file_respected(self, isolated_workdir,
                                         synthetic_pipeline):
        """Not asserting specific symbol output (the synthetic portfolio
        doesn't use OLDSYM) — just that the rename file was written
        and loaded without error.  Deeper tests in test_ticker_renames.py."""
        rename_path = isolated_workdir / "cache" / "ticker_renames.json"
        assert rename_path.exists()


# ---------------------------------------------------------------------------
# CUSIP capture
# ---------------------------------------------------------------------------

class TestCusipCapture:
    def test_cusips_extracted_from_descriptions(self, synthetic_pipeline):
        """Phase 5: every Robinhood txn with a CUSIP in its
        description should carry a `cusip` field."""
        aapl_buys = [t for t in synthetic_pipeline["transactions"]
                     if t.get("symbol") == "AAPL"
                     and t.get("action") == "Buy"]
        assert any(t.get("cusip") == "037833100" for t in aapl_buys)


class TestRefreshPricesParity:
    """``--refresh-prices`` must produce holdings + basis_methods totals
    identical (modulo small intraday-price drift) to a back-to-back full
    pipeline run.  A real bug — fixed 2026-04-27 — slipped USD balances
    from non-Savings accounts into the changes panel via the refresh
    path; pin the invariant so it can't recur silently."""

    def test_refresh_prices_matches_full_pipeline(self, isolated_workdir,
                                                   synthetic_pipeline):
        """After the full pipeline runs (synthetic_pipeline fixture),
        re-run with --refresh-prices and assert the produced JSON has
        the same USD totals + basis_methods.fifo.totals.cost_basis."""
        import json as _json
        from pathlib import Path
        full_data = synthetic_pipeline
        full_usd = sum(
            (h.get("value") or 0) for h in full_data.get("holdings_by_account", [])
            if h.get("symbol") == "USD"
        )
        full_basis = (full_data.get("basis_methods", {})
                      .get("fifo", {}).get("totals", {})
                      .get("cost_basis", 0))

        # Now run --refresh-prices.  Same harness as full pipeline,
        # different argv.  No new yfinance calls because the test stubs
        # are still active and the cache is now warm.
        import sys
        argv_save = sys.argv
        sys.argv = ["fin", "--refresh-prices"]
        try:
            from src.main import main
            main()
        finally:
            sys.argv = argv_save

        json_path = Path(isolated_workdir) / "exports" / "transactions.json"
        refresh_data = _json.loads(json_path.read_text(encoding="utf-8"))
        refresh_usd = sum(
            (h.get("value") or 0) for h in refresh_data.get("holdings_by_account", [])
            if h.get("symbol") == "USD"
        )
        refresh_basis = (refresh_data.get("basis_methods", {})
                         .get("fifo", {}).get("totals", {})
                         .get("cost_basis", 0))

        assert abs(full_usd - refresh_usd) < 0.01, (
            f"USD totals diverge between full pipeline (${full_usd}) "
            f"and --refresh-prices (${refresh_usd}) — likely USD-in-"
            f"non-Savings balance leakage"
        )
        assert abs(full_basis - refresh_basis) < 0.01, (
            f"basis_methods.fifo.totals.cost_basis diverges between full "
            f"pipeline (${full_basis}) and --refresh-prices "
            f"(${refresh_basis}) — likely missing cash-fold step in refresh"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _holding(data, account_group, symbol):
    for h in data.get("holdings_by_account", []):
        if h["account_group"] == account_group and h["symbol"] == symbol:
            return h
    return None


def _realized_for_symbol(data, symbol):
    return sum(
        float(t.get("realized_gain") or 0)
        for t in data["transactions"]
        if t.get("symbol") == symbol
    )
