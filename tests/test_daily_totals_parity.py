"""`compute_daily_totals` must agree with `compute_history`.

Segment 3. `compute_daily_totals` is fin's **third** valuation
implementation, after `basis._walk` and `history`'s snapshot walker, and
its docstring makes the parity claim explicitly:

    Valuation mirrors the snapshot walker exactly:
      - balance walk skips USD outside Savings
      - cache-priced: as-of-date qty x split_factor_since x close
      - unpriceable: most recent txn price at or before the day
      - option contracts x contract_multiplier

Nothing tested that claim. It is the same shape as F-015 (the Split rule
duplicated across two walkers) and F-025 (`_value_at_date`'s fallback
diverging from history) — both of which turned out to be real
divergences on at least one path.

This one carries weight because the daily walker is what upgrades the
**drawdown headline stats** to daily resolution. If it drifts, Max
Drawdown and its peak/trough window are computed on a different
portfolio than the chart beside them shows.

Mutation found two of its rules unprotected: the first-date selection
(`min` over transaction dates) and the USD-outside-Savings skip.

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
    # Savings cash is tracked; broker cash is skipped by BOTH walkers.
    _txn("2024-01-05", "Deposit", "USD", 5000.0, 1.0, 5000.0,
         group="Savings", acct_type="Savings"),
    _txn("2024-03-01", "Deposit", "USD", 900.0, 1.0, 900.0),
    # A NEUTRAL action carrying a real quantity.  Both walkers must
    # treat it as a no-op; if either stops skipping it, the 7 units
    # land in the balance and the totals diverge.  Without a row like
    # this in the fixture the rule is untested for lack of INPUT rather
    # than lack of assertion — the distinction Segment 1 established.
    _txn("2024-02-10", "Neutral", "AAA", 7.0, 100.0, 700.0),
]

DATES = ["2024-01-31", "2024-02-29", "2024-03-31", "2024-04-30", "2024-05-31"]
PRICES = {
    "AAA": dict(zip(DATES, [105.0, 108.0, 125.0, 130.0, 140.0])),
    "BBB": dict(zip(DATES, [50.0, 52.0, 55.0, 60.0, 58.0])),
}


@pytest.fixture
def seeded(stub_prices):
    from src.config import ACCOUNT_GROUPS, ACCOUNT_TYPES
    from src.prices import ensure_coverage
    from datetime import datetime

    for sym, series in PRICES.items():
        stub_prices.set(sym, series)
        stub_prices.set_sector(sym, "Technology")
        ensure_coverage([sym],
                        datetime.strptime(min(series), "%Y-%m-%d").date(),
                        datetime.strptime(max(series), "%Y-%m-%d").date())
    ACCOUNT_GROUPS.update({"Broker": "Broker", "Savings": "Savings"})
    ACCOUNT_TYPES.update({"Broker": "Taxable", "Savings": "Savings"})
    return stub_prices


class TestDailyTotalsMatchSnapshots:

    def _run(self):
        from src.history import compute_daily_totals, compute_history

        txns = [dict(t) for t in LEDGER]
        snaps = compute_history(txns, {"AAA": 140.0, "BBB": 58.0, "USD": 1.0})
        daily = dict(compute_daily_totals([dict(t) for t in LEDGER]))
        return snaps, daily

    def test_agrees_at_every_snapshot_date(self, seeded):
        """The docstring's claim, asserted at every date rather than one."""
        snaps, daily = self._run()
        assert len(snaps) >= 4, "fixture produced too few snapshots to matter"

        mismatches = []
        for s in snaps:
            d = s["date"][:10]
            if d not in daily:
                mismatches.append(f"{d}: absent from the daily series")
                continue
            if abs(daily[d] - s["total"]) > 0.05:
                mismatches.append(
                    f"{d}: snapshot={s['total']:.2f} daily={daily[d]:.2f}")
        assert not mismatches, (
            "the daily walker disagrees with the snapshot walker, so the "
            "drawdown headline stats describe a different portfolio than "
            "the chart beside them:\n  " + "\n  ".join(mismatches)
        )

    def test_the_comparison_is_not_vacuous(self, seeded):
        snaps, daily = self._run()
        assert snaps[-1]["total"] > 0
        assert daily[snaps[-1]["date"][:10]] > 0

    def test_series_starts_at_the_first_transaction(self, seeded):
        """`first = min(date)` — mutation showed this unprotected.

        Using max instead would start the walk at the LAST transaction
        and silently produce a near-empty series, which degrades the
        drawdown stats to nothing while still returning a valid-looking
        list.
        """
        _snaps, daily = self._run()
        first = min(t["date"] for t in LEDGER)
        assert first in daily, (
            f"daily series does not reach the first transaction ({first})"
        )
        assert len(daily) > 300, (
            f"series has only {len(daily)} days — it should cover every "
            "calendar day from the first transaction to today"
        )

    def test_savings_cash_counts_but_broker_cash_does_not(self, seeded):
        """The USD-outside-Savings rule, which mutation showed
        unprotected in this walker.

        The ledger deposits 5,000 into Savings and 900 into Broker. Only
        the Savings cash may be valued — broker cash balances are
        incomplete in the source CSVs, so tracking them would be worse
        than not.
        """
        snaps, daily = self._run()
        d = snaps[-1]["date"][:10]
        by_group = snaps[-1].get("by_account_group") or {}

        assert by_group.get("Savings") == pytest.approx(5000.0, abs=0.05)
        # The 900 broker deposit must appear in NEITHER walker's total.
        assert daily[d] == pytest.approx(snaps[-1]["total"], abs=0.05)
        assert daily[d] < 5000.0 + 900.0 + by_group.get("Broker", 0.0)

    def test_every_calendar_day_is_present(self, seeded):
        """Daily resolution is the whole point — a gap would let an
        intra-month trough go unseen, which is what the snapshot cadence
        already does."""
        from datetime import date, timedelta

        _snaps, daily = self._run()
        days = sorted(daily)
        d0 = date.fromisoformat(days[0])
        d1 = date.fromisoformat(days[-1])
        assert len(daily) == (d1 - d0).days + 1, (
            "the daily series has gaps; drawdown depth would be understated"
        )
