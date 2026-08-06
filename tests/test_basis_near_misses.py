"""Near-miss tests for basis-layer guards, found by mutation testing.

Both gaps below are the same shape, and it is the shape this audit keeps
finding: the existing tests prove a rule **fires when it should** and
never prove it **stays off when it shouldn't**. A condition guarding the
"off" direction can then be deleted with the suite still green.

Found by `tools/mutate.py` against `src/basis.py`; each test here was
verified to FAIL under the mutation that motivated it.

Synthetic round numbers only — never real portfolio figures.
"""

from __future__ import annotations

from src.basis import _consume_lots_reserving, txn_external_cash_flow


class TestCashFlowCarveOutNearMisses:
    """`txn_external_cash_flow` is the single source of truth for
    cash-flow accounting, and CLAUDE.md documents it as pinned by
    `test_actions_catalog.py::test_external_cash_flow_helper_handles_all_carve_outs`.

    It is pinned — in one direction. That test covers marker + right
    account (fires) and non-marker + right account (silent), but never
    marker + WRONG account. So the `account_group` half of each carve-out
    condition could be deleted and the suite would stay green, while
    every marker-bearing row in any account silently became external
    money in — inflating net_contributed, and with it TWR, the SPY
    benchmark comparison, savings rate and FIRE projections.
    """

    def test_contribution_marker_outside_a_roth_account_is_not_a_cash_flow(self):
        """The USAA carve-out reads contribution markers off Buy rows.
        It must not do so for other brokers' Buy rows, whose descriptions
        are uncontrolled free text."""
        assert txn_external_cash_flow({
            "action": "Buy", "amount": 3000.0, "account_group": "Robinhood",
            "description": "PRIOR YEAR CONTRIBUTION",
        }) == 0.0
        assert txn_external_cash_flow({
            "action": "Buy", "amount": 3000.0, "account_group": "Coinbase",
            "description": "CURRENT YEAR CONTRIBUTION",
        }) == 0.0

    def test_contribution_marker_on_a_non_buy_roth_row_is_not_a_cash_flow(self):
        assert txn_external_cash_flow({
            "action": "Sell", "amount": 3000.0, "account_group": "Roth IRA",
            "description": "PRIOR YEAR CONTRIBUTION",
        }) == 0.0

    def test_coinbase_funding_heuristic_does_not_apply_to_other_brokers(self):
        assert txn_external_cash_flow({
            "action": "Buy", "amount": 1000.0, "account_group": "Robinhood",
            "description": "Bought 0.5 BTC using Bank Account Chase ****1234",
        }) == 0.0

    def test_coinbase_buy_without_a_funding_marker_is_not_external_money(self):
        """The heuristic needs BOTH "bought" and "using": a description
        naming no funding source tells us nothing about where the money
        came from, and guessing "external" invents contributed capital.

        This is the assertion that stops `and` becoming `or` — the mutant
        that turns every unmarked Coinbase Buy into a deposit.
        """
        assert txn_external_cash_flow({
            "action": "Buy", "amount": 1000.0, "account_group": "Coinbase",
            "description": "Bought 0.5 BTC",
        }) == 0.0
        assert txn_external_cash_flow({
            "action": "Buy", "amount": 1000.0, "account_group": "Coinbase",
            "description": "",
        }) == 0.0

    def test_coinbase_usd_wallet_buy_stays_internal(self):
        """Cash already in the user's USD wallet is not new money."""
        assert txn_external_cash_flow({
            "action": "Buy", "amount": 1000.0, "account_group": "Coinbase",
            "description": "Bought 0.5 BTC using USD Wallet",
        }) == 0.0

    def test_the_positive_cases_still_hold(self):
        """Near-miss tests are only meaningful next to the fire case."""
        assert txn_external_cash_flow({
            "action": "Buy", "amount": 3000.0, "account_group": "Roth IRA",
            "description": "PRIOR YEAR CONTRIBUTION",
        }) == 3000.0
        assert txn_external_cash_flow({
            "action": "Buy", "amount": 1000.0, "account_group": "Coinbase",
            "description": "Bought 0.5 BTC using Chase ****1234",
        }) == 1000.0


def _lot(date: str, qty: float, per_share: float) -> dict:
    return {"date": date, "qty": qty, "basis_per_share": per_share,
            "origin": "reconstructed"}


class TestReservingConsumeRemovesLotsInDescendingIndexOrder:
    """`_consume_lots_capped` (reached via `_consume_lots_reserving`)
    deletes emptied lots by index, which is only correct descending —
    popping ascending shifts every later index by one and removes the
    wrong lot.

    Its sibling loop in `_consume_lots` is protected; this one was not.
    No existing test consumed TWO OR MORE lots to exhaustion in a single
    reserving call, so `reverse=True` could be flipped with the suite
    green — silently leaving the wrong lot in the pool, which changes
    cost basis, realized gain and holding period on every later sale.
    """

    def test_two_fully_consumed_lots_leave_the_correct_survivor(self):
        lots = [
            _lot("2021-01-01", 1.0, 10.0),
            _lot("2022-01-01", 1.0, 20.0),
            _lot("2023-01-01", 1.0, 30.0),
        ]
        # Reserve the newest lot so the capped pass can only take the
        # first two — that's what routes us through _consume_lots_capped
        # rather than the plain _consume_lots delegation.
        reserved = {"2023-01-01": 1.0}

        basis_removed, carried = _consume_lots_reserving(
            lots, 2.0, "fifo", reserved)

        assert basis_removed == 30.0, "should relieve the 10.0 and 20.0 lots"
        assert sum(c["qty"] for c in carried) == 2.0
        assert len(lots) == 1, "two emptied lots should have been removed"
        assert lots[0]["date"] == "2023-01-01", (
            "the RESERVED lot must be the survivor — a wrong survivor here "
            "means emptied lots were popped in ascending index order"
        )
        assert lots[0]["basis_per_share"] == 30.0

    def test_three_fully_consumed_lots_empty_the_pool(self):
        lots = [
            _lot("2021-01-01", 1.0, 10.0),
            _lot("2022-01-01", 1.0, 20.0),
            _lot("2023-01-01", 1.0, 30.0),
            _lot("2024-01-01", 1.0, 40.0),
        ]
        reserved = {"2024-01-01": 1.0}

        basis_removed, _carried = _consume_lots_reserving(
            lots, 3.0, "fifo", reserved)

        assert basis_removed == 60.0
        assert [l["date"] for l in lots] == ["2024-01-01"]

    def test_partial_consumption_leaves_the_lot_in_place(self):
        """Near-miss: a lot that is only partly consumed must NOT be
        removed, or the remaining shares lose their basis entirely."""
        lots = [_lot("2021-01-01", 5.0, 10.0), _lot("2022-01-01", 5.0, 20.0)]
        reserved = {"2022-01-01": 5.0}

        _basis, _carried = _consume_lots_reserving(lots, 2.0, "fifo", reserved)

        assert len(lots) == 2
        assert lots[0]["qty"] == 3.0
