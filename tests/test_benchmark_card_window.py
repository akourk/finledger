"""The SPY card on Performance must cover the SAME span as the TWR
card beside it.

The failure this pins: with an account filter active and the window set
to "lifetime", the two cards straddled different periods.  "Your Return
(TWR)" came from the precomputed per-filter summary, whose window starts
when THAT ACCOUNT first held value.  "SPY Return" was measured over
``windowedHistory``, which on a lifetime view is every snapshot the
portfolio has — starting when the FIRST account was opened, potentially
many years earlier.

The symptom is a comparison that reads as a catastrophic miss and is
arithmetically impossible on its face: a lower cumulative return paired
with a HIGHER annualized one.  For an account holding an S&P 500 fund it
showed the fund trailing the index by thousands of basis points a year.
Worse, ``computeSPYReturnOverPeriod`` scans every filter's summary for a
date match, so the wrong-window lookup found the *Total* portfolio's
entry and returned a real, plausible-looking figure rather than nothing.

Two halves:

* Python already computes the correctly-paired SPY figure and hangs it
  off the same summary as the return — pinned behaviourally below.
* The JS must actually read that window instead of the chart's — pinned
  by source inspection, the same way the other dashboard-layout
  invariants in this suite are.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.analytics._shared import compute_twr_summary

PERF = Path(__file__).resolve().parent.parent / "src" / "dashboard" / "app" / "90-performance.js"


@pytest.fixture(scope="module")
def perf_src():
    return PERF.read_text(encoding="utf-8")


@pytest.fixture()
def late_account_history():
    """Two accounts; "Late" opens two years after "Early".

    SPY doubles-ish across the whole span but rises only 50% across
    Late's own lifetime, so a window mix-up is unmissable.
    """
    dates = ["2020-12-31", "2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31"]
    prices = [100.0, 110.0, 120.0, 150.0, 180.0]
    groups = [
        {"Early": 1000.0},
        {"Early": 1100.0},
        {"Early": 1200.0, "Late": 1000.0},
        {"Early": 1500.0, "Late": 1250.0},
        {"Early": 1800.0, "Late": 1500.0},
    ]
    history = [
        {
            "date": d,
            "total": sum(g.values()),
            "by_account_group": dict(g),
            "benchmark_spy_price": p,
        }
        for d, p, g in zip(dates, prices, groups)
    ]
    txns = [{
        "date": "2022-06-30", "account_group": "Late", "action": "Contribution",
        "symbol": "FUND", "amount": 1000.0, "quantity": 1.0, "price": 1000.0,
    }]
    return txns, history


class TestPythonPairsSpyToTheFiltersOwnWindow:

    def test_filtered_summary_starts_when_the_account_did(self, late_account_history):
        txns, history = late_account_history
        late = compute_twr_summary(txns, history, [], {"Late"})
        assert late is not None
        assert late["start_date"] == "2022-12-31", (
            "the filtered window must begin at the account's first funded "
            "snapshot, not the portfolio's first snapshot"
        )

    def test_spy_is_measured_over_that_same_window(self, late_account_history):
        txns, history = late_account_history
        late = compute_twr_summary(txns, history, [], {"Late"})
        total = compute_twr_summary(txns, history, [], None)
        # 180/120 - 1 over Late's window; 180/100 - 1 over the whole history.
        assert late["spy_cumulative"] == pytest.approx(0.5)
        assert total["spy_cumulative"] == pytest.approx(0.8)
        assert late["spy_cumulative"] != total["spy_cumulative"], (
            "fixture no longer distinguishes the two windows"
        )

    def test_cumulative_and_annualized_agree_on_the_span(self, late_account_history):
        """The tell-tale of a mismatched pair: compounding the annualized
        figure over the reported years must reproduce the cumulative one.
        """
        txns, history = late_account_history
        late = compute_twr_summary(txns, history, [], {"Late"})
        recomputed = (1 + late["spy_annualized"]) ** late["years"] - 1
        # Loose: ``years`` ships rounded to 2dp, so exact re-derivation
        # isn't available.  A window mix-up moves this by tens of points,
        # not by the fourth decimal.
        assert recomputed == pytest.approx(late["spy_cumulative"], rel=1e-3)


class TestJsReadsTheReturnsWindowNotTheCharts:

    def test_windowed_metrics_reports_its_own_window(self, perf_src):
        block = perf_src[perf_src.index("function computeWindowedMetrics("):]
        block = block[:block.index("\nfunction ")]
        assert "startDate, endDate, spyCum, spyAnn" in block, (
            "computeWindowedMetrics must expose the span its cum/ann "
            "figures cover, or callers can only guess at it"
        )
        assert "summary.spy_cumulative" in block, (
            "the lifetime branch must carry through the SPY figure Python "
            "already paired to this filter's window"
        )

    def test_benchmark_card_does_not_reach_for_the_chart_window(self, perf_src):
        block = perf_src[perf_src.index("const twr = (win.cum != null"):]
        block = block[:block.index("const pctStr")]
        assert "win.startDate" in block and "win.endDate" in block
        assert "win.spyCum" in block, (
            "the SPY card must prefer the filter-paired figure over a "
            "date-matched lookup across every filter's summary"
        )
        # The chart's own bounds may remain only as a fallback.
        head = block[:block.index("start_date:")]
        assert "windowedHistory[0].date" not in head
