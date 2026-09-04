"""The two cost-basis walkers must agree on what a cash position cost.

A Savings cash position has no lots, so neither walker can get its
basis from the lot queue — they each had to answer separately, and they
answered differently.  ``build_holdings`` used principal (so a HYSA's
accrued interest reads as unrealized gain, which is the whole point of
the convention); the snapshot walker used face value (unrealized always
zero).  Both described the same date.

It surfaced on the Performance tab, whose Unrealized card reads live
holdings for the lifetime window and the latest snapshot for every
other window: switching Lifetime → 3y moved a number that cannot move,
because unrealized is a level at the window END and every trailing
window ends on the same day.

Nothing caught it because ``_check_history_holdings_basis_parity``
skipped ``symbol == "USD"`` outright — the check passed by declining to
look at the only positions where the two conventions could differ.

Synthetic round numbers only.
"""

from __future__ import annotations

import pytest


def _cash_txn(date, action, amount, account="Apple Savings"):
    """A hand-maintained savings-account cash row, post-normalization."""
    return {
        "date": date,
        "account": account,
        "account_group": account,
        "account_type": "Savings",
        "symbol": "USD",
        "action": action,
        "raw_action": action,
        "quantity": amount,
        "price": 1.0,
        "fees": 0.0,
        "amount": amount,
        "description": "",
        "source": "apple-savings.csv",
    }


# Deposits 10,000; withdraws 2,000; earns 300 of interest and receives
# 100 of card cash back.  Balance 8,400, principal 8,100, gain 300.
LEDGER = [
    _cash_txn("2026-08-01", "Deposit", 10_000.0),
    _cash_txn("2026-08-05", "Withdrawal", 2_000.0),
    _cash_txn("2026-08-20", "Interest", 300.0),
    _cash_txn("2026-08-25", "Cash Back", 100.0),
]
EXPECTED_BALANCE = 8_400.0
EXPECTED_PRINCIPAL = 8_100.0
EXPECTED_GAIN = 300.0


@pytest.fixture
def fin(isolated_workdir):
    """Import after `isolated_workdir` so config resolves against tmp
    dirs, and type the account (ACCOUNT_TYPES ships empty and is filled
    from metadata.csv in production)."""
    from src.config import ACCOUNT_TYPES
    ACCOUNT_TYPES.update({"Apple Savings": "Savings", "Robinhood": "Taxable"})
    import src.history as H
    import src.pipeline_stages as PS
    return H, PS


class TestCashPrincipalEffect:
    """The per-txn rule both walkers call."""

    def test_a_deposit_is_principal(self, fin):
        _, PS = fin
        assert PS.cash_principal_effect(
            _cash_txn("2026-08-01", "Deposit", 10_000.0)) == 10_000.0

    def test_a_withdrawal_removes_principal(self, fin):
        _, PS = fin
        assert PS.cash_principal_effect(
            _cash_txn("2026-08-05", "Withdrawal", 2_000.0)) == -2_000.0

    def test_interest_is_not_principal(self, fin):
        """The account's own return.  Absorbing it into basis is what
        makes a HYSA read "gain: 0" forever."""
        _, PS = fin
        assert PS.cash_principal_effect(
            _cash_txn("2026-08-20", "Interest", 300.0)) == 0.0

    def test_cash_back_is_principal(self, fin):
        """The regression this rule was rewritten for.

        `Cash Back` is `cash_flow="in"` in the catalog, so
        `net_contributed` counts those dollars as contributed capital.
        The old literal Deposit/Withdrawal check did not, so the same
        dollars ALSO showed up as the account's unrealized gain —
        contributed capital and investment return at once.  Deriving
        from `txn_external_cash_flow` makes that impossible to
        re-introduce for any future action.
        """
        _, PS = fin
        assert PS.cash_principal_effect(
            _cash_txn("2026-08-25", "Cash Back", 100.0)) == 100.0

    def test_cash_outside_a_savings_account_is_not_principal(self, fin):
        """Broker cash balances are deliberately untracked; a Robinhood
        deposit must not conjure a cash basis figure."""
        _, PS = fin
        assert PS.cash_principal_effect(
            _cash_txn("2026-08-01", "Deposit", 500.0,
                      account="Robinhood")) == 0.0

    def test_a_security_is_not_cash_principal(self, fin):
        """Even in a Savings-typed account — a security's basis is lot
        basis.  Guards the same near-miss `build_holdings` guards."""
        _, PS = fin
        t = _cash_txn("2026-08-01", "Buy", 500.0)
        t["symbol"] = "VOO"
        assert PS.cash_principal_effect(t) == 0.0


class TestBothWalkersAgree:

    def test_snapshot_and_holdings_report_the_same_cash_basis(self, fin, stub_prices):
        """The bug itself: one date, one position, two answers."""
        H, PS = fin
        history = H.compute_history(LEDGER, {"USD": "Cash"})
        snap = history[-1]
        cash_rows = [p for p in snap["positions"]
                     if p["account_group"] == "Apple Savings"
                     and p["symbol"] == "USD"]
        assert len(cash_rows) == 1
        snap_basis = cash_rows[0]["cost_basis"]

        holdings_by_account, _ = PS.build_holdings(
            balances={("Apple Savings", "USD"): EXPECTED_BALANCE},
            last_prices={"USD": 1.0},
            fifo_basis_by_key={},
            cash_principal_by_key=PS.compute_cash_principal(LEDGER),
        )
        holdings_basis = holdings_by_account[0]["cost_basis"]

        assert snap_basis == holdings_basis == EXPECTED_PRINCIPAL, (
            "the snapshot walker and the holdings table must use one cash "
            "basis convention — the Performance tab reads holdings for the "
            "lifetime window and this snapshot for every other window"
        )

    def test_the_agreed_figure_is_principal_not_face_value(self, fin, stub_prices):
        """Pins the DIRECTION of the fix.  Making both walkers use face
        value would also make them agree, and would silently delete the
        HYSA's entire reported return."""
        H, _ = fin
        snap = H.compute_history(LEDGER, {"USD": "Cash"})[-1]
        row = next(p for p in snap["positions"] if p["symbol"] == "USD")
        assert row["value"] == EXPECTED_BALANCE
        assert row["cost_basis"] == EXPECTED_PRINCIPAL
        assert row["value"] - row["cost_basis"] == EXPECTED_GAIN, (
            "a savings account's accrued interest is its unrealized gain"
        )

    def test_cash_basis_is_walked_as_of_each_snapshot(self, fin, stub_prices):
        """Not a final total stamped onto every date: a snapshot taken
        before the interest posted must show no gain."""
        H, _ = fin
        history = H.compute_history(LEDGER, {"USD": "Cash"})
        early = next(h for h in history if h["date"] == "2026-08-15")
        row = next(p for p in early["positions"] if p["symbol"] == "USD")
        assert row["value"] == 8_000.0
        assert row["cost_basis"] == 8_000.0, (
            "principal must be walked incrementally — a snapshot before "
            "any interest posted has no gain"
        )

    def test_snapshot_rollup_matches_the_position_rows(self, fin, stub_prices):
        """total_cost_basis feeds the Overview chart's Cost Basis line."""
        H, _ = fin
        snap = H.compute_history(LEDGER, {"USD": "Cash"})[-1]
        assert snap["total_cost_basis"] == pytest.approx(
            sum(p["cost_basis"] for p in snap["positions"]), abs=0.01)
        assert snap["cost_basis_by_group"]["Apple Savings"] == EXPECTED_PRINCIPAL


class TestTheParityCheckLooksAtCash:
    """The USD exemption is gone and must stay gone.

    An exemption that hides the only class of position where two
    conventions can disagree is worse than no check at all: it reports
    green over exactly the gap it was written to cover.
    """

    def test_a_cash_basis_disagreement_is_flagged(self, fin):
        from src.analytics.data_health import _check_history_holdings_basis_parity
        history = [{
            "date": "2026-08-31",
            "positions": [{"account_group": "Apple Savings", "symbol": "USD",
                           "quantity": 8_400.0, "price": 1.0,
                           "value": 8_400.0, "cost_basis": 8_400.0}],
        }]
        holdings = [{"account_group": "Apple Savings", "account_type": "Savings",
                     "symbol": "USD", "quantity": 8_400.0, "price": 1.0,
                     "value": 8_400.0, "cost_basis": 8_100.0,
                     "unrealized_gain": 300.0}]
        flags = _check_history_holdings_basis_parity(history, holdings)
        assert flags, (
            "the parity check is exempting cash again — this is the exact "
            "shape of the bug it failed to catch"
        )
        assert flags[0]["severity"] == "high"

    def test_agreeing_cash_is_not_flagged(self, fin):
        from src.analytics.data_health import _check_history_holdings_basis_parity
        history = [{
            "date": "2026-08-31",
            "positions": [{"account_group": "Apple Savings", "symbol": "USD",
                           "quantity": 8_400.0, "price": 1.0,
                           "value": 8_400.0, "cost_basis": 8_100.0}],
        }]
        holdings = [{"account_group": "Apple Savings", "account_type": "Savings",
                     "symbol": "USD", "quantity": 8_400.0, "price": 1.0,
                     "value": 8_400.0, "cost_basis": 8_100.0,
                     "unrealized_gain": 300.0}]
        assert _check_history_holdings_basis_parity(history, holdings) == []

    def test_a_bridged_groups_synthetic_cash_row_is_not_flagged(self, fin):
        """Bridged groups (Coinbase / Robinhood) get a synthetic USD
        snapshot position at face value, and `inject_into_holdings` gives
        the holdings row the same face-value basis — so they agree.  A
        snapshot-only row must not flag either: the check iterates
        holdings, so a position with no holdings counterpart is skipped.
        """
        from src.analytics.data_health import _check_history_holdings_basis_parity
        history = [{
            "date": "2026-08-31",
            "positions": [{"account_group": "Robinhood", "symbol": "USD",
                           "quantity": 1_500.0, "price": 1.0,
                           "value": 1_500.0, "cost_basis": 1_500.0}],
        }]
        assert _check_history_holdings_basis_parity(history, []) == []
