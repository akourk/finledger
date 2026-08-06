"""Balance anchors, and the two refusals that keep them honest.

An anchor states the real statement balance for a hand-maintained CASH
account; `balance_anchor.py` walks fin's own USD balance to that date
and books ONE `Cash Back` row for the difference. The motivating case is
Apple Card Daily Cash, which lands in Apple Savings in small irregular
amounts that never reach the hand-kept CSV, so fin drifts low forever.

The mechanism is a **plug**, which makes its two refusals the important
part — and refusals are exactly the direction this audit repeatedly
found untested:

* **Savings accounts only.** For cash, a delta is exact arithmetic and
  unambiguously means missing transactions. On a securities account it
  could equally be a pricing error, a missing split or a basis bug, and
  plugging that would paper over precisely what this codebase exists to
  surface.
* **Positive deltas only.** fin holding MORE than the statement means
  fin has transactions the account does not — a bug to investigate, not
  a gap to fill.

`tests/test_balance_anchor.py` covers the happy path; this module covers
the refusals, the re-anchoring arithmetic, and the
not-income property.

Synthetic round numbers only.
"""

from __future__ import annotations

import pytest


def _dep(date: str, amount: float, group="Apple Savings",
         acct_type="Savings") -> dict:
    return {"date": date, "account": group, "account_group": group,
            "account_type": acct_type, "symbol": "USD", "action": "Deposit",
            "quantity": amount, "price": 1.0, "fees": 0.0, "amount": amount,
            "description": "", "source": "t.csv"}


def _anchor(date: str, amount: float, group="Apple Savings") -> dict:
    return {"date": date, "amount": amount, "account_group": group,
            "note": "statement"}


@pytest.fixture
def apply(isolated_workdir):
    from src.config import ACCOUNT_GROUPS, ACCOUNT_TYPES
    from src.balance_anchor import apply_balance_anchors

    ACCOUNT_GROUPS.update({"Apple Savings": "Apple Savings",
                           "Robinhood": "Robinhood"})
    ACCOUNT_TYPES.update({"Apple Savings": "Savings", "Robinhood": "Taxable"})
    return lambda txns, anchors: apply_balance_anchors(
        txns, anchors, verbose=False)


def _cash_back(txns):
    return [t for t in txns if t.get("action") == "Cash Back"]


class TestTheHappyPath:

    def test_a_positive_drift_books_one_cash_back_row(self, apply):
        """1,000 in the ledger, 1,050 on the statement -> book 50."""
        out = apply([_dep("2024-01-05", 1000.0)], [_anchor("2024-06-30", 1050.0)])
        rows = _cash_back(out)
        assert len(rows) == 1
        assert rows[0]["amount"] == pytest.approx(50.0)
        assert rows[0]["date"] == "2024-06-30"
        assert rows[0]["account_group"] == "Apple Savings"

    def test_an_exact_match_books_nothing(self, apply):
        """Near-miss: no drift, no plug."""
        out = apply([_dep("2024-01-05", 1000.0)], [_anchor("2024-06-30", 1000.0)])
        assert _cash_back(out) == []

    def test_a_sub_cent_drift_is_ignored(self, apply):
        """Float noise is not Daily Cash."""
        out = apply([_dep("2024-01-05", 1000.0)],
                    [_anchor("2024-06-30", 1000.005)])
        assert _cash_back(out) == []

    def test_only_activity_up_to_the_anchor_date_counts(self, apply):
        """A deposit AFTER the statement date must not shrink the drift —
        the anchor describes the balance on its own date."""
        out = apply([_dep("2024-01-05", 1000.0), _dep("2024-12-01", 500.0)],
                    [_anchor("2024-06-30", 1050.0)])
        assert _cash_back(out)[0]["amount"] == pytest.approx(50.0)


class TestTheRefusals:
    """The half that makes the plug safe."""

    def test_a_negative_drift_is_refused(self, apply):
        """fin holding MORE than the statement means fin has transactions
        the account does not. That is a bug to investigate; plugging it
        would erase the evidence."""
        out = apply([_dep("2024-01-05", 1000.0)], [_anchor("2024-06-30", 900.0)])
        assert _cash_back(out) == [], (
            "a negative delta was plugged — fin being ABOVE the statement "
            "is a bug, not a gap"
        )

    def test_a_securities_account_is_refused(self, apply):
        """On a securities account a delta could be a pricing error, a
        missing split or a basis bug. Plugging it papers over exactly
        what this codebase works to surface."""
        out = apply([_dep("2024-01-05", 1000.0, group="Robinhood",
                          acct_type="Taxable")],
                    [_anchor("2024-06-30", 1050.0, group="Robinhood")])
        assert _cash_back(out) == [], (
            "a non-Savings account was anchored — cash is the only place a "
            "delta unambiguously means missing transactions"
        )

    def test_an_unknown_account_group_is_refused(self, apply):
        out = apply([_dep("2024-01-05", 1000.0)],
                    [_anchor("2024-06-30", 1050.0, group="Nonexistent")])
        assert _cash_back(out) == []


class TestReAnchoring:
    """Anchors process chronologically and each sees the earlier ones'
    synthesized rows, so a later anchor books only the NEW drift."""

    def test_a_second_anchor_books_only_the_new_drift(self, apply):
        out = apply(
            [_dep("2024-01-05", 1000.0)],
            [_anchor("2024-06-30", 1050.0), _anchor("2024-12-31", 1075.0)])
        rows = sorted(_cash_back(out), key=lambda t: t["date"])
        assert len(rows) == 2
        assert rows[0]["amount"] == pytest.approx(50.0)
        assert rows[1]["amount"] == pytest.approx(25.0), (
            "the second anchor re-booked drift the first already covered — "
            "the running balance must include synthesized rows"
        )
        assert sum(r["amount"] for r in rows) == pytest.approx(75.0), (
            "total booked must equal the total drift, not double-count it"
        )

    def test_anchors_are_processed_in_date_order(self, apply):
        """Order of the metadata rows must not change the result."""
        forward = apply([_dep("2024-01-05", 1000.0)],
                        [_anchor("2024-06-30", 1050.0),
                         _anchor("2024-12-31", 1075.0)])
        reverse = apply([_dep("2024-01-05", 1000.0)],
                        [_anchor("2024-12-31", 1075.0),
                         _anchor("2024-06-30", 1050.0)])
        assert sorted(t["amount"] for t in _cash_back(forward)) == \
            sorted(t["amount"] for t in _cash_back(reverse))


class TestCashBackIsNotIncome:
    """Card cash back is a purchase REBATE arriving from outside the
    portfolio. Booking it as income would put it on the Income tab and in
    AGI; booking it as return would overstate the account's performance.
    It is `cash_flow="in"` and nothing else."""

    def test_cash_back_is_a_cash_inflow(self, isolated_workdir):
        from src.actions import names_with_cash_flow

        assert "Cash Back" in names_with_cash_flow("in")

    def test_cash_back_is_not_in_any_income_bucket(self, isolated_workdir):
        from src.actions import INCOME_ACTIONS

        assert "Cash Back" not in INCOME_ACTIONS, (
            "Cash Back reached the income set — it is a rebate, not "
            "taxable income, and must never reach the Income tab or AGI"
        )
