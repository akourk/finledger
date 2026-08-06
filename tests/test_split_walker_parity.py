"""The two lot walkers must handle a Split identically.

CLAUDE.md declares the invariant: `history.py` keeps its own inline lot
walker mirroring `basis._walk`, and basis-rule changes have TWICE landed
in one without the other. The `history_holdings_basis_parity` data-health
check pins the two together — **but only for ledgers the tests actually
run**, and no test contained a `Split`. `audit/sample-coverage.md`
confirms the sample can't reach one either: no held symbol splits after
the sample's start date.

So the split rule existed in two places, verbatim, with neither copy
executed by anything:

* `basis._apply_split_to_lots` — a shared helper, never called in any
  test run (it was on the never-executed list).
* `history.py`'s split branch — the same arithmetic re-implemented
  inline rather than calling that helper.

They agree today; this module is what makes that a checked fact instead
of a coincidence. If either drifts, `test_split_basis_agrees_across_walkers`
fails.

Synthetic round numbers only.
"""

from __future__ import annotations

import pytest


# --------------------------------------------------------------------------
# The shared helper, in isolation
# --------------------------------------------------------------------------

class TestApplySplitToLots:
    """`basis._apply_split_to_lots` had zero executed lines."""

    def test_forward_split_scales_quantity_and_preserves_total_basis(self):
        from src.basis import _apply_split_to_lots

        # 10 shares at $100 = $1,000 basis; a 2-for-1 adds 10 shares.
        lots = [{"date": "2021-01-01", "qty": 10.0, "basis_per_share": 100.0}]
        _apply_split_to_lots(lots, old_total_qty=10.0, added_qty=10.0)

        assert lots[0]["qty"] == 20.0
        assert lots[0]["basis_per_share"] == 50.0
        assert lots[0]["qty"] * lots[0]["basis_per_share"] == 1000.0, (
            "a split creates no basis and destroys none — only the "
            "per-share denominator changes"
        )

    def test_basis_is_preserved_across_multiple_lots(self):
        from src.basis import _apply_split_to_lots

        lots = [
            {"date": "2021-01-01", "qty": 10.0, "basis_per_share": 100.0},
            {"date": "2022-01-01", "qty": 30.0, "basis_per_share": 50.0},
        ]
        before = sum(l["qty"] * l["basis_per_share"] for l in lots)
        _apply_split_to_lots(lots, old_total_qty=40.0, added_qty=40.0)
        after = sum(l["qty"] * l["basis_per_share"] for l in lots)

        assert after == pytest.approx(before)
        assert [l["qty"] for l in lots] == [20.0, 60.0]

    def test_acquired_dates_are_untouched(self):
        """Holding period survives a split — the shares are treated as
        acquired when the original lot was, which decides ST vs LT."""
        from src.basis import _apply_split_to_lots

        lots = [{"date": "2021-03-04", "qty": 10.0, "basis_per_share": 100.0}]
        _apply_split_to_lots(lots, 10.0, 10.0)
        assert lots[0]["date"] == "2021-03-04"

    @pytest.mark.parametrize("old_total,added", [
        (0.0, 10.0),     # no existing position
        (10.0, 0.0),     # no shares added
        (-5.0, 10.0),    # negative pool (orphan corp-action artifact)
        (10.0, -5.0),
    ])
    def test_degenerate_inputs_are_a_no_op(self, old_total, added):
        """Near-miss: the guard must leave lots ALONE rather than divide
        by zero or invert basis."""
        from src.basis import _apply_split_to_lots

        lots = [{"date": "2021-01-01", "qty": 10.0, "basis_per_share": 100.0}]
        _apply_split_to_lots(lots, old_total, added)
        assert lots == [{"date": "2021-01-01", "qty": 10.0,
                         "basis_per_share": 100.0}]


# --------------------------------------------------------------------------
# The two walkers, on the same ledger
# --------------------------------------------------------------------------

def _row(date, action, qty, price, amount, **kw) -> dict:
    return {"date": date, "account": "Robinhood",
            "account_group": "Robinhood", "account_type": "Taxable",
            "symbol": "SPLT", "action": action, "quantity": qty,
            "price": price, "fees": 0.0, "amount": amount,
            "description": kw.get("description", action), "source": "t.csv"}


def _split_ledger() -> list[dict]:
    """Buy 10 @ $100 · 2-for-1 split (+10) · sell half.

    The trailing SELL is load-bearing and was missing from the first
    version of this file, which made the whole parity test vacuous —
    the history mutation survived it.

    Why: a split preserves TOTAL basis by construction, and the snapshot
    quantity comes from the balance walker, not the lot walker. So
    "quantity == 20 and basis == 1000" holds whether or not the lot-level
    rescale ever ran. It only becomes observable when lots are CONSUMED,
    because then the amount of basis relieved depends on the rescaled
    per-share figure:

        rescaled   : 20 lots @ $50  -> selling 10 relieves $500, $500 left
        NOT rescaled: 10 lots @ $100 -> selling 10 relieves $1,000, $0 left
    """
    return [
        _row("2021-01-04", "Buy",   10.0, 100.0, 1000.0),
        _row("2022-06-15", "Split", 10.0,   0.0,    0.0,
             description="2-for-1 split"),
        _row("2023-03-01", "Sell",  10.0,  60.0,  600.0),
    ]


class TestSplitAcrossBothWalkers:

    def test_split_basis_agrees_across_walkers(self, isolated_workdir,
                                               stub_prices):
        """The parity assertion this module exists for.

        Runs the SAME ledger through the annotated basis walk and through
        the history snapshot walker, and compares the cost basis each
        arrives at. A drift in either copy of the split rule shows up
        here as a mismatch.
        """
        from src.basis import compute_basis_default
        from src.history import compute_history

        stub_prices.set("SPLT", {
            "2021-01-04": 100.0, "2022-06-15": 50.0, "2023-03-01": 60.0,
            "2023-12-31": 60.0,
        })

        txns = _split_ledger()
        compute_basis_default(txns)

        # The basis walker's view: selling half a rescaled pool relieves
        # half the basis, and realizes proceeds minus THAT.
        sell = [t for t in txns if t["action"] == "Sell"][0]
        assert sell["cost_basis"] == pytest.approx(500.0, abs=0.01), (
            "basis walker relieved the wrong amount — the lot pool was not "
            "rescaled by the split"
        )
        assert sell["realized_gain"] == pytest.approx(100.0, abs=0.01)

        snapshots = compute_history(txns, {"SPLT": 60.0})
        assert snapshots, "history produced no snapshots"

        final = snapshots[-1]
        positions = [p for p in (final.get("positions") or [])
                     if p.get("symbol") == "SPLT"]
        assert positions, "SPLT missing from the final snapshot"

        # 10 bought + 10 from the split - 10 sold.
        assert positions[0]["quantity"] == pytest.approx(10.0)
        # THE parity assertion. $500 only if history rescaled the lots the
        # same way basis._apply_split_to_lots does; $0 if it did not.
        assert positions[0]["cost_basis"] == pytest.approx(500.0, abs=0.05), (
            "history's split branch disagrees with basis._apply_split_to_lots "
            "— the two walkers have drifted"
        )
        assert final["total_cost_basis"] == pytest.approx(500.0, abs=0.05)
