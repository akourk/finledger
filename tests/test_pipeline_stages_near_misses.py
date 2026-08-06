"""Near-miss coverage for `build_holdings`' cost-basis source rule.

Found by mutation: `sym in CASH_SYMBOLS and ACCOUNT_TYPES.get(acct) ==
"Savings"` could be flipped to `or` with the full suite green.

That condition picks where a holding's cost basis COMES FROM — cash
principal (deposits − withdrawals) for savings cash, the FIFO walker for
everything else. Under `or`, a security held in an account the user has
typed as `Savings` takes cash-principal basis instead of its real lot
basis, and its unrealized P&L is wrong by the whole difference.

`Account Type` is user-configurable via `metadata.csv`, so a
`Savings`-typed account holding a non-cash symbol is a reachable
configuration, not a hypothetical. The same rule at two other sites in
this module is already protected; this site was not.

Synthetic round numbers only.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def stages(isolated_workdir):
    """Import after `isolated_workdir` so module-level path/config state
    resolves against the tmp dirs, and seed the account maps this rule
    reads (they ship empty and are filled from metadata.csv)."""
    from src.config import ACCOUNT_TYPES
    from src import pipeline_stages as PS

    ACCOUNT_TYPES.update({"Apple Savings": "Savings", "Robinhood": "Taxable"})
    return PS


class TestCostBasisSourceSelection:

    def test_savings_cash_uses_cash_principal(self, stages):
        holdings_by_account, _ = stages.build_holdings(
            balances={("Apple Savings", "USD"): 5_000.0},
            last_prices={"USD": 1.0},
            fifo_basis_by_key={},
            cash_principal_by_key={("Apple Savings", "USD"): 4_000.0},
        )
        row = holdings_by_account[0]
        assert row["cost_basis"] == 4_000.0, (
            "savings cash basis is contributed principal, not market value"
        )

    def test_security_in_a_savings_typed_account_uses_the_fifo_walker(self, stages):
        """The near-miss that the `and` exists for.

        Both basis sources are populated with DIFFERENT values, so the
        assertion can only pass if the correct branch was taken — if
        either input were absent, `or` and `and` would agree and the test
        would prove nothing.
        """
        holdings_by_account, _ = stages.build_holdings(
            balances={("Apple Savings", "VOO"): 10.0},
            last_prices={"VOO": 500.0},
            fifo_basis_by_key={("Apple Savings", "VOO"): 3_000.0},
            cash_principal_by_key={("Apple Savings", "VOO"): 9_999.0},
        )
        row = holdings_by_account[0]
        assert row["cost_basis"] == 3_000.0, (
            "a security's basis must come from the lot walker even when its "
            "account is typed Savings — cash principal is not lot basis"
        )
        assert row["unrealized_gain"] == 2_000.0

    def test_cash_outside_a_savings_account_has_no_basis(self, stages):
        """USD in a non-Savings account is deliberately untracked (broker
        cash balances are incomplete), so it must report None rather than
        a fabricated zero."""
        holdings_by_account, _ = stages.build_holdings(
            balances={("Robinhood", "USD"): 1_000.0},
            last_prices={"USD": 1.0},
            fifo_basis_by_key={},
            cash_principal_by_key={("Robinhood", "USD"): 7_777.0},
        )
        row = holdings_by_account[0]
        assert row["cost_basis"] is None, (
            "non-savings cash must not borrow the cash-principal figure"
        )
        assert row["unrealized_gain"] is None

    def test_ordinary_taxable_security_uses_the_fifo_walker(self, stages):
        holdings_by_account, _ = stages.build_holdings(
            balances={("Robinhood", "VOO"): 10.0},
            last_prices={"VOO": 500.0},
            fifo_basis_by_key={("Robinhood", "VOO"): 3_000.0},
            cash_principal_by_key={},
        )
        assert holdings_by_account[0]["cost_basis"] == 3_000.0
