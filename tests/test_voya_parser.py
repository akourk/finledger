"""Voya 401K parser tests — focuses on the negative-CONTRIBUTION
reversal handling we built for plan-administrator error reversals."""

from __future__ import annotations

import pytest

from .conftest import write_voya_csv


class TestNegativeContribution:
    def test_negative_contribution_emits_reversal_action(self, isolated_workdir):
        """Voya emits CONTRIBUTION rows with negative units when an
        employer or admin reverses a prior contribution.  Without
        renaming, the parser abs()-es the qty and treats it as an ADD,
        producing 2 * |units| of phantom shares.  After the fix the
        action becomes 'CONTRIBUTION REVERSAL' which normalizes to the
        canonical 'Contribution Reversal' (a SUBTRACT action).
        """
        csv = isolated_workdir / "data" / "voya-401k-1.csv"
        write_voya_csv(csv, [
            {"Activity Date": "2020-12-01", "Activity": "CONTRIBUTION",
             "Fund": "VANG WELLINGTON ADM", "Money Source": "Before-Tax",
             "# of Units": "0.9000", "Unit Price": "75.93", "Amount": "62.50"},
            {"Activity Date": "2021-01-04", "Activity": "CONTRIBUTION",
             "Fund": "VANG WELLINGTON ADM", "Money Source": "Before-Tax",
             "# of Units": "-0.9000", "Unit Price": "75.93", "Amount": "-62.50"},
        ])
        from src.parsers import parse_voya_401k
        txns = parse_voya_401k(csv)
        # Should have one CONTRIBUTION and one CONTRIBUTION REVERSAL
        actions = [t["action"] for t in txns]
        assert "CONTRIBUTION" in actions
        assert "CONTRIBUTION REVERSAL" in actions
        # Final balance walk: positive CONTRIBUTION adds, REVERSAL subtracts → 0
        SUB = {"Sell", "Withdrawal", "Transfer Out", "Distribution",
               "Fee", "Tax", "Option Sell", "Option Expire", "Option Exercise",
               "Contribution Reversal"}

        # Apply normalize so we get canonical action names
        from src.normalize import normalize_action
        bal = 0.0
        for t in txns:
            t["account_group"] = "Rollover IRA"  # Voya maps here
            canon = normalize_action(t)
            qty = t.get("quantity", 0)
            if canon == "Neutral":
                continue
            if canon in SUB:
                bal -= qty
            else:
                bal += qty
        assert bal == pytest.approx(0.0), \
            f"CONTRIBUTION + REVERSAL should net to 0 shares, got {bal}"

    def test_negative_dividend_emits_reversal_action(self, isolated_workdir):
        """Existing pattern from before this work: negative DIVIDEND
        renames to DIVIDEND REVERSAL.  Re-test as a regression guard."""
        csv = isolated_workdir / "data" / "voya-401k-1.csv"
        write_voya_csv(csv, [
            {"Activity Date": "2022-12-31", "Activity": "DIVIDEND",
             "Fund": "VANG WELLINGTON ADM", "Money Source": "Before-Tax",
             "# of Units": "-0.05", "Unit Price": "60.00", "Amount": "-3.00"},
        ])
        from src.parsers import parse_voya_401k
        txns = parse_voya_401k(csv)
        assert any(t["action"] == "DIVIDEND REVERSAL" for t in txns)


class TestTransferDirection:
    def test_transfer_split_by_sign(self, isolated_workdir):
        """Voya emits TRANSFER (no direction) — parser must split into
        TRANSFER IN / TRANSFER OUT based on the units sign."""
        csv = isolated_workdir / "data" / "voya-401k-1.csv"
        write_voya_csv(csv, [
            {"Activity Date": "2021-12-10", "Activity": "TRANSFER",
             "Fund": "VANG INST TR 2055", "Money Source": "Before-Tax",
             "# of Units": "-100.5", "Unit Price": "30.0", "Amount": "-3015.00"},
            {"Activity Date": "2021-12-10", "Activity": "TRANSFER",
             "Fund": "VANG TR II 2055", "Money Source": "Before-Tax",
             "# of Units": "100.5", "Unit Price": "30.0", "Amount": "3015.00"},
        ])
        from src.parsers import parse_voya_401k
        txns = parse_voya_401k(csv)
        actions = [t["action"] for t in txns]
        assert "TRANSFER OUT" in actions
        assert "TRANSFER IN" in actions
