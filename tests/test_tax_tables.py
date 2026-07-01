"""Tests for the single-sourced federal tax tables emitted to the JSON.

The dashboard JS reads DATA.tax_tables instead of hardcoding brackets, so
the emitted payload must (a) be finite JSON and (b) faithfully reflect the
Python tables that analytics/tax.py actually computes against.
"""

from __future__ import annotations

import json
import math

import pytest


def test_tax_tables_emit_is_finite_json():
    from src.analytics.tax import tax_tables_to_json
    tt = tax_tables_to_json()
    # allow_nan=False raises on any inf/nan — guards the no-non-finite rule.
    json.dumps(tt, allow_nan=False)
    for key in ("federal_brackets", "ltcg_brackets", "std_deduction",
                "k401_limit", "roth_magi_phaseout", "section_1256_underlyings"):
        assert key in tt, f"tax_tables missing {key}"


def test_top_bracket_threshold_is_null_sentinel():
    """The open-ended top bracket emits null (→ Infinity in JS), never a
    non-finite literal."""
    from src.analytics.tax import tax_tables_to_json
    tt = tax_tables_to_json()
    top = tt["federal_brackets"]["2025"]["Single"][-1]
    assert top[0] is None and top[1] == 0.37


def test_emit_matches_python_source_tables():
    """The emitted tables must match the in-Python tables the analytics
    uses — otherwise the JS and Python tax math would diverge again."""
    from src.analytics import tax as T
    tt = T.tax_tables_to_json()
    # Spot-check every (year, status) bracket row round-trips, with inf↔null.
    for (yr, status), rows in T._BRACKETS_BY_YEAR_STATUS.items():
        emitted = tt["federal_brackets"][str(yr)][status]
        for (p_thr, p_rate), (j_thr, j_rate) in zip(rows, emitted):
            exp = None if math.isinf(p_thr) else p_thr
            assert (j_thr, j_rate) == (exp, p_rate)
    for (yr, status), amt in T._STD_DED_BY_YEAR_STATUS.items():
        assert tt["std_deduction"][str(yr)][status] == amt
    assert set(tt["section_1256_underlyings"]) == set(T.SECTION_1256_UNDERLYINGS)


def test_form_8949_taxable_only_and_term_classified():
    from src.analytics.tax import _build_form_8949
    rows = _build_form_8949([
        {"account_type": "Taxable", "account_group": "Robinhood",
         "symbol": "AAPL", "date": "2025-06-01", "quantity": 10,
         "amount": 2000, "cost_basis": 1500, "realized_gain": 500,
         "holding_days": 400},
        {"account_type": "Taxable", "account_group": "Robinhood",
         "symbol": "TSLA", "date": "2025-03-01", "quantity": 5,
         "amount": 1000, "cost_basis": 1200, "realized_gain": -200,
         "holding_days": 100},
        # Retirement disposal must be excluded (not taxable).
        {"account_type": "Retirement", "account_group": "Roth IRA",
         "symbol": "VOO", "date": "2025-01-01", "quantity": 1,
         "amount": 400, "cost_basis": 300, "realized_gain": 100,
         "holding_days": 50},
    ])
    assert len(rows) == 2, "retirement disposal should be excluded"
    by_sym = {r["description"].split()[1]: r for r in rows}
    assert by_sym["AAPL"]["term"] == "long"     # > 365 days
    assert by_sym["TSLA"]["term"] == "short"    # < 365 days
    assert by_sym["AAPL"]["date_acquired"] == "2024-04-27"  # sold - holding_days
    assert by_sym["TSLA"]["gain"] == -200.0


def test_classify_realized_splits_lots_across_one_year_line():
    """A single sell that straddles the 1-year line must split ST/LT
    lot-by-lot, not dump the whole gain into one bucket by the weighted-
    average holding period.  Regression for the NVDA 1099-B mismatch
    (08/29/25 sale classified ST by fin, LT by the broker)."""
    from src.analytics.tax import _classify_realized
    # 100 sh from a >1yr lot (gain 4000) + 100 sh from a <1yr lot (gain
    # 1000).  Weighted-avg days would be ~330 (< 365) → old code marked
    # the whole $5000 short-term.  Correct answer: 4000 LT + 1000 ST.
    txn = {
        "symbol": "NVDA", "realized_gain": 5000,
        "holding_days": 330,  # blended avg < 365 (the trap)
        "lot_breakdown": [
            {"date_acquired": "2023-06-28", "qty": 100,
             "cost_basis": 6000, "proceeds": 10000, "days": 580},
            {"date_acquired": "2024-09-30", "qty": 100,
             "cost_basis": 11000, "proceeds": 12000, "days": 333},
        ],
    }
    c = _classify_realized(txn)
    assert c["lt"] == 4000.0, "long-held lot's gain must be long-term"
    assert c["st"] == 1000.0, "short-held lot's gain must be short-term"
    assert c["st"] + c["lt"] == txn["realized_gain"]


def test_form_8949_emits_one_row_per_lot_with_real_dates():
    """With a lot_breakdown, each consumed lot is its own 8949 row with
    its true acquired date and term — no fabricated date_acquired."""
    from src.analytics.tax import _build_form_8949
    rows = _build_form_8949([
        {"account_type": "Taxable", "account_group": "Robinhood",
         "symbol": "NVDA", "date": "2025-08-29", "quantity": 200,
         "amount": 22000, "cost_basis": 17000, "realized_gain": 5000,
         "holding_days": 330,
         "lot_breakdown": [
             {"date_acquired": "2023-06-28", "qty": 100,
              "cost_basis": 6000, "proceeds": 10000, "days": 580},
             {"date_acquired": "2024-09-30", "qty": 100,
              "cost_basis": 11000, "proceeds": 12000, "days": 333},
         ]},
    ])
    assert len(rows) == 2, "multi-lot sell should emit one row per lot"
    by_date = {r["date_acquired"]: r for r in rows}
    assert by_date["2023-06-28"]["term"] == "long"
    assert by_date["2023-06-28"]["gain"] == 4000.0
    assert by_date["2024-09-30"]["term"] == "short"
    assert by_date["2024-09-30"]["gain"] == 1000.0
    # No fabricated dates — both are real acquired dates from the lots.
    assert all(r["date_acquired"] in ("2023-06-28", "2024-09-30") for r in rows)


def test_form_8949_various_when_no_holding_days():
    from src.analytics.tax import _build_form_8949
    rows = _build_form_8949([
        {"account_type": "Taxable", "symbol": "X", "date": "2025-01-01",
         "quantity": 1, "amount": 10, "cost_basis": 5, "realized_gain": 5},
    ])
    assert rows[0]["date_acquired"] == "VARIOUS"


def test_reconcile_realized_override_replaces_fin_computed():
    """A Reconcile Realized row makes the tax/MAGI calc trust the broker
    figure over fin's reconstruction (off-platform basis fin can't see).
    fin computes $80k realized; the broker reported $19,025.81 → AGI uses
    the broker number, preserving fin's short-term character."""
    from src.analytics.tax import _tax_rate_estimate
    meta = {"filing_status": "Single",
            "salary_history": [{"date": "2023-01-01", "amount": 120000}],
            "reconcile": [{"kind": "realized", "account_group": "Coinbase",
                           "date": "2024", "amount": 20000.00}]}
    txns = [{"date": "2024-03-01", "account_type": "Taxable",
             "account_group": "Coinbase", "symbol": "ETH-USD",
             "realized_gain": 80000, "holding_days": 100,
             "amount": 100000, "cost_basis": 20000}]
    e = _tax_rate_estimate("2024", meta, txns)
    assert e["realized_st"] == pytest.approx(20000.00)   # overridden, not 80000
    assert e["realized_lt"] == 0.0
    assert e["realized_override_accounts"] == ["Coinbase"]


def test_reconcile_realized_override_absent_uses_fin_figure():
    """Without a Reconcile Realized row, fin's own computed realized stands."""
    from src.analytics.tax import _tax_rate_estimate
    meta = {"filing_status": "Single",
            "salary_history": [{"date": "2023-01-01", "amount": 120000}]}
    txns = [{"date": "2024-03-01", "account_type": "Taxable",
             "account_group": "Coinbase", "symbol": "ETH-USD",
             "realized_gain": 80000, "holding_days": 100,
             "amount": 100000, "cost_basis": 20000}]
    e = _tax_rate_estimate("2024", meta, txns)
    assert e["realized_st"] == pytest.approx(80000)
    assert e["realized_override_accounts"] == []


def test_estimated_cap_gains_tax_federal_state_niit():
    from src.analytics.tax import _tax_rate_estimate
    meta = {"filing_status": "Single", "state_tax_rate": 0.093,
            "salary_history": [{"date": "2024-01-01", "amount": 250000}]}
    txns = [{"date": "2026-03-01", "account_type": "Taxable", "symbol": "AAPL",
             "realized_gain": 50000, "holding_days": 400,
             "amount": 100000, "cost_basis": 50000}]
    e = _tax_rate_estimate("2026", meta, txns)
    assert e["realized_lt"] == 50000.0
    assert e["est_cap_gains_tax_federal"] == 7500.0   # 50k × 15% LTCG
    assert e["est_cap_gains_tax_state"] == 4650.0      # 50k × 9.3%
    assert e["est_niit"] == 1900.0                      # 3.8% × 50k (above MAGI thresh)
    assert e["est_cap_gains_tax_total"] == 14050.0
    assert e["est_quarterly_payment"] == 3512.5


def test_estimated_cap_gains_tax_no_state_no_niit_low_income():
    from src.analytics.tax import _tax_rate_estimate
    meta = {"filing_status": "Single", "state_tax_rate": 0.0,
            "salary_history": [{"date": "2024-01-01", "amount": 40000}]}
    txns = [{"date": "2026-02-01", "account_type": "Taxable", "symbol": "X",
             "realized_gain": 2000, "holding_days": 100,
             "amount": 5000, "cost_basis": 3000}]
    e = _tax_rate_estimate("2026", meta, txns)
    assert e["est_cap_gains_tax_state"] == 0.0
    assert e["est_niit"] == 0.0   # AGI well under $200k threshold


def test_bracket_fill_top_room_left_is_none_not_inf():
    """The open-ended top bracket's room_left must be None, never inf —
    inf would serialize as an invalid JSON literal and leak Infinity into
    the dashboard (same class as the NaN price-cache bug)."""
    from src.analytics.tax import _tax_rate_estimate
    meta = {"filing_status": "Single",
            "salary_history": [{"date": "2024-01-01", "amount": 80000}]}
    e = _tax_rate_estimate("2025", meta, [])
    bf = e["bracket_fill"]
    assert bf[-1]["room_left"] is None, "top bracket room_left should be None"
    assert all(b["room_left"] is None or math.isfinite(b["room_left"]) for b in bf)
    json.dumps(e, allow_nan=False)   # whole estimate must be finite


def test_agi_excludes_retirement_account_income():
    """Dividends/interest inside a 401K / IRA are tax-deferred and never
    hit AGI — only taxable + savings account income counts.  Regression
    for MAGI being overstated right at the Roth phase-out edge."""
    from src.analytics.tax import _tax_rate_estimate
    meta = {"filing_status": "Single",
            "salary_history": [{"date": "2023-01-01", "amount": 100000}]}
    txns = [
        # Taxable dividend — counts.
        {"date": "2024-03-01", "account_type": "Taxable",
         "account_group": "Robinhood", "symbol": "USD",
         "action": "Dividend", "amount": 500.0},
        # Savings interest — taxable (1099-INT), counts.
        {"date": "2024-04-01", "account_type": "Savings",
         "account_group": "Apple Savings", "symbol": "USD",
         "action": "Interest", "amount": 200.0},
        # Retirement dividends — must NOT count.
        {"date": "2024-05-01", "account_type": "Retirement",
         "account_group": "Roth IRA", "symbol": "USD",
         "action": "Dividend", "amount": 3000.0},
        {"date": "2024-06-01", "account_type": "Retirement",
         "account_group": "Rollover IRA", "symbol": "FUND",
         "action": "Dividend", "amount": 1500.0},
    ]
    e = _tax_rate_estimate("2024", meta, txns)
    assert e["portfolio_income"] == pytest.approx(700.0)


def test_k401_deduction_excludes_employer_match_and_nets_reversals():
    """Only the employee elective deferral reduces W-2 wages; employer
    match doesn't.  Contribution Reversal rows net against the year's
    deferral instead of adding to it."""
    from src.analytics.tax import _tax_rate_estimate
    meta = {"filing_status": "Single",
            "salary_history": [{"date": "2023-01-01", "amount": 100000}]}
    txns = [
        {"date": "2024-02-01", "account_type": "Retirement",
         "account_group": "401K", "symbol": "FUND",
         "action": "Contribution", "amount": 10000.0,
         "description": "EMPLOYEE PRE-TAX"},
        {"date": "2024-02-01", "account_type": "Retirement",
         "account_group": "401K", "symbol": "FUND",
         "action": "Contribution", "amount": 4000.0,
         "description": "EMPLOYER MATCH"},
        {"date": "2024-03-01", "account_type": "Retirement",
         "account_group": "401K", "symbol": "FUND",
         "action": "Contribution Reversal", "amount": 1000.0,
         "description": "EMPLOYEE PRE-TAX"},
    ]
    e = _tax_rate_estimate("2024", meta, txns)
    assert e["k401"] == pytest.approx(9000.0)   # 10000 − 1000, no match


def test_lt_boundary_calendar_correct_across_leap_day():
    """A sale exactly on the one-year anniversary is SHORT-term, even
    when the window spans Feb 29 (366 days) — the naive days>365 test
    got this wrong."""
    from src.analytics.tax import _classify_realized
    txn = {
        "symbol": "AAPL", "realized_gain": 1000, "date": "2025-02-01",
        "lot_breakdown": [
            # Acquired 2024-02-01, sold 2025-02-01: 366 days elapsed
            # (2024 is a leap year) but exactly one year — short-term.
            {"date_acquired": "2024-02-01", "qty": 10,
             "cost_basis": 1000, "proceeds": 2000, "days": 366},
        ],
    }
    c = _classify_realized(txn)
    assert c["st"] == 1000.0
    assert c["lt"] == 0.0
    # One day later it flips to long-term.
    txn["date"] = "2025-02-02"
    txn["lot_breakdown"][0]["days"] = 367
    c = _classify_realized(txn)
    assert c["lt"] == 1000.0


def test_wash_sales_ignore_retirement_account_losses():
    """A loss sell inside an IRA is never deductible, so it can't be a
    wash sale worth flagging."""
    from src.analytics.tax import compute_tax_analytics
    txns = [
        {"date": "2024-03-01", "account_type": "Retirement",
         "account_group": "Roth IRA", "symbol": "VOO", "action": "Sell",
         "quantity": 5, "amount": 400, "realized_gain": -100.0,
         "cost_basis": 500},
        {"date": "2024-03-10", "account_type": "Retirement",
         "account_group": "Roth IRA", "symbol": "VOO", "action": "Buy",
         "quantity": 5, "amount": 420},
    ]
    out = compute_tax_analytics(txns, [], {})
    assert out["wash_sales"] == []


def test_2026_tables_present_with_obbba_2025_std_deduction():
    """2026 brackets exist (no silent fallback to 2025) and the 2025
    standard deduction reflects OBBBA's retroactive bump."""
    from src.analytics import tax as T
    assert (2026, "Single") in T._BRACKETS_BY_YEAR_STATUS
    assert T._BRACKETS_BY_YEAR_STATUS[(2026, "Single")][0] == (12400, 0.10)
    assert T._LTCG_BY_YEAR_STATUS[(2026, "Single")][0] == (49450, 0.00)
    assert T._STD_DED_BY_YEAR_STATUS[(2025, "Single")] == 15750
    assert T._STD_DED_BY_YEAR_STATUS[(2025, "Married Filing Jointly")] == 31500
    assert T._STD_DED_BY_YEAR_STATUS[(2026, "Single")] == 16100
    roth_mfj = T._ROTH_MAGI_PHASEOUT_BY_STATUS["Married Filing Jointly"]
    assert roth_mfj["start"][2026] == 242000


def test_tax_tables_embedded_in_export():
    """export_json must include tax_tables at the top level (the JS reads
    DATA.tax_tables)."""
    from src.export import export_json
    import tempfile, os
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "x.json"
        export_json([], out)
        data = json.loads(out.read_text(encoding="utf-8"))
    assert "tax_tables" in data
    assert "federal_brackets" in data["tax_tables"]
