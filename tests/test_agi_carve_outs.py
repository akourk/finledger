"""AGI / MAGI build-up carve-outs — Segment 7 item 3.

`_tax_rate_estimate` builds AGI from salary, bonuses, portfolio income,
realized gains and the 401(k) deduction. CLAUDE.md documents several
carve-outs in that build-up, and AGI feeds Roth eligibility, the
marginal-rate display and the estimated capital-gains tax — so a wrong
carve-out is a wrong tax figure, not a cosmetic one.

Existing tests drive `_tax_rate_estimate` mostly with an EMPTY txn list,
which exercises the salary/paycheck side and leaves the txn-driven
carve-outs unprotected. Mutation confirmed it: the retirement-income
exclusion and the employer-match filter were caught, but three rules
were not, and each is pinned here.

Every fixture supplies BOTH sides of the rule — a row that must count
and a near-identical row that must not — so a test can only pass if the
condition is actually doing work.

Synthetic round numbers only.
"""

from __future__ import annotations

import pytest

SALARY = 100_000.0


def _meta(**over) -> dict:
    m = {
        "salary_history": [{"date": "2020-01-01", "amount": SALARY, "note": ""}],
        "bonus_history": [],
        "filing_status": "Single",
        "pay_frequency": 26,
        "paycheck_deductions": [],
    }
    m.update(over)
    return m


def _txn(**kw) -> dict:
    base = {
        "date": "2024-05-01", "account": "acct", "account_group": "Robinhood",
        "account_type": "Taxable", "symbol": "SYM", "action": "Dividend",
        "quantity": 0.0, "price": 0.0, "fees": 0.0, "amount": 0.0,
        "description": "", "source": "t.csv",
    }
    base.update(kw)
    return base


@pytest.fixture
def estimate(isolated_workdir):
    from src.analytics.tax import _tax_rate_estimate
    return lambda txns, meta=None: _tax_rate_estimate(
        "2024", meta or _meta(), txns)


class TestRetirementIncomeIsExcluded:
    """Dividends inside a 401K / IRA are tax-deferred and never hit AGI.
    Savings-account interest IS taxable and stays in."""

    def test_taxable_dividend_raises_agi(self, estimate):
        base = estimate([])["agi"]
        with_div = estimate([_txn(amount=5_000.0)])["agi"]
        assert with_div == pytest.approx(base + 5_000.0)

    def test_retirement_dividend_does_not(self, estimate):
        base = estimate([])["agi"]
        got = estimate([_txn(amount=5_000.0, account_group="Roth IRA",
                             account_type="Retirement")])["agi"]
        assert got == pytest.approx(base), (
            "a dividend inside a retirement wrapper reached AGI — that "
            "overstates MAGI right at the Roth phase-out edge"
        )

    def test_savings_interest_still_counts(self, estimate):
        base = estimate([])["agi"]
        got = estimate([_txn(action="Interest", amount=1_000.0,
                             account_group="Apple Savings",
                             account_type="Savings")])["agi"]
        assert got == pytest.approx(base + 1_000.0), (
            "savings interest is 1099-INT income and must reach AGI"
        )


class TestRealizedGainsAreTaxableAccountsOnly:
    """Selling inside an IRA realizes nothing taxable. Mutation showed
    this restriction was unprotected."""

    def _sell(self, **kw) -> dict:
        d = {"action": "Sell", "realized_gain": 10_000.0, "holding_days": 30}
        d.update(kw)
        return _txn(**d)

    def test_taxable_realized_gain_reaches_the_estimate(self, estimate):
        est = estimate([self._sell()])
        assert est["realized_st"] + est["realized_lt"] == pytest.approx(10_000.0)

    def test_retirement_realized_gain_does_not(self, estimate):
        est = estimate([self._sell(account_group="Roth IRA",
                                   account_type="Retirement")])
        assert est["realized_st"] + est["realized_lt"] == pytest.approx(0.0), (
            "a gain realized inside a retirement account entered the tax "
            "estimate — those are not taxable events"
        )

    def test_savings_realized_gain_does_not(self, estimate):
        est = estimate([self._sell(account_group="Apple Savings",
                                   account_type="Savings")])
        assert est["realized_st"] + est["realized_lt"] == pytest.approx(0.0)


class TestK401DeductionScope:
    """Only an employee elective deferral into 401K / Rollover IRA
    reduces W-2 wages.

    The sharpest near-miss: a **Roth IRA** contribution is post-tax and
    must NEVER be deducted. Mutation showed the account_group scope was
    unprotected, and deducting it would understate AGI — which then feeds
    the Roth eligibility calculation that the contribution belongs to.
    """

    def _contrib(self, **kw) -> dict:
        d = {"action": "Contribution", "amount": 10_000.0,
             "account_group": "401K", "account_type": "Retirement",
             "symbol": "FUND"}
        d.update(kw)
        return _txn(**d)

    def test_401k_contribution_reduces_agi(self, estimate):
        base = estimate([])["agi"]
        got = estimate([self._contrib()])["agi"]
        assert got == pytest.approx(base - 10_000.0)

    def test_rollover_ira_contribution_reduces_agi(self, estimate):
        base = estimate([])["agi"]
        got = estimate([self._contrib(account_group="Rollover IRA")])["agi"]
        assert got == pytest.approx(base - 10_000.0)

    def test_roth_ira_contribution_does_not_reduce_agi(self, estimate):
        base = estimate([])["agi"]
        got = estimate([self._contrib(account_group="Roth IRA")])["agi"]
        assert got == pytest.approx(base), (
            "a Roth contribution was deducted from AGI — Roth money is "
            "POST-tax, and understating AGI here corrupts the very Roth "
            "eligibility figure it feeds"
        )

    def test_employer_match_is_not_deducted(self, estimate):
        base = estimate([])["agi"]
        got = estimate([self._contrib(description="Employer Match")])["agi"]
        assert got == pytest.approx(base), (
            "employer match is not an employee deferral and never reduces "
            "W-2 wages"
        )

    def test_contribution_reversal_nets_out(self, estimate):
        """A reversal carries a negative amount and must cancel the
        original, or the deduction outlives the contribution."""
        both = estimate([
            self._contrib(),
            self._contrib(action="Contribution Reversal"),
        ])["agi"]
        assert both == pytest.approx(estimate([])["agi"])


class TestPretaxDeductionClamp:
    """Section 125 premiums reduce W-2 wages, but never below zero and
    never by more than the salary itself."""

    def test_pretax_deductions_reduce_wages(self, estimate):
        # Dated rows: an UNDATED Paycheck Deduction applies only from the
        # current year onward (_shared.active_paycheck_deductions), so an
        # undated fixture silently contributes nothing to a past year.
        meta = _meta(paycheck_deductions=[
            {"label": "Medical", "kind": "pretax", "amount": 100.0, "date": "2020-01-01"},
        ])
        est = estimate([], meta)
        assert est["pretax_deductions"] == pytest.approx(100.0 * 26)
        assert est["agi"] == pytest.approx(SALARY - 100.0 * 26)

    def test_absurd_pretax_deduction_cannot_exceed_salary(self, estimate):
        """Near-miss for the clamp, which mutation showed unprotected.
        Without it a fat-fingered row produces NEGATIVE wages and an AGI
        below zero, which then picks a nonsense tax bracket."""
        meta = _meta(paycheck_deductions=[
            {"label": "Typo", "kind": "pretax", "amount": 999_999.0, "date": "2020-01-01"},
        ])
        est = estimate([], meta)
        assert est["pretax_deductions"] <= SALARY
        assert est["agi"] >= 0.0, "AGI went negative"

    def test_negative_pretax_total_is_floored_at_zero(self, estimate):
        """A credit-only deduction set (e.g. a wellness refund with no
        premiums) must not INCREASE wages."""
        meta = _meta(paycheck_deductions=[
            {"label": "Wellness credit", "kind": "pretax", "amount": -50.0,
             "date": "2020-01-01"},
        ])
        est = estimate([], meta)
        assert est["pretax_deductions"] == pytest.approx(0.0)
        assert est["agi"] == pytest.approx(SALARY)
