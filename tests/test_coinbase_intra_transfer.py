"""Coinbase regular ↔ Pro shuffles must not read as external money.

Both wallets share `account_group = "Coinbase"`, so moving cash between
them is a no-op. But the GDAX export does not tag it, and the Pro-side
`deposit` row is indistinguishable from a real bank deposit.
`reconcile_intra_transfers` pairs them on (date, amount) and re-tags the
Pro leg.

The stakes are the reason this matters more than it looks: without the
pairing, every regular→Pro shuffle inflates `net_contributed` by the
transferred amount — and `net_contributed` is the denominator for TWR,
the SPY benchmark comparison, the savings rate and the FIRE projection.
A shuffle would register as new savings.

The near-miss half is equally important. A REAL bank→Pro deposit has no
regular-side counterpart and must stay a `Deposit`; re-tagging it would
erase genuine contributed capital and understate everything the first
paragraph overstates.

Synthetic round numbers only.
"""

from __future__ import annotations

import pytest

PRO = "coinbase-pro-gdax-1.csv"
REG = "coinbase-1.csv"


def _txn(action, amount, source, date="2023-01-15", symbol="USD") -> dict:
    return {"date": date, "account": "Coinbase", "account_group": "Coinbase",
            "account_type": "Taxable", "symbol": symbol, "action": action,
            "quantity": amount, "price": 1.0, "fees": 0.0, "amount": amount,
            "description": "", "source": source}


@pytest.fixture
def reconcile(isolated_workdir):
    from src.coinbase_reconcile import reconcile_intra_transfers
    return reconcile_intra_transfers


def _actions(txns, source):
    return [t["action"] for t in txns if t["source"] == source]


class TestPairingFires:

    def test_a_matched_pro_deposit_is_retagged(self, reconcile):
        out = reconcile([
            _txn("Transfer Out", 500.0, REG),
            _txn("Deposit", 500.0, PRO),
        ])
        assert _actions(out, PRO) == ["Transfer In"], (
            "the Pro deposit was not re-tagged, so it still reads as "
            "external money arriving"
        )
        assert _actions(out, REG) == ["Transfer Out"]

    def test_a_matched_pro_withdrawal_is_retagged(self, reconcile):
        """The other direction: Pro → regular."""
        out = reconcile([
            _txn("Transfer In", 500.0, REG),
            _txn("Withdrawal", 500.0, PRO),
        ])
        assert _actions(out, PRO) == ["Transfer Out"]

    def test_the_pair_nets_to_zero_external_cash_flow(self, reconcile):
        """The assertion that matters. Both legs must contribute nothing
        to net_contributed, or a wallet shuffle registers as savings."""
        from src.basis import txn_external_cash_flow

        out = reconcile([
            _txn("Transfer Out", 500.0, REG),
            _txn("Deposit", 500.0, PRO),
        ])
        assert sum(txn_external_cash_flow(t) for t in out) == pytest.approx(0.0)

    def test_without_pairing_it_would_have_counted(self, reconcile):
        """Not-vacuous guard: prove the untagged Pro deposit really does
        register as external money, so the test above is measuring
        something."""
        from src.basis import txn_external_cash_flow

        assert txn_external_cash_flow(_txn("Deposit", 500.0, PRO)) == \
            pytest.approx(500.0)


class TestUnpairedLegsAreLeftAlone:
    """Real bank→Pro funding must survive untouched."""

    def test_a_pro_deposit_with_no_counterpart_stays_a_deposit(self,
                                                               reconcile):
        out = reconcile([_txn("Deposit", 500.0, PRO)])
        assert _actions(out, PRO) == ["Deposit"], (
            "a genuine bank→Pro deposit was re-tagged — that erases real "
            "contributed capital"
        )

    def test_a_different_amount_does_not_pair(self, reconcile):
        out = reconcile([
            _txn("Transfer Out", 400.0, REG),
            _txn("Deposit", 500.0, PRO),
        ])
        assert _actions(out, PRO) == ["Deposit"]

    def test_a_different_date_does_not_pair(self, reconcile):
        out = reconcile([
            _txn("Transfer Out", 500.0, REG, date="2023-01-14"),
            _txn("Deposit", 500.0, PRO),
        ])
        assert _actions(out, PRO) == ["Deposit"]

    def test_two_pro_deposits_and_one_counterpart_pair_only_once(self,
                                                                 reconcile):
        """Each regular-side leg may be consumed once. A second identical
        Pro deposit is real external funding that happens to collide on
        date and amount."""
        out = reconcile([
            _txn("Transfer Out", 500.0, REG),
            _txn("Deposit", 500.0, PRO),
            _txn("Deposit", 500.0, PRO),
        ])
        assert sorted(_actions(out, PRO)) == ["Deposit", "Transfer In"]

    def test_two_shuffles_pair_one_to_one(self, reconcile):
        """Two identical same-day shuffles must consume two DISTINCT Pro
        legs, not re-tag the same one twice.

        This needs two of each to discriminate: with a single regular
        leg the matching loop runs once and a reusable Pro index looks
        identical to a consumed one. Mutation found exactly that hole in
        the first version of this file.
        """
        out = reconcile([
            _txn("Transfer Out", 500.0, REG),
            _txn("Transfer Out", 500.0, REG),
            _txn("Deposit", 500.0, PRO),
            _txn("Deposit", 500.0, PRO),
        ])
        assert _actions(out, PRO) == ["Transfer In", "Transfer In"], (
            "both Pro legs should have paired; a leftover Deposit means "
            "one regular leg re-tagged a Pro row that was already claimed"
        )

    def test_a_regular_side_transfer_out_alone_is_untouched(self, reconcile):
        out = reconcile([_txn("Transfer Out", 500.0, REG)])
        assert _actions(out, REG) == ["Transfer Out"]


class TestTheSampleExercisesIt:
    """The sample carries both legs, so this path has an end-to-end
    check rather than only unit coverage."""

    def test_sample_has_a_reconciled_pair(self, isolated_workdir):
        import io
        import contextlib
        from pathlib import Path

        from src.snapshot import import_snapshot

        root = Path(__file__).resolve().parent.parent
        with contextlib.redirect_stdout(io.StringIO()):
            import_snapshot(root / "samples" / "portfolio.snapshot.json",
                            isolated_workdir / "data", overwrite=True)

        pro = (isolated_workdir / "data" / PRO).read_text(encoding="utf-8")
        reg = (isolated_workdir / "data" / REG).read_text(encoding="utf-8")
        assert "deposit" in pro, "sample lost the Pro-side deposit"
        assert "Pro Deposit" in reg, (
            "sample lost the regular-side counter-leg, so the pairing "
            "never fires end to end"
        )
