"""Ambiguous broker actions must be split by sign IN THE PARSER.

CLAUDE.md's central post-parse invariant: quantity, price, fees and
amount are always non-negative after parsing, and **direction lives in
the action**. Downstream, `main.py` picks a sign from
`_SUBTRACT_ACTIONS` / `_NEUTRAL_ACTIONS`, and `cash_bridge`'s docstring
states the stakes plainly — a sign error there invents portfolio value.

That only works if every direction-ambiguous raw action is split
*before* the `abs()`. Once abs() has run the direction is gone and no
downstream code can recover it. CLAUDE.md lists the known ambiguous
actions; this module pins each one in both directions, and asserts the
stored magnitudes stay non-negative so the split can't be "fixed" later
by leaking a negative through instead.

Taxonomy (4). Synthetic round numbers only.
"""

from __future__ import annotations

import pytest

from tests.conftest import write_robinhood_csv, write_voya_csv


def _nonneg(txn: dict) -> None:
    for field in ("quantity", "price", "fees", "amount"):
        val = txn.get(field)
        if isinstance(val, (int, float)):
            assert val >= 0, (
                f"{field}={val} is negative after parsing — direction must be "
                f"encoded in the action, not the sign (action={txn['action']!r})"
            )


class TestRobinhoodACH:
    """`ACH` alone says nothing about direction; the amount's sign does."""

    def _parse(self, tmp_path, amount: str):
        from src.parsers import parse_robinhood

        p = tmp_path / "data" / "robinhood-1.csv"
        write_robinhood_csv(p, [{
            "Activity Date": "1/15/2022", "Description": "ACH Deposit",
            "Trans Code": "ACH", "Amount": amount,
        }])
        return parse_robinhood(p)

    def test_positive_amount_is_a_deposit(self, isolated_workdir):
        txns = self._parse(isolated_workdir, "$5000.00")
        assert len(txns) == 1
        assert txns[0]["action"] == "ACH Deposit"
        _nonneg(txns[0])

    def test_negative_amount_is_a_withdrawal(self, isolated_workdir):
        txns = self._parse(isolated_workdir, "($5000.00)")
        assert len(txns) == 1
        assert txns[0]["action"] == "ACH Withdrawal", (
            "a negative ACH parsed as a Deposit would count money leaving "
            "the account as money arriving — inventing contributed capital"
        )
        _nonneg(txns[0])

    def test_the_two_directions_differ(self, isolated_workdir):
        """The assertion that actually matters. If the split were removed,
        both rows would carry the same action and the test above could
        still pass by coincidence of naming."""
        pos = self._parse(isolated_workdir, "$5000.00")[0]
        neg = self._parse(isolated_workdir, "($5000.00)")[0]
        assert pos["action"] != neg["action"]
        assert pos["amount"] == neg["amount"] == 5000.0, (
            "magnitudes must be identical — only the action differs"
        )


class TestVoyaTransfer:
    """Voya writes `TRANSFER` for both directions; units carry the sign."""

    def _parse(self, tmp_path, units: str):
        from src.parsers import parse_voya_401k

        p = tmp_path / "data" / "voya-401k-1.csv"
        write_voya_csv(p, [{
            "Activity Date": "2022-01-15", "Activity": "TRANSFER",
            "Fund": "Target 2050", "Money Source": "Employee",
            "# of Units": units, "Unit Price": "10.00", "Amount": "1000.00",
        }])
        return parse_voya_401k(p)

    def test_positive_units_transfer_in(self, isolated_workdir):
        txns = self._parse(isolated_workdir, "100.0")
        assert len(txns) == 1
        assert txns[0]["action"] == "TRANSFER IN"
        _nonneg(txns[0])

    def test_negative_units_transfer_out(self, isolated_workdir):
        txns = self._parse(isolated_workdir, "-100.0")
        assert len(txns) == 1
        assert txns[0]["action"] == "TRANSFER OUT", (
            "an outbound 401k transfer parsed as inbound would add the "
            "units to the balance instead of removing them"
        )
        _nonneg(txns[0])

    def test_the_two_directions_differ(self, isolated_workdir):
        pos = self._parse(isolated_workdir, "100.0")[0]
        neg = self._parse(isolated_workdir, "-100.0")[0]
        assert pos["action"] != neg["action"]
        assert pos["quantity"] == neg["quantity"] == 100.0


class TestDirectionSurvivesNormalization:
    """A parser-level split is only useful if the canonical vocabulary
    keeps the two apart, with opposite balance effects."""

    @pytest.mark.parametrize("raw_in,raw_out,group", [
        ("ACH Deposit", "ACH Withdrawal", "Robinhood"),
        ("TRANSFER IN", "TRANSFER OUT", "Rollover IRA"),
    ])
    def test_opposite_actions_have_opposite_balance_effects(
        self, isolated_workdir, raw_in, raw_out, group
    ):
        from src.actions import (names_with_balance_effect)
        from src.normalize import normalize_action

        adds = names_with_balance_effect("add")
        subs = names_with_balance_effect("subtract")

        a_in = normalize_action({"action": raw_in, "account_group": group,
                                 "amount": 100.0, "symbol": "USD"})
        a_out = normalize_action({"action": raw_out, "account_group": group,
                                  "amount": 100.0, "symbol": "USD"})

        assert a_in != a_out, (
            f"{raw_in!r} and {raw_out!r} normalized to the same action — the "
            "parser's sign split is discarded downstream"
        )
        assert a_in in adds, f"{a_in!r} should increase a balance"
        assert a_out in subs, f"{a_out!r} should decrease a balance"


class TestNoParserEmitsNegativeMagnitudes:
    """The invariant across every parser, on the shipped sample.

    `tests/test_sample_snapshot.py` asserts this too; repeated here
    because this module is where someone will look after touching a sign
    split, and the check is what stops a "fix" that leaks a negative
    through instead of setting the right action.
    """

    def test_sample_portfolio_has_no_negative_magnitudes(self,
                                                         isolated_workdir):
        from pathlib import Path

        from src.parsers import parse_all_files
        from src.snapshot import import_snapshot

        root = Path(__file__).resolve().parent.parent
        import_snapshot(root / "samples" / "portfolio.snapshot.json",
                        isolated_workdir / "data", overwrite=True)
        for t in parse_all_files(isolated_workdir / "data"):
            _nonneg(t)
