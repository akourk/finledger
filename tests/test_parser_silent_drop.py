"""A broker file that parses to nothing must say so (F-017).

Every parser drops an unparseable row with a bare `continue`. That is
the right call for one odd row, but it means a change to the broker's
date format takes the WHOLE file to zero in silence, and that account
disappears from the portfolio with no error.

Measured before the fix, on the synthetic sample: rewriting every
date-like token to a format no parser accepts took 7 of 8 broker files
to zero rows — 35 transactions gone, no exception, no warning, no
stderr.

Nothing downstream reliably catches it. The What's-Changed panel reports
the drop only after the fact, and `analytics.changes`' empty-run guard
fires only when EVERY file is empty, so a single missing broker sails
through.

The two halves of this module matter equally:

* the warning FIRES when a file with data rows yields nothing;
* it stays SILENT for a legitimately empty file.

An empty `manual-adjustments.csv` is the common case. A warning that
cries wolf on every run is worse than none, because it trains the reader
to skip the line — which is the same failure this audit found in the
alerts panel.
"""

from __future__ import annotations

import re

import pytest

from src.parsers import parse_all_files

_DATE_TOKEN = re.compile(r"\b\d{1,4}[/-]\d{1,2}[/-]\d{1,4}\b")

ROBINHOOD_CSV = (
    "Activity Date,Process Date,Settle Date,Instrument,Description,"
    "Trans Code,Quantity,Price,Amount\n"
    + "".join(
        f"{m}/15/2022,,,VOO,Vanguard S&P 500 ETF,Buy,1,$400.00,($400.00)\n"
        for m in range(1, 13)
    )
)


def _write(tmp_path, name: str, body: str):
    p = tmp_path / "data" / name
    p.write_text(body, encoding="utf-8")
    return p


class TestZeroRowWarning:

    def test_warns_when_a_populated_file_parses_to_nothing(
        self, isolated_workdir, capsys
    ):
        """The F-017 scenario: broker changes its date format."""
        broken = _DATE_TOKEN.sub("31.12.2099", ROBINHOOD_CSV)
        _write(isolated_workdir, "robinhood-1.csv", broken)

        txns = parse_all_files(isolated_workdir / "data")
        out = capsys.readouterr().out

        assert txns == [], "fixture no longer reproduces the drop"
        assert "WARNING" in out, (
            "a broker file went from 12 rows to zero and said nothing — "
            "that account would silently vanish from the portfolio"
        )
        assert "robinhood-1.csv" in out
        assert "MISSING" in out, "the warning must state the consequence"

    def test_silent_for_a_legitimately_empty_file(self, isolated_workdir,
                                                  capsys):
        """Near-miss. An empty manual-adjustments.csv is normal."""
        _write(isolated_workdir, "manual-adjustments.csv",
               "# Manual adjustments — hand-edited corrections.\n"
               "# Lines starting with # are ignored.\n"
               "Account,Date,Type,Symbol,Quantity,Price,Amount,Description\n")

        parse_all_files(isolated_workdir / "data")
        out = capsys.readouterr().out

        assert "WARNING" not in out, (
            "warned about a legitimately empty file — a warning that fires "
            "every run trains the reader to ignore it"
        )

    def test_silent_when_the_file_parses_normally(self, isolated_workdir,
                                                  capsys):
        _write(isolated_workdir, "robinhood-1.csv", ROBINHOOD_CSV)

        txns = parse_all_files(isolated_workdir / "data")
        out = capsys.readouterr().out

        assert len(txns) == 12
        assert "WARNING" not in out

    def test_header_only_broker_file_is_silent(self, isolated_workdir, capsys):
        """A broker export with a header and no activity is valid — some
        accounts genuinely have no transactions in a period."""
        header = ROBINHOOD_CSV.splitlines()[0] + "\n"
        _write(isolated_workdir, "robinhood-1.csv", header)

        parse_all_files(isolated_workdir / "data")
        assert "WARNING" not in capsys.readouterr().out


def _with_bad_dates(n_bad: int) -> str:
    """ROBINHOOD_CSV with the first `n_bad` rows' dates mangled."""
    head, *rows = ROBINHOOD_CSV.splitlines()
    for i in range(n_bad):
        rows[i] = _DATE_TOKEN.sub("31.12.2099", rows[i])
    return head + "\n" + "\n".join(rows) + "\n"


class TestPartialDropWarning:
    """Tier 2 of F-017 — the case tier 1 structurally cannot see.

    A file that loses SOME rows still parses to a non-zero count, so the
    zero-row warning stays quiet and nothing anywhere notices. Those
    transactions are simply absent from the portfolio.

    Counted at the one seam that makes it possible without touching any
    parser: every parser calls exactly one `_date_*` helper per row
    inside the try/except that drops it, and none tries several formats
    speculatively — so a raise from those helpers is exactly a dropped
    row.
    """

    def test_warns_with_the_exact_count(self, isolated_workdir, capsys):
        _write(isolated_workdir, "robinhood-1.csv", _with_bad_dates(3))

        txns = parse_all_files(isolated_workdir / "data")
        out = capsys.readouterr().out

        assert len(txns) == 9, "the good rows must still parse"
        assert "3 row(s) were DROPPED" in out, (
            "a partial drop went unreported — those transactions are "
            "missing from the portfolio with no count anomaly to notice"
        )

    def test_silent_when_every_row_parses(self, isolated_workdir, capsys):
        _write(isolated_workdir, "robinhood-1.csv", ROBINHOOD_CSV)
        parse_all_files(isolated_workdir / "data")
        assert "DROPPED" not in capsys.readouterr().out

    def test_blank_dates_are_not_counted_as_drops(self, isolated_workdir,
                                                  capsys):
        """Near-miss. Trailing blank lines and spacer rows are structural,
        not a format problem. Counting them would put a permanent false
        warning on every file that contains one."""
        head, *rows = ROBINHOOD_CSV.splitlines()
        rows[0] = "," * 8            # a wholly blank data row
        _write(isolated_workdir, "robinhood-1.csv",
               head + "\n" + "\n".join(rows) + "\n")

        parse_all_files(isolated_workdir / "data")
        assert "DROPPED" not in capsys.readouterr().out

    def test_the_tally_does_not_leak_between_files(self, isolated_workdir,
                                                   capsys):
        """The counter is module-global, so a missing reset would blame
        the next file for the previous one's drops."""
        _write(isolated_workdir, "robinhood-1.csv", _with_bad_dates(2))
        _write(isolated_workdir, "robinhood-2.csv", ROBINHOOD_CSV)

        parse_all_files(isolated_workdir / "data")
        out = capsys.readouterr().out

        assert out.count("DROPPED") == 1, (
            "the clean file was blamed for the broken file's drops"
        )
        assert "robinhood-1.csv — 2 row(s)" in out
        assert "robinhood-2.csv — " not in out

    def test_a_stale_tally_is_not_blamed_on_the_first_file(
        self, isolated_workdir, capsys
    ):
        """The counter is module-global and parsers are importable
        individually (the package docstring advertises that for unit
        testing). So a tally can already be standing when
        `parse_all_files` starts, and without the pre-parse reset it
        would be attributed to whichever file happens to sort first.
        """
        # Both imported HERE, in the test body, on purpose.
        # `isolated_workdir` purges `src.*` from sys.modules, but this
        # file's module-level `parse_all_files` was bound at collection
        # time and still closes over the pre-purge `_helpers`. Mixing the
        # two gives you two different module-global counters and the
        # assertion below silently tests nothing.
        from src.parsers import parse_all_files as _parse_all
        from src.parsers._helpers import _date_ymd

        for _ in range(4):                       # build a stale tally
            with pytest.raises(ValueError):
                _date_ymd("31.12.2099")

        _write(isolated_workdir, "robinhood-1.csv", ROBINHOOD_CSV)
        txns = _parse_all(isolated_workdir / "data")
        out = capsys.readouterr().out

        assert len(txns) == 12
        assert "DROPPED" not in out, (
            "a clean file was blamed for drops that happened before "
            "parse_all_files was even called"
        )

    def test_take_date_failures_returns_and_resets(self, isolated_workdir):
        from src.parsers._helpers import _date_ymd, take_date_failures

        take_date_failures()
        for _ in range(3):
            with pytest.raises(ValueError):
                _date_ymd("31.12.2099")

        assert take_date_failures() == 3
        assert take_date_failures() == 0, "the tally must reset when read"

    def test_a_blank_value_raises_but_is_not_tallied(self, isolated_workdir):
        from src.parsers._helpers import _date_ymd, take_date_failures

        take_date_failures()
        with pytest.raises(ValueError):
            _date_ymd("   ")
        assert take_date_failures() == 0

    def test_helpers_still_raise_unchanged(self, isolated_workdir):
        """The counting wrapper must not swallow or convert the error —
        every parser's `except` depends on the original exception."""
        from src.parsers._helpers import (_date_dmy, _date_iso, _date_mdy,
                                          _date_ymd)

        for fn in (_date_mdy, _date_ymd, _date_dmy, _date_iso):
            with pytest.raises(ValueError):
                fn("definitely-not-a-date")


class TestHasUnreadData:
    """The heuristic on its own, including its deliberate blind spot."""

    def test_comment_and_blank_lines_do_not_count_as_data(self,
                                                          isolated_workdir):
        from src.parsers import _has_unread_data

        p = _write(isolated_workdir, "x.csv",
                   "# c\n" * 20 + "\n" * 20 + "Account,Date\n")
        assert _has_unread_data(p) is False

    def test_a_populated_file_counts_as_data(self, isolated_workdir):
        from src.parsers import _has_unread_data

        p = _write(isolated_workdir, "x.csv", ROBINHOOD_CSV)
        assert _has_unread_data(p) is True

    def test_a_long_preamble_with_no_rows_stays_below_the_threshold(
        self, isolated_workdir
    ):
        """Voya writes six lines before its header. The allowance is set
        above that on purpose, so an empty Voya export cannot warn."""
        from src.parsers import _has_unread_data

        p = _write(isolated_workdir, "x.csv",
                   "plan name\ndate range\n\"\n\"\n\"\n\"\nActivity Date,Fund\n")
        assert _has_unread_data(p) is False

    def test_missing_file_is_not_treated_as_data(self, isolated_workdir):
        from src.parsers import _has_unread_data

        assert _has_unread_data(isolated_workdir / "data" / "nope.csv") is False
