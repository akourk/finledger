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


def test_withholding_reduces_take_home_but_not_taxes():
    """Extra federal withholding is cash out of the paycheck but a
    PREPAYMENT — it must reduce take-home without inflating the tax
    totals."""
    from src.analytics.paycheck import compute_paycheck
    base = compute_paycheck(_meta(), {"k401": 0.0})
    meta = _meta()
    meta["paycheck_deductions"].append(
        {"label": "Extra Withholding", "kind": "withholding",
         "amount": 200.0, "date": ""})
    out = compute_paycheck(meta, {"k401": 0.0})
    assert out["withholding_annual"] == pytest.approx(200.0 * 26)
    assert out["taxes_annual"] == base["taxes_annual"]          # unchanged
    assert out["total_tax_annual"] == base["total_tax_annual"]  # unchanged
    assert out["take_home_annual"] == pytest.approx(
        base["take_home_annual"] - 200.0 * 26)


def test_withholding_credits_estimated_cap_gains_tax():
    """tax.py nets extra withholding off the estimated tax on realized
    gains before suggesting a quarterly payment."""
    from datetime import date
    from src.analytics.tax import _tax_rate_estimate
    cur = date.today().year
    meta = _meta(paycheck_deductions=[
        {"label": "Extra Withholding", "kind": "withholding",
         "amount": 200.0, "date": ""},
    ])
    # A short-term realized gain in a taxable account this year.
    txns = [{"date": f"{cur}-02-01", "symbol": "XYZ", "action": "Sell",
             "account_group": "Robinhood", "account_type": "Taxable",
             "amount": 30000.0, "realized_gain": 20000.0,
             "holding_days": 100}]
    est = _tax_rate_estimate(str(cur), meta, txns)
    assert est["extra_withholding"] == pytest.approx(200.0 * 26)
    assert est["est_tax_after_withholding"] == pytest.approx(
        max(0.0, est["est_cap_gains_tax_total"] - 5200.0), abs=0.02)
    assert est["est_quarterly_payment"] == pytest.approx(
        est["est_tax_after_withholding"] / 4, abs=0.02)


def test_metadata_parses_withholding_kind(tmp_path):
    from src.metadata import parse_metadata
    (tmp_path / "metadata.csv").write_text(
        "Type,Date,Amount,Symbol,Note\n"
        "Paycheck Deduction,,200,Withholding,Extra Federal Withholding\n",
        encoding="utf-8")
    meta = parse_metadata(tmp_path)
    assert meta["paycheck_deductions"][0]["kind"] == "withholding"


def test_safe_harbor_covered_and_shortfall():
    from datetime import date
    from src.analytics.tax import _tax_rate_estimate
    cur = date.today().year
    # Prior-year AGI below $150k → 100% prong.
    meta = _meta(tax_returns={str(cur - 1): {"total_tax": 10000.0,
                                             "agi": 120000.0}})
    est = _tax_rate_estimate(str(cur), meta, [])
    sh = est["safe_harbor"]
    assert sh is not None
    assert sh["threshold_pct"] == 1.00
    assert sh["prior_year_prong"] == pytest.approx(10000.0)
    # W-4 proxy on a 104k salary comfortably exceeds min(10k, 90% prong).
    assert sh["projected_withholding"] == pytest.approx(
        sh["w4_withholding_est"])          # no extra withholding rows here?
    # (this meta HAS no withholding rows → extra = 0)
    assert sh["extra_withholding"] == 0.0
    assert sh["effective_target"] == pytest.approx(
        min(sh["prior_year_prong"], sh["ninety_pct_prong"]))
    assert sh["covered"] == (sh["shortfall"] == 0.0)

    # Prior-year AGI above $150k → 110% prong; huge prior tax → shortfall.
    meta2 = _meta(tax_returns={str(cur - 1): {"total_tax": 60000.0,
                                              "agi": 200000.0}})
    est2 = _tax_rate_estimate(str(cur), meta2, [])
    sh2 = est2["safe_harbor"]
    assert sh2["threshold_pct"] == 1.10
    assert sh2["prior_year_prong"] == pytest.approx(66000.0)
    # Effective target = LESSER prong — a giant prior-year bill never
    # forces overpaying past 90% of this year's tax.
    assert sh2["effective_target"] == pytest.approx(
        min(66000.0, sh2["ninety_pct_prong"]))


def test_safe_harbor_absent_without_prior_return():
    from datetime import date
    from src.analytics.tax import _tax_rate_estimate
    cur = date.today().year
    est = _tax_rate_estimate(str(cur), _meta(), [])
    assert est["safe_harbor"] is None
    # Past years never carry a safe-harbor block.
    meta = _meta(tax_returns={str(cur - 2): {"total_tax": 9000.0}})
    est_prev = _tax_rate_estimate(str(cur - 1), meta, [])
    assert est_prev.get("safe_harbor") is None


def test_metadata_parses_tax_return_rows(tmp_path):
    from src.metadata import parse_metadata
    (tmp_path / "metadata.csv").write_text(
        "Type,Date,Amount,Symbol,Note\n"
        "Tax Return,2025,12000,Total Tax,1040 line 24\n"
        "Tax Return,2025,95000,AGI,1040 line 11\n"
        "Tax Return,2025,9000,Withholding,1040 line 25d\n"
        "Tax Return,2024,11000,Total Tax,1040 line 24\n"
        "Tax Return,2025,999,Nonsense Field,ignored\n",
        encoding="utf-8")
    meta = parse_metadata(tmp_path)
    tr = meta["tax_returns"]
    assert tr["2025"]["total_tax"] == 12000.0
    assert tr["2025"]["agi"] == 95000.0
    assert tr["2025"]["withholding"] == 9000.0
    assert tr["2024"]["total_tax"] == 11000.0
    assert "nonsense_field" not in tr["2025"]


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
        "Paycheck Deduction,,100.00,Pre-Tax,Medical Premium\n"
        "Paycheck Deduction,,-5.00,Pre-Tax,Wellness Incentive\n"
        "Paycheck Deduction,,250.00,Tax,OASDI\n"
        "Paycheck Deduction,,10.00,Post-Tax,Long-Term Disability\n"
        "Paycheck Deduction,,5.00,Bogus Kind,Ignored row\n"
        "Pay Frequency,,26,,Biweekly\n",
        encoding="utf-8")
    meta = parse_metadata(tmp_path)
    rows = {r["label"]: r for r in meta["paycheck_deductions"]}
    assert rows["Medical Premium"]["kind"] == "pretax"
    assert rows["Wellness Incentive"]["amount"] == -5.00
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


def _sell(year, st_gain=0.0, lt_gain=0.0):
    """One taxable sell per bucket for _tax_rate_estimate tests."""
    out = []
    if st_gain:
        out.append({"date": f"{year}-03-01", "symbol": "AAA", "action": "Sell",
                    "account_group": "Robinhood", "account_type": "Taxable",
                    "amount": abs(st_gain) * 2, "realized_gain": st_gain,
                    "holding_days": 100})
    if lt_gain:
        out.append({"date": f"{year}-03-02", "symbol": "BBB", "action": "Sell",
                    "account_group": "Robinhood", "account_type": "Taxable",
                    "amount": abs(lt_gain) * 2, "realized_gain": lt_gain,
                    "holding_days": 700})
    return out


def test_capital_loss_capped_at_3000_for_agi():
    """A big net realized loss deducts at most $3,000 against ordinary
    income — it must not sink AGI by the full loss."""
    from datetime import date
    from src.analytics.tax import _tax_rate_estimate
    cur = date.today().year
    est = _tax_rate_estimate(str(cur), _meta(),
                             _sell(cur, st_gain=-200.0, lt_gain=-9800.0))
    # Raw figures preserved for display…
    assert est["realized_st"] == pytest.approx(-200.0)
    assert est["realized_lt"] == pytest.approx(-9800.0)
    # …but AGI only drops by the capped $3,000.
    assert est["realized_st_agi"] + est["realized_lt_agi"] == pytest.approx(-3000.0)
    assert est["capital_loss_disallowed"] == pytest.approx(10000.0 - 3000.0)
    assert est["agi"] == pytest.approx(104000.0 - 2340.0 - 3000.0)
    # A net loss owes no capital-gains tax (was negative before the fix).
    assert est["est_cap_gains_tax_total"] == 0.0


def test_capital_loss_cross_netting():
    """An ST loss offsets an LT gain before any cap; small net losses
    (≤ $3,000) pass through uncapped."""
    from datetime import date
    from src.analytics.tax import _tax_rate_estimate
    cur = date.today().year
    # ST −5k vs LT +8k → net +3k, all LT for tax purposes.
    est = _tax_rate_estimate(str(cur), _meta(),
                             _sell(cur, st_gain=-5000.0, lt_gain=8000.0))
    assert est["realized_st_agi"] == pytest.approx(0.0)
    assert est["realized_lt_agi"] == pytest.approx(3000.0)
    assert est["capital_loss_disallowed"] == 0.0
    assert est["est_cap_gains_tax_total"] > 0
    # Small net loss: no cap.
    est2 = _tax_rate_estimate(str(cur), _meta(),
                              _sell(cur, st_gain=-1200.0))
    assert est2["realized_st_agi"] == pytest.approx(-1200.0)
    assert est2["capital_loss_disallowed"] == 0.0


def test_safe_harbor_lt_loss_does_not_reduce_projected_tax():
    """A net LT capital LOSS must not subtract loss × LTCG-rate from the
    safe-harbor's projected federal tax.  The projection's LTCG term
    uses the netted, floored-at-zero LT gain (same value as the
    estimated-cap-gains figure) — the raw realized_lt once let a −$40k
    loss knock $6k off the 90% prong and falsely report "covered"."""
    from datetime import date
    from src.analytics.tax import _tax_rate_estimate
    cur = date.today().year
    meta = _meta(tax_returns={str(cur - 1): {"total_tax": 12000.0,
                                             "agi": 100000.0}})
    lt_loss = [{
        "date": f"{cur}-02-01", "symbol": "XYZ", "action": "Sell",
        "account_group": "Robinhood", "account_type": "Taxable",
        "quantity": 100, "amount": 10000.0, "realized_gain": -40000.0,
        "cost_basis": 50000.0,
        "lot_breakdown": [{
            "date_acquired": f"{cur - 3}-01-15", "qty": 100,
            "cost_basis": 50000.0, "proceeds": 10000.0, "days": 1100,
        }],
    }]
    base = _tax_rate_estimate(str(cur), meta, [])
    with_loss = _tax_rate_estimate(str(cur), meta, lt_loss)
    sh_base, sh_loss = base["safe_harbor"], with_loss["safe_harbor"]
    # The loss legitimately lowers ordinary tax a little (the −$3k
    # capped deduction flows through the netted ST/LT AGI values), but
    # never by anything close to loss × LTCG rate.
    assert sh_loss["est_total_federal_tax"] >= sh_base["est_total_federal_tax"] - 3000 * 0.24
    # And a positive-LT year still projects MORE tax than the base.
    lt_gain = [dict(lt_loss[0], amount=90000.0, realized_gain=40000.0,
                    lot_breakdown=[{
                        "date_acquired": f"{cur - 3}-01-15", "qty": 100,
                        "cost_basis": 50000.0, "proceeds": 90000.0,
                        "days": 1100}])]
    with_gain = _tax_rate_estimate(str(cur), meta, lt_gain)
    assert (with_gain["safe_harbor"]["est_total_federal_tax"]
            > sh_base["est_total_federal_tax"])
