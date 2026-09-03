"""Tests for the insight-layer analytics: money-weighted return (XIRR),
savings rate, fee rollup, expense coverage, LTCG headroom, SPY delta."""

from __future__ import annotations

import pytest


# --- XIRR -------------------------------------------------------------------

def test_xirr_simple_no_flows_matches_simple_return():
    """$10k grows to $11k over exactly one year with no flows → 10%."""
    from src.analytics._shared import compute_money_weighted_return
    history = [
        {"date": "2023-12-31", "total": 10000.0},
        {"date": "2024-12-31", "total": 11000.0},
    ]
    mw = compute_money_weighted_return([], history, [], None)
    assert mw is not None
    assert mw["annualized"] == pytest.approx(0.10, abs=0.002)
    assert mw["total_invested"] == pytest.approx(10000.0)


def test_xirr_flows_solution_zeroes_npv():
    """With a mid-period contribution, the returned rate must be an
    actual root of the NPV equation (not an approximation artifact)."""
    from src.analytics._shared import (
        _xnpv, compute_money_weighted_return,
    )
    history = [
        {"date": "2023-12-31", "total": 10000.0},
        {"date": "2024-06-30", "total": 15600.0},
        {"date": "2024-12-31", "total": 17000.0},
    ]
    txns = [{"date": "2024-06-15", "account_group": "Robinhood",
             "action": "Deposit", "symbol": "USD",
             "quantity": 5000.0, "amount": 5000.0}]
    mw = compute_money_weighted_return(txns, history, [], None)
    assert mw is not None
    # Rebuild the flow schedule and confirm NPV(irr) ≈ 0.
    flows = [(0.0, -10000.0),
             ((167) / 365.25, -5000.0),   # 2024-06-15 is 167 days after t0 (leap year)
             ((366) / 365.25, 17000.0)]
    assert _xnpv(mw["annualized"], flows) == pytest.approx(0.0, abs=1.0)
    assert mw["annualized"] > 0


def test_xirr_behavior_gap_direction():
    """Contributing right before a crash must drag XIRR below the
    no-timing story: the late dollars caught only the drop."""
    from src.analytics._shared import compute_money_weighted_return
    history = [
        {"date": "2023-12-31", "total": 10000.0},
        {"date": "2024-06-30", "total": 30000.0},   # +$20k contributed at peak
        {"date": "2024-12-31", "total": 24000.0},   # then -20%
    ]
    txns = [{"date": "2024-06-30", "account_group": "Robinhood",
             "action": "Deposit", "symbol": "USD",
             "quantity": 20000.0, "amount": 20000.0}]
    mw = compute_money_weighted_return(txns, history, [], None)
    assert mw is not None
    assert mw["annualized"] < 0, "badly-timed contribution → negative XIRR"


def test_xirr_none_on_degenerate_input():
    from src.analytics._shared import compute_money_weighted_return
    assert compute_money_weighted_return([], [], [], None) is None
    assert compute_money_weighted_return(
        [], [{"date": "2024-01-31", "total": 0.0}], [], None) is None


# --- savings rate -----------------------------------------------------------

def test_savings_by_year_rate_and_salary_effective_date():
    from src.analytics.savings import compute_savings_by_year
    meta = {
        "salary_history": [
            {"date": "2022-01-01", "amount": 100000},
            {"date": "2024-03-01", "amount": 150000},   # raise mid-2024
        ],
        "bonus_history": [{"date": "2024-02-15", "amount": 10000}],
    }
    txns = [
        {"date": "2023-05-01", "account_group": "Robinhood",
         "action": "Deposit", "symbol": "USD", "amount": 30000.0},
        {"date": "2024-05-01", "account_group": "Robinhood",
         "action": "Deposit", "symbol": "USD", "amount": 40000.0},
        {"date": "2024-08-01", "account_group": "Robinhood",
         "action": "Withdrawal", "symbol": "USD", "amount": 8000.0},
    ]
    out = compute_savings_by_year(txns, meta)
    assert out["2023"]["gross_income"] == pytest.approx(100000.0)
    assert out["2023"]["savings_rate_pct"] == pytest.approx(30.0)
    # 2024: salary 150k (effective), +10k bonus; net 40k − 8k = 32k
    assert out["2024"]["gross_income"] == pytest.approx(160000.0)
    assert out["2024"]["net_contributed"] == pytest.approx(32000.0)
    assert out["2024"]["savings_rate_pct"] == pytest.approx(20.0)


def test_savings_by_year_empty_without_salary_data():
    from src.analytics.savings import compute_savings_by_year
    txns = [{"date": "2024-05-01", "account_group": "X",
             "action": "Deposit", "symbol": "USD", "amount": 1000.0}]
    assert compute_savings_by_year(txns, {}) == {}


# --- fees -------------------------------------------------------------------

def test_fees_rollup():
    from src.analytics.savings import compute_fees
    txns = [
        {"date": "2023-01-01", "account_group": "Coinbase", "fees": 12.5},
        {"date": "2023-06-01", "account_group": "Coinbase", "fees": 7.5},
        {"date": "2024-01-01", "account_group": "Robinhood", "fees": 1.0},
        {"date": "2024-02-01", "account_group": "Robinhood", "fees": 0.0},
    ]
    out = compute_fees(txns)
    assert out["total"] == pytest.approx(21.0)
    assert out["by_year"]["2023"] == pytest.approx(20.0)
    assert out["by_account"]["Coinbase"] == pytest.approx(20.0)


# --- LTCG headroom ----------------------------------------------------------

def test_ltcg_headroom_and_niit_headroom():
    from src.analytics.tax import _tax_rate_estimate
    meta = {"filing_status": "Single",
            "salary_history": [{"date": "2024-01-01", "amount": 50000}]}
    e = _tax_rate_estimate("2026", meta, [])
    # taxable_income = 50000 − 16100 = 33900; 0% LTCG bracket cap 49450.
    assert e["ltcg_headroom"] == pytest.approx(49450 - 33900)
    assert e["ltcg_next_rate"] == 0.15
    # NIIT threshold $200k; AGI $50k → $150k of headroom.
    assert e["niit_headroom"] == pytest.approx(150000.0)


# --- income expense coverage -------------------------------------------------

def test_income_calendar_expense_coverage():
    from datetime import datetime, timedelta
    from src.analytics.income_calendar import compute_income_calendar
    recent = (datetime.now().date() - timedelta(days=30)).isoformat()
    txns = [{"date": recent, "account_group": "Robinhood", "symbol": "VOO",
             "action": "Dividend", "amount": 4000.0}]
    out = compute_income_calendar(txns, [], annual_expenses=40000.0)
    assert out["ttm_actual"] == pytest.approx(4000.0)
    assert out["expense_coverage_pct"] == pytest.approx(10.0)
    # Without expenses configured → None (dashboard hides the figure).
    out2 = compute_income_calendar(txns, [])
    assert out2["expense_coverage_pct"] is None


def test_savings_rows_report_their_own_trailing_income():
    """Every savings account's interest lands on symbol ``USD``, so a
    per-SYMBOL trailing tally is a portfolio-wide total.  Reporting that
    as one account's own income overstated it, and with two savings
    accounts open both rows printed the identical figure.  Trailing cash
    income has to be keyed by account group.
    """
    from datetime import datetime, timedelta
    from src.analytics.income_calendar import compute_income_calendar

    recent = (datetime.now().date() - timedelta(days=30)).isoformat()
    txns = [
        {"date": recent, "account_group": "Big Savings", "symbol": "USD",
         "action": "Interest", "amount": 900.0},
        {"date": recent, "account_group": "New Savings", "symbol": "USD",
         "action": "Interest", "amount": 100.0},
    ]
    holdings = [
        {"account_group": "Big Savings", "symbol": "USD",
         "quantity": 20000.0, "value": 20000.0},
        {"account_group": "New Savings", "symbol": "USD",
         "quantity": 2000.0, "value": 2000.0},
    ]
    apr = [{"account_group": "Big Savings", "date": "", "rate": 0.04},
           {"account_group": "New Savings", "date": "", "rate": 0.04}]

    out = compute_income_calendar(txns, holdings, savings_apr=apr)
    rows = {r["symbol"]: r for r in out["forecast_12mo"]
            if r.get("is_savings_rate")}
    assert rows["Big Savings (cash)"]["last_12mo_income"] == pytest.approx(900.0)
    assert rows["New Savings (cash)"]["last_12mo_income"] == pytest.approx(100.0)
    # The portfolio-wide total is still the sum of both.
    assert out["ttm_actual"] == pytest.approx(1000.0)
    # Projection stays rate x balance, independent of trailing income.
    assert rows["New Savings (cash)"]["projected_annual"] == pytest.approx(80.0)


# --- benchmark dollar delta ---------------------------------------------------

def test_benchmark_delta_in_analytics():
    from src.analytics import build_analytics
    history = [{
        "date": "2026-06-30", "total": 120000.0, "benchmark_spy": 100000.0,
        "by_account_group": {"Robinhood": 120000.0},
        "by_sector": {"Technology": 120000.0},
        "net_contributed": 90000.0, "priced_pct": 1.0, "positions": [],
    }]
    out = build_analytics([], history, [], [], retirement_meta={})
    bd = out["benchmark_delta"]
    assert bd["delta"] == pytest.approx(20000.0)
    assert bd["as_of"] == "2026-06-30"
