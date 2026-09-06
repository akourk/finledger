"""`metadata.csv` edge cases — Segment 4 item 5.

`metadata.csv` is hand-maintained, so malformed input is a matter of
when, not if, and every value here feeds a displayed planning or tax
figure. This module pins what the parser ACTUALLY does with bad input.

Two things it documents rather than merely asserts:

1. **Out-of-range numerics are rejected to the DEFAULT, not clamped.**
   CLAUDE.md described `Retirement Age` as "sanity-clamped to 30..100",
   `Pay Frequency` as "snapped to 12/24/26/52", and `State Tax Rate` as
   "clamped to [0, 0.20]". None of those clamps: each falls back to its
   default. `Retirement Age = 20` gives 67, not 30. Reject-to-default is
   a defensible choice — a wildly out-of-range value is more likely a
   typo than an intent — but it is a different choice, and the docs have
   been corrected to match. Behaviour left alone deliberately: changing
   it would move planning figures for anyone relying on today's result.

2. **Every rejection and coercion is silent.** A typo'd `Annual
   Expenses` becomes `0.0`, and that figure is multiplied by 25 to give
   the FI number on the Planning tab. See F-018.

Synthetic round numbers only.
"""

from __future__ import annotations

import pytest

_NL = chr(10)
_HEADER = "Type,Date,Amount,Symbol,Note" + _NL


def _meta(isolated_workdir, *rows: str):
    from src.metadata import parse_metadata

    path = isolated_workdir / "data" / "metadata.csv"
    path.write_text(_HEADER + "".join(r + _NL for r in rows), encoding="utf-8")
    return parse_metadata(isolated_workdir / "data")


class TestNumericGuards:
    """Pinned against measured behaviour, not against the old docs."""

    @pytest.mark.parametrize("amount,expected", [
        ("0.093", 0.093),      # decimal form, in range
        ("9.3", 0.093),        # percent form, divided by 100
        ("0", 0.0),
        ("-5", 0.0),           # negative -> 0
        ("25", 0.0),           # OUT OF RANGE -> default 0, NOT clamped to 0.20
    ])
    def test_state_tax_rate(self, isolated_workdir, amount, expected):
        got = _meta(isolated_workdir, f"State Tax Rate,,{amount},,")["state_tax_rate"]
        assert got == pytest.approx(expected)

    @pytest.mark.parametrize("amount,expected", [
        ("62", 62),
        ("30", 30),            # lower bound accepted
        ("100", 100),          # upper bound accepted
        ("20", 67),            # below range -> DEFAULT, not clamped to 30
        ("200", 67),           # above range -> DEFAULT, not clamped to 100
        ("", 67),
    ])
    def test_retirement_age(self, isolated_workdir, amount, expected):
        got = _meta(isolated_workdir, f"Retirement Age,,{amount},,")["retirement_age"]
        assert got == expected

    def test_retirement_age_defaults_when_absent(self, isolated_workdir):
        assert _meta(isolated_workdir)["retirement_age"] == 67

    @pytest.mark.parametrize("amount,expected", [
        ("12", 12), ("24", 24), ("26", 26), ("52", 52),
        ("13", 26),            # not a listed value -> DEFAULT, not snapped to 12
        ("1000", 26),
        ("0", 26),
    ])
    def test_pay_frequency(self, isolated_workdir, amount, expected):
        got = _meta(isolated_workdir, f"Pay Frequency,,{amount},,")["pay_frequency"]
        assert got == expected

    def test_pay_frequency_defaults_when_absent(self, isolated_workdir):
        assert _meta(isolated_workdir)["pay_frequency"] == 26


class TestMalformedRowsDoNotCrash:
    """The parser must survive a hand-edited file. Nothing here should
    raise — the ledger is worth more than strictness."""

    def test_unknown_type_is_ignored(self, isolated_workdir):
        m = _meta(isolated_workdir,
                  "Not A Real Type,2024-01-01,100,SYM,note",
                  "Annual Expenses,2024-01-01,50000,,")
        assert [r["amount"] for r in m["annual_expenses"]] == [50000.0]

    def test_empty_and_blank_rows_are_ignored(self, isolated_workdir):
        m = _meta(isolated_workdir, ",,,,", "   ,,,,",
                  "Annual Expenses,2024-01-01,50000,,")
        assert len(m["annual_expenses"]) == 1

    def test_missing_file_yields_empty_defaults(self, isolated_workdir):
        from src.metadata import parse_metadata

        m = parse_metadata(isolated_workdir / "data")
        assert m["annual_expenses"] == []
        assert m["retirement_age"] == 67
        assert m["account_groups"] == {}


class TestRejectionsAreReported:
    """F-018's fix. The rejections above are the right behaviour; being
    QUIET about them was not.

    A mistyped value is otherwise indistinguishable from an absent one,
    and the fallback is always a plausible-looking number — so nothing
    on the dashboard looks wrong. The warning is stdout only and changes
    no figure.
    """

    def test_non_numeric_amount_is_reported(self, isolated_workdir, capsys):
        with pytest.raises(ValueError, match="metadata.csv: row 2, column Amount"):
            _meta(isolated_workdir, "Annual Expenses,2024-01-01,50k,,")
        assert "50k" not in capsys.readouterr().out

    @pytest.mark.parametrize("row,needle", [
        ("Retirement Age,,20,,", "Retirement Age"),
        ("Retirement Age,,200,,", "Retirement Age"),
        ("Pay Frequency,,13,,", "Pay Frequency"),
        ("State Tax Rate,,25,,", "State Tax Rate"),
    ])
    def test_out_of_range_values_are_reported(self, isolated_workdir, capsys,
                                              row, needle):
        _meta(isolated_workdir, row)
        out = capsys.readouterr().out
        assert "WARNING" in out and needle in out

    def test_the_message_states_what_was_used_instead(self, isolated_workdir,
                                                      capsys):
        """Naming the fallback is the difference between a warning and a
        scold — the user needs to know which number the dashboard is
        actually showing."""
        _meta(isolated_workdir, "Retirement Age,,20,,")
        assert "67" in capsys.readouterr().out

    @pytest.mark.parametrize("row", [
        "Retirement Age,,62,,",
        "Retirement Age,,30,,",          # lower bound is VALID
        "Retirement Age,,100,,",         # upper bound is VALID
        "Pay Frequency,,26,,",
        "Pay Frequency,,52,,",
        "State Tax Rate,,0.093,,",
        "State Tax Rate,,9.3,,",         # percent form is VALID
        "State Tax Rate,,0,,",
        "Annual Expenses,2024-01-01,50000,,",
        "Account Group,,,Schwab Roth IRA,Roth IRA",   # blank Amount
        "Salary History,2024-01-01,120000,,",
    ])
    def test_valid_rows_are_silent(self, isolated_workdir, capsys, row):
        """The half that matters most. Blank Amounts are extremely common
        (every Account Group / Account Type row has one) and must never
        warn — a warning on every run is one the user stops reading."""
        _meta(isolated_workdir, row)
        assert "WARNING" not in capsys.readouterr().out

    def test_a_wholly_valid_file_is_silent(self, isolated_workdir, capsys):
        _meta(isolated_workdir,
              "Personal Info,,,,Birthday=1990-06-15",
              "Account Group,,,Schwab Roth IRA,Roth IRA",
              "Account Type,,,Roth IRA,Retirement",
              "Retirement Age,,62,,",
              "Pay Frequency,,26,,",
              "State Tax Rate,,0.093,,",
              "Annual Expenses,2024-01-01,50000,,")
        assert "WARNING" not in capsys.readouterr().out

    def test_missing_file_is_silent(self, isolated_workdir, capsys):
        from src.metadata import parse_metadata

        parse_metadata(isolated_workdir / "data")
        assert "WARNING" not in capsys.readouterr().out


class TestSilentCoercion:
    """F-018. These pin behaviour that is arguably WRONG, so that a
    future fix has to change a test deliberately rather than by
    accident — and so the blast radius is written down next to it.
    """

    def test_non_numeric_amount_cannot_become_zero(self, isolated_workdir):
        """Reject the input instead of publishing a plausible but false FI target."""
        with pytest.raises(ValueError, match="invalid number"):
            _meta(isolated_workdir, "Annual Expenses,2024-01-01,50k,,")

    def test_malformed_date_is_stored_unvalidated(self, isolated_workdir):
        """Dates are kept as raw strings. Consumers slice `date[:4]` for
        a tax year, so a malformed date propagates as a garbage year
        rather than being rejected at the door."""
        m = _meta(isolated_workdir, "Annual Expenses,not-a-date,50000,,")
        assert m["annual_expenses"][0]["date"] == "not-a-date"

    def test_bad_numeric_row_rejects_whole_metadata(self, isolated_workdir):
        """An incomplete metadata import must never reach the dashboard."""
        with pytest.raises(ValueError, match="metadata.csv: row 2"):
            _meta(isolated_workdir,
                  "Annual Expenses,not-a-date,abc,,",
                  "Account Group,,,Schwab Roth IRA,Roth IRA")
