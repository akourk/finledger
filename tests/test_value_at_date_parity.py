"""`_value_at_date` must agree with `history.compute_history`.

Segment 5 item 4. CLAUDE.md states the claim directly:

    ``_value_at_date`` applies the same valuation rules as
    ``history.compute_history`` (USD-skipping, split adjustment,
    option-intrinsic floor, reconstructed broker cash), and was verified
    against every snapshot date to agree within float-summation noise.

Nothing tested it. That is the same shape as F-015 — a documented parity
between two valuation implementations with no test pinning it — and this
pair matters more than most, because `_value_at_date` is load-bearing
for **two** features at once:

* the reconciliation panel, which walks the ledger to a broker
  statement's exact date (snapping to a snapshot was the 2026-08-05 bug);
* the daily-TWR path, which is the headline performance figure.

If the two drift, reconciliation starts reporting breaks that are really
just a second opinion about valuation — and the audit's most
consequential technique is measured with an uncalibrated instrument.

Synthetic round numbers only.
"""

from __future__ import annotations

import pytest


def _txn(date, action, symbol, qty, price, amount, group="Broker",
         acct_type="Taxable") -> dict:
    return {"date": date, "account": group, "account_group": group,
            "account_type": acct_type, "symbol": symbol, "action": action,
            "quantity": qty, "price": price, "fees": 0.0, "amount": amount,
            "description": "", "source": "t.csv"}


LEDGER = [
    _txn("2024-01-10", "Buy", "AAA", 10.0, 100.0, 1000.0),
    _txn("2024-02-05", "Buy", "BBB", 20.0, 50.0, 1000.0),
    _txn("2024-03-20", "Buy", "AAA", 5.0, 120.0, 600.0),
    _txn("2024-04-15", "Sell", "BBB", 8.0, 60.0, 480.0),
    # A second account, so the group filter has something to bite on.
    _txn("2024-02-20", "Buy", "AAA", 3.0, 110.0, 330.0, group="Other"),
    # Savings cash: tracked. Non-savings USD is skipped by BOTH walkers,
    # so include one of each to pin that rule on the shared path.
    _txn("2024-01-05", "Deposit", "USD", 5000.0, 1.0, 5000.0,
         group="Savings", acct_type="Savings"),
    _txn("2024-03-01", "Deposit", "USD", 900.0, 1.0, 900.0),
]

PRICES = {
    "AAA": {"2024-01-31": 105.0, "2024-02-29": 108.0, "2024-03-31": 125.0,
            "2024-04-30": 130.0, "2024-05-31": 140.0},
    "BBB": {"2024-01-31": 50.0, "2024-02-29": 52.0, "2024-03-31": 55.0,
            "2024-04-30": 60.0, "2024-05-31": 58.0},
}


@pytest.fixture
def priced(isolated_workdir, stub_prices):
    for sym, series in PRICES.items():
        stub_prices.set(sym, series)
        stub_prices.set_sector(sym, "Technology")
    from src.config import ACCOUNT_GROUPS, ACCOUNT_TYPES
    ACCOUNT_GROUPS.update({"Broker": "Broker", "Other": "Other",
                           "Savings": "Savings"})
    ACCOUNT_TYPES.update({"Broker": "Taxable", "Other": "Taxable",
                          "Savings": "Savings"})
    return stub_prices


class TestValueAtDateMatchesHistory:

    def _run(self):
        from src.analytics._shared import _balance_sort_key, _value_at_date
        from src.history import compute_history

        txns = [dict(t) for t in LEDGER]
        last = {"AAA": 140.0, "BBB": 58.0, "USD": 1.0}
        snaps = compute_history(txns, last)
        ordered = sorted(txns, key=_balance_sort_key)
        return snaps, ordered, _value_at_date

    def test_agrees_at_every_snapshot_date(self, priced):
        """The claim, asserted at every date rather than one."""
        snaps, ordered, value_at = self._run()
        assert len(snaps) >= 4, "fixture produced too few snapshots to matter"

        mismatches = []
        for s in snaps:
            got = value_at(ordered, s["date"][:10], None, [], None)
            if abs(got - s["total"]) > 0.05:
                mismatches.append(
                    f"{s['date'][:10]}: history={s['total']:.2f} "
                    f"value_at_date={got:.2f}")
        assert not mismatches, (
            "the two valuation paths disagree — reconciliation would report "
            "breaks that are really a second opinion:\n  "
            + "\n  ".join(mismatches)
        )

    def test_the_comparison_is_not_vacuous(self, priced):
        """Both sides must be non-zero, or agreement proves nothing."""
        snaps, ordered, value_at = self._run()
        last = snaps[-1]
        assert last["total"] > 0
        assert value_at(ordered, last["date"][:10], None, [], None) > 0

    def test_agrees_under_an_account_group_filter(self, priced):
        """Reconciliation always filters to ONE account group, so the
        filtered path is the one it actually uses."""
        snaps, ordered, value_at = self._run()
        for s in snaps:
            d = s["date"][:10]
            expected = (s.get("by_account_group") or {}).get("Broker", 0.0)
            got = value_at(ordered, d, frozenset({"Broker"}), [], None)
            assert got == pytest.approx(expected, abs=0.05), (
                f"{d}: by_account_group['Broker']={expected:.2f} but "
                f"_value_at_date filtered={got:.2f}"
            )

    def test_savings_cash_counts_and_other_cash_does_not(self, priced):
        """Both walkers skip USD outside Savings — broker cash balances
        are incomplete, so tracking them would be worse than not."""
        snaps, ordered, value_at = self._run()
        d = snaps[-1]["date"][:10]

        savings = value_at(ordered, d, frozenset({"Savings"}), [], None)
        assert savings == pytest.approx(5000.0, abs=0.05)

        # The Broker account received a USD deposit that must NOT be valued.
        broker = value_at(ordered, d, frozenset({"Broker"}), [], None)
        assert broker == pytest.approx(
            (snaps[-1].get("by_account_group") or {}).get("Broker", 0.0),
            abs=0.05)

    def test_a_date_before_any_activity_is_zero(self, priced):
        _snaps, ordered, value_at = self._run()
        assert value_at(ordered, "2023-01-01", None, [], None) == pytest.approx(0.0)
