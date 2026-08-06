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
