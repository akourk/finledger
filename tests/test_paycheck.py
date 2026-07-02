"""Paycheck breakdown (analytics/paycheck.py), `Paycheck Deduction` /
`Pay Frequency` metadata parsing, and the pre-tax AGI adjustment in
analytics/tax.py."""

from __future__ import annotations

import pytest


def _meta(**over) -> dict:
    m = {
        "salary_history": [{"date": "2024-01-01", "amount": 104000.0,
                            "note": ""}],
        "bonus_history": [],
        "filing_status": "Single",
        "pay_frequency": 26,
        "paycheck_deductions": [
            {"label": "Medical", "kind": "pretax", "amount": 100.0, "date": ""},
            {"label": "Wellness credit", "kind": "pretax", "amount": -10.0,
             "date": ""},
            {"label": "OASDI", "kind": "tax", "amount": 245.0, "date": ""},
            {"label": "Medicare", "kind": "tax", "amount": 57.0, "date": ""},
            {"label": "LTD", "kind": "posttax", "amount": 12.0, "date": ""},
        ],
    }
    m.update(over)
    return m


def test_paycheck_breakdown_math():
    from src.analytics.paycheck import compute_paycheck
    out = compute_paycheck(_meta(), {"k401": 13000.0, "is_projection": True})
    assert out is not None
    assert out["frequency"] == 26
    assert out["gross_per_paycheck"] == pytest.approx(4000.0)
    # Pre-tax nets the credit: (100 − 10) × 26
    assert out["pretax_annual"] == pytest.approx(2340.0)
    assert out["taxes_annual"] == pytest.approx((245.0 + 57.0) * 26)
    assert out["posttax_annual"] == pytest.approx(12.0 * 26)
    assert out["k401_annual"] == 13000.0
    # Take-home = salary − pretax − 401k − est fed − payroll − posttax
    expected_th = (104000.0 - 2340.0 - 13000.0
                   - out["est_federal_tax_annual"]
                   - out["taxes_annual"] - out["posttax_annual"])
    assert out["take_home_annual"] == pytest.approx(expected_th, abs=0.02)
    assert out["take_home_per_paycheck"] == pytest.approx(expected_th / 26,
                                                          abs=0.02)
    assert out["est_federal_tax_annual"] > 0
    assert out["is_projection"] is True


def test_paycheck_hidden_without_rows_or_salary():
    from src.analytics.paycheck import compute_paycheck
    assert compute_paycheck(_meta(paycheck_deductions=[]), None) is None
    assert compute_paycheck(_meta(salary_history=[]), None) is None
    assert compute_paycheck(None, None) is None


def test_federal_tax_on_brackets():
    from src.analytics.paycheck import _federal_tax_on
    brackets = [(10000.0, 0.10), (40000.0, 0.20), (float("inf"), 0.30)]
    assert _federal_tax_on(0.0, brackets) == 0.0
    assert _federal_tax_on(10000.0, brackets) == pytest.approx(1000.0)
    # 10k @ 10% + 30k @ 20% + 10k @ 30%
    assert _federal_tax_on(50000.0, brackets) == pytest.approx(
        1000.0 + 6000.0 + 3000.0)


def test_metadata_parses_paycheck_rows(tmp_path):
    from src.metadata import parse_metadata
    (tmp_path / "metadata.csv").write_text(
        "Type,Date,Amount,Symbol,Note\n"
        "Paycheck Deduction,,110.00,Pre-Tax,Medical/PPO Before-Tax\n"
        "Paycheck Deduction,,-7.50,Pre-Tax,Wellness Incentive\n"
        "Paycheck Deduction,,300.00,Tax,OASDI\n"
        "Paycheck Deduction,,13.99,Post-Tax,Long-Term Disability\n"
        "Paycheck Deduction,,5.00,Bogus Kind,Ignored row\n"
        "Pay Frequency,,26,,Biweekly\n",
        encoding="utf-8")
    meta = parse_metadata(tmp_path)
    rows = {r["label"]: r for r in meta["paycheck_deductions"]}
    assert rows["Medical/PPO Before-Tax"]["kind"] == "pretax"
    assert rows["Wellness Incentive"]["amount"] == -7.50
    assert rows["OASDI"]["kind"] == "tax"
    assert rows["Long-Term Disability"]["kind"] == "posttax"
    assert "Ignored row" not in rows          # unknown kind dropped
    assert meta["pay_frequency"] == 26


def test_metadata_pay_frequency_snaps_to_standard(tmp_path):
    from src.metadata import parse_metadata
    (tmp_path / "metadata.csv").write_text(
        "Type,Date,Amount,Symbol,Note\n"
        "Pay Frequency,,27,,typo\n",
        encoding="utf-8")
    meta = parse_metadata(tmp_path)
    assert meta["pay_frequency"] == 26        # default kept, typo rejected


def test_budget_6mo_cadence(tmp_path):
    from src.metadata import parse_metadata
    from src.analytics.budget import compute_budget
    (tmp_path / "metadata.csv").write_text(
        "Type,Date,Amount,Symbol,Note\n"
        "Budget,,1010,Insurance,Car insurance @6mo\n"
        "Budget,,189,Insurance,Renters insurance @semiannual\n",
        encoding="utf-8")
    meta = parse_metadata(tmp_path)
    b = compute_budget(meta["budget"])
    by_label = {r["label"]: r for r in b["rows"]}
    assert by_label["Car insurance"]["cadence"] == "6mo"
    assert by_label["Car insurance"]["monthly"] == pytest.approx(1010 / 6, abs=0.01)
    assert by_label["Renters insurance"]["cadence"] == "6mo"


def test_pretax_deductions_reduce_agi_current_year_only():
    """Undated pre-tax rows apply from the CURRENT year onward — past
    years' MAGI / Roth eligibility must not shift."""
    from datetime import date
    from src.analytics.tax import _tax_rate_estimate
    meta = _meta()
    cur = date.today().year

    est_cur = _tax_rate_estimate(str(cur), meta, [])
    # (100 − 10) × 26 = 2340 off wages → AGI
    assert est_cur["pretax_deductions"] == pytest.approx(2340.0)
    assert est_cur["agi"] == pytest.approx(104000.0 - 2340.0)
    assert est_cur["salary"] == pytest.approx(104000.0)   # display stays gross

    est_prev = _tax_rate_estimate(str(cur - 1), meta, [])
    assert est_prev["pretax_deductions"] == 0.0
    assert est_prev["agi"] == pytest.approx(104000.0)

    # A DATED row applies from its year onward.
    meta2 = _meta(paycheck_deductions=[
        {"label": "Medical", "kind": "pretax", "amount": 100.0,
         "date": f"{cur - 2}-01-01"},
    ])
    est_prev2 = _tax_rate_estimate(str(cur - 1), meta2, [])
    assert est_prev2["pretax_deductions"] == pytest.approx(2600.0)


def test_active_paycheck_deductions_supersede_by_label():
    from datetime import date
    from src.analytics._shared import active_paycheck_deductions
    cur = date.today().year
    meta = {"paycheck_deductions": [
        {"label": "Medical", "kind": "pretax", "amount": 80.0,
         "date": f"{cur - 3}-01-01"},
        {"label": "Medical", "kind": "pretax", "amount": 100.0,
         "date": f"{cur - 1}-01-01"},
    ]}
    rows = active_paycheck_deductions(meta, cur)
    assert len(rows) == 1
    assert rows[0]["amount"] == 100.0
    # As of two years ago, the older amount was active.
    rows_old = active_paycheck_deductions(meta, cur - 2)
    assert rows_old[0]["amount"] == 80.0
