"""Wash-sale detection — Segment 7 item 5.

IRS Publication 550: selling at a loss and acquiring substantially
identical stock within 30 days **before or after** the sale defers the
loss. fin surfaces these as "Potential Wash Sales" on the Tax tab.

Mutation found only one of five rules protected — the retirement-account
exclusion. The ±30-day window boundaries, the loss-only guard and the
same-day exclusion could all be changed with the suite green, so each is
pinned here from both sides of its boundary.

Two deliberate limitations are pinned as behaviour rather than asserted
as correct, so that changing either is a decision rather than an
accident. Both are documented in AUDIT.md (F-022):

* fin matches on **exact symbol**, making no substantially-identical
  judgement — selling SPY and buying VOO is not flagged.
* a repurchase on the **sale's own date** is excluded.

Synthetic round numbers only.
"""

from __future__ import annotations

import pytest

SELL_DATE = "2024-06-15"


def _txn(**kw) -> dict:
    base = {
        "date": SELL_DATE, "account": "acct", "account_group": "Robinhood",
        "account_type": "Taxable", "symbol": "SYM", "action": "Sell",
        "quantity": 1.0, "price": 100.0, "fees": 0.0, "amount": 100.0,
        "description": "", "source": "t.csv",
    }
    base.update(kw)
    return base


def _loss_sale(**kw) -> dict:
    d = {"realized_gain": -500.0, "holding_days": 40}
    d.update(kw)
    return _txn(**d)


def _buy(date: str, **kw) -> dict:
    d = {"date": date, "action": "Buy"}
    d.update(kw)
    return _txn(**d)


@pytest.fixture
def wash(isolated_workdir):
    from src.analytics.tax import compute_tax_analytics

    def run(txns, meta=None):
        out = compute_tax_analytics(txns, meta or {}, [])
        return out["wash_sales"]
    return run


class TestWindowBoundaries:
    """30 days before through 30 days after, inclusive."""

    @pytest.mark.parametrize("buy_date,flagged", [
        ("2024-05-16", True),    # exactly 30 days before -> inside
        ("2024-05-15", False),   # 31 days before -> outside
        ("2024-07-15", True),    # exactly 30 days after -> inside
        ("2024-07-16", False),   # 31 days after -> outside
        ("2024-06-01", True),    # comfortably inside, before
        ("2024-06-30", True),    # comfortably inside, after
    ])
    def test_boundary(self, wash, buy_date, flagged):
        rows = wash([_loss_sale(), _buy(buy_date)])
        assert bool(rows) is flagged, (
            f"buy on {buy_date} vs sale on {SELL_DATE}: "
            f"expected flagged={flagged}"
        )
        if flagged:
            assert rows[0]["offending_buy_date"] == buy_date
            assert rows[0]["symbol"] == "SYM"
            assert rows[0]["loss"] == -500.0


class TestOnlyLossesInTaxableAccounts:

    def test_a_gain_is_never_a_wash_sale(self, wash):
        """The rule defers LOSSES. A profitable sale followed by a
        repurchase is just re-entering a position."""
        assert wash([_loss_sale(realized_gain=500.0), _buy("2024-06-20")]) == []

    def test_a_zero_gain_sale_is_not_flagged(self, wash):
        assert wash([_loss_sale(realized_gain=0.0), _buy("2024-06-20")]) == []

    def test_loss_inside_a_retirement_account_is_not_flagged(self, wash):
        """A loss in an IRA was never deductible, so deferring it is
        meaningless — flagging it would be pure noise."""
        rows = wash([_loss_sale(account_group="Roth IRA",
                                account_type="Retirement"),
                     _buy("2024-06-20")])
        assert rows == []

    def test_a_non_sell_action_is_not_flagged(self, wash):
        rows = wash([_loss_sale(action="Transfer Out"), _buy("2024-06-20")])
        assert rows == []


class TestCrossAccountAndActionCoverage:
    """The IRS applies the rule across ALL your accounts, including
    IRAs — a repurchase in an IRA still triggers it."""

    def test_repurchase_in_a_different_taxable_account_counts(self, wash):
        rows = wash([_loss_sale(), _buy("2024-06-20", account_group="Coinbase")])
        assert rows, "a repurchase in another account must still trigger"

    def test_repurchase_inside_an_ira_counts(self, wash):
        rows = wash([_loss_sale(),
                     _buy("2024-06-20", account_group="Roth IRA",
                          account_type="Retirement")])
        assert rows, (
            "the IRS applies the wash rule across accounts — an IRA "
            "repurchase after a taxable loss still defers the loss"
        )

    @pytest.mark.parametrize("action", ["Buy", "Reinvest", "Contribution"])
    def test_each_acquiring_action_counts_as_a_repurchase(self, wash, action):
        """A reinvested dividend is a purchase — a classic accidental
        wash sale."""
        rows = wash([_loss_sale(), _buy("2024-06-20", action=action)])
        assert rows, f"{action} should count as an acquiring transaction"

    def test_a_different_symbol_does_not_trigger(self, wash):
        assert wash([_loss_sale(), _buy("2024-06-20", symbol="OTHER")]) == []


class TestDocumentedLimitations:
    """Pinned as BEHAVIOUR, not endorsed as correct. See F-022."""

    def test_same_day_repurchase_is_not_flagged(self, wash):
        """A buy on the sale's own date is excluded.

        Strictly, the IRS window includes the sale date, so a genuine
        same-day repurchase IS a wash sale. The exclusion exists because
        fin matches on date alone and cannot tell a same-day repurchase
        from the acquisition of the very lot being sold. It errs toward
        fewer false positives on an advisory panel.

        Pinned so that changing it is a decision, not an accident.
        """
        assert wash([_loss_sale(), _buy(SELL_DATE)]) == []

    def test_no_substantially_identical_judgement_is_made(self, wash):
        """fin matches EXACT symbols only.

        Selling SPY at a loss and buying VOO the next day is arguably a
        wash sale under the substantially-identical test; fin will not
        flag it. Encoding that judgement would require a similarity model
        the IRS itself declines to specify.
        """
        rows = wash([_loss_sale(symbol="SPY"), _buy("2024-06-16", symbol="VOO")])
        assert rows == [], (
            "fin deliberately makes no substantially-identical judgement — "
            "if that changed, this test should be rewritten, not deleted"
        )
