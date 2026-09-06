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


class TestSubstantiveRows:
    """Count actual parsed CSV rows, including one-row files and preambles."""

    def test_comment_and_blank_lines_do_not_count_as_data(self, isolated_workdir):
        from src.parsers import parse_report
        _write(isolated_workdir, "manual-adjustments.csv",
               "# c\n" * 20 + "\n" * 20
               + "Account,Date,Type,Symbol,Quantity,Price,Amount,Description\n")
        assert parse_all_files(isolated_workdir / "data") == []
        assert parse_report() == []

    def test_a_populated_file_has_transactions(self, isolated_workdir):
        _write(isolated_workdir, "robinhood.csv", ROBINHOOD_CSV)
        assert len(parse_all_files(isolated_workdir / "data")) == 12

    def test_a_long_preamble_without_data_is_valid(self, isolated_workdir):
        from src.parsers import parse_report
        _write(isolated_workdir, "voya-401k.csv",
               "plan name\ndate range\n\n\n\n\n"
               "Activity Date,Fund,Activity,# of Units,Unit Price,Amount\n")
        assert parse_all_files(isolated_workdir / "data") == []
        assert parse_report() == []

    def test_missing_data_is_blocked_by_validation(self, isolated_workdir):
        from src.parsers import validate_ingestion
        with pytest.raises(ValueError, match="No valid transactions"):
            validate_ingestion(parse_all_files(isolated_workdir / "data"))


class TestTierThreeReachesTheDashboard:
    """F-017 tier 3. Detection is worth what it reaches.

    Tiers 1 and 2 print to the console — the one place a daily run's
    output is least likely to be read, for the highest-severity failure
    there is: an account silently absent from the portfolio. Tier 3
    records the findings so `data_health` can surface them in the
    dashboard panel alongside every other integrity issue.

    A file that parses to ZERO leaves no transactions behind, so the
    out-of-band record is the only trace it existed. That is why this
    cannot be reconstructed downstream and has to be captured at parse
    time.
    """

    BROKEN = (
        "Activity Date,Process Date,Settle Date,Instrument,Description,"
        "Trans Code,Quantity,Price,Amount\n"
        + "".join(
            f"2022-{m:02d}-15,,,VOO,Vanguard S&P 500 ETF,Buy,1,$400.00,($400.00)\n"
            for m in range(1, 13)
        )
    )

    def _parse(self, workdir, files: dict):
        import contextlib
        import io as _io

        from src.parsers import parse_all_files, parse_report

        data = workdir / "data"
        data.mkdir(parents=True, exist_ok=True)
        for name, body in files.items():
            (data / name).write_text(body, encoding="utf-8")
        with contextlib.redirect_stdout(_io.StringIO()):
            txns = parse_all_files(data)
        return txns, parse_report()

    def test_a_clean_parse_reports_nothing(self, isolated_workdir):
        """The normal case. A report that is never empty is noise, and
        noise is what trains a reader to skip the panel."""
        txns, report = self._parse(isolated_workdir,
                                   {"robinhood-1.csv": ROBINHOOD_CSV})
        assert txns, "fixture did not parse — the near-miss is meaningless"
        assert report == []

    def test_a_zero_row_file_is_recorded(self, isolated_workdir):
        txns, report = self._parse(isolated_workdir,
                                   {"robinhood-1.csv": self.BROKEN})
        assert txns == [], "fixture parsed after all; pick a worse date format"
        assert len(report) == 1
        assert report[0]["file"] == "robinhood-1.csv"
        assert report[0]["parsed"] == 0
        assert report[0]["empty_with_data"] is True

    def test_the_report_is_rebuilt_per_run(self, isolated_workdir):
        """A stale finding is worse than none — it would report an
        account missing that is now present."""
        self._parse(isolated_workdir, {"robinhood-1.csv": self.BROKEN})
        _txns, report = self._parse(isolated_workdir,
                                    {"robinhood-1.csv": ROBINHOOD_CSV})
        assert report == [], (
            "the previous run's finding survived into a clean run"
        )

    def test_reading_the_report_does_not_consume_it(self, isolated_workdir):
        """Unlike `take_date_failures`, which MUST be cleared between
        files. A consuming read here would mean whichever caller asked
        first won, and the dashboard would show nothing."""
        from src.parsers import parse_report

        self._parse(isolated_workdir, {"robinhood-1.csv": self.BROKEN})
        assert parse_report() == parse_report() != []

    def test_the_returned_report_cannot_mutate_the_stored_one(self,
                                                              isolated_workdir):
        from src.parsers import parse_report

        self._parse(isolated_workdir, {"robinhood-1.csv": self.BROKEN})
        got = parse_report()
        got[0]["file"] = "tampered"
        assert parse_report()[0]["file"] == "robinhood-1.csv"


class TestTheCheckSplitsSeverityOnEvidence:
    """A whole missing account and one malformed row are not the same
    finding, and giving them the same severity would make the loud one
    unreadable."""

    def _check(self, report):
        from src.analytics.data_health import _check_parser_dropped_rows
        return _check_parser_dropped_rows(report)

    def _row(self, **kw):
        base = {"file": "b-1.csv", "broker": "robinhood", "parsed": 400,
                "dropped": 0, "empty_with_data": False}
        base.update(kw)
        return base

    def test_a_zero_row_file_is_high(self, isolated_workdir):
        issues = self._check([self._row(parsed=0, dropped=9,
                                        empty_with_data=True)])
        assert [i["severity"] for i in issues] == ["high"]
        assert issues[0]["kind"] == "parser_produced_no_rows"

    def test_dropped_rows_alone_are_warn(self, isolated_workdir):
        """Ordinary in a real export. Escalating it would put a routine
        finding next to 'an account is missing'."""
        issues = self._check([self._row(dropped=3)])
        assert [i["severity"] for i in issues] == ["warn"]
        assert issues[0]["kind"] == "parser_dropped_rows"
        assert issues[0]["count"] == 3

    def test_a_zero_row_file_is_not_double_reported(self, isolated_workdir):
        """It drops rows AND parses to zero. Reporting both would put the
        same file under two headings and inflate the dropped-row count."""
        issues = self._check([self._row(parsed=0, dropped=9,
                                        empty_with_data=True)])
        assert len(issues) == 1

    def test_both_kinds_coexist_across_files(self, isolated_workdir):
        issues = self._check([
            self._row(file="a.csv", parsed=0, dropped=9, empty_with_data=True),
            self._row(file="b.csv", dropped=3),
        ])
        assert sorted(i["severity"] for i in issues) == ["high", "warn"]

    def test_dropped_counts_sum_across_files(self, isolated_workdir):
        issues = self._check([self._row(file="a.csv", dropped=3),
                              self._row(file="b.csv", dropped=4)])
        assert issues[0]["count"] == 7
        assert len(issues[0]["details"]) == 2

    def test_an_empty_report_is_silent(self, isolated_workdir):
        assert self._check([]) == []
        assert self._check(None) == []

    def test_the_details_name_the_file(self, isolated_workdir):
        """The message has to be actionable without a console scrollback:
        which file, which parser."""
        issues = self._check([self._row(file="schwab-roth-ira-1.csv",
                                        broker="schwab_roth", parsed=0,
                                        dropped=5, empty_with_data=True)])
        joined = " ".join(issues[0]["details"])
        assert "schwab-roth-ira-1.csv" in joined
        assert "schwab_roth" in joined


class TestTheCheckIsActuallyWired:
    """Working and connected are different claims.

    Mutation found this: deleting the `issues.extend(...)` line from
    `compute_data_health` survived the entire suite. Every test above
    proves the detector detects and the report records — none proved the
    two ever meet. That is precisely the failure tier 3 exists to
    prevent, one level up: perfect detection that reaches nobody.
    """

    def _args(self, **kw):
        """Minimal well-formed arguments for compute_data_health."""
        base = {"txns": [], "holdings_by_account": [], "history": [],
                "analytics": {}, "cache_dir": None}
        base.update(kw)
        return base

    def test_an_explicit_report_reaches_the_output(self, isolated_workdir):
        from src.analytics.data_health import compute_data_health

        issues = compute_data_health(
            **self._args(cache_dir=isolated_workdir / "cache"),
            parse_report=[{"file": "b-1.csv", "broker": "robinhood",
                           "parsed": 0, "dropped": 9,
                           "empty_with_data": True}])
        kinds = {i["kind"] for i in issues}
        assert "parser_produced_no_rows" in kinds, (
            "the parser check is not wired into compute_data_health, so a "
            "missing account never reaches the dashboard"
        )

    def test_a_clean_report_adds_nothing(self, isolated_workdir):
        from src.analytics.data_health import compute_data_health

        issues = compute_data_health(
            **self._args(cache_dir=isolated_workdir / "cache"),
            parse_report=[])
        assert not [i for i in issues
                    if i["kind"].startswith("parser_")]

    def test_it_defaults_to_the_last_parse_run(self, isolated_workdir):
        """The production path passes no report — `main()` parses and
        then builds analytics, and nothing threads the findings between
        them. If the default lookup broke, every real run would silently
        lose this check while the explicit-argument test above stayed
        green."""
        import contextlib
        import io as _io

        from src.parsers import parse_all_files
        from src.analytics.data_health import compute_data_health

        data = isolated_workdir / "data"
        data.mkdir(parents=True, exist_ok=True)
        (data / "robinhood-1.csv").write_text(
            TestTierThreeReachesTheDashboard.BROKEN, encoding="utf-8")
        with contextlib.redirect_stdout(_io.StringIO()):
            parse_all_files(data)

        issues = compute_data_health(
            **self._args(cache_dir=isolated_workdir / "cache"))
        assert "parser_produced_no_rows" in {i["kind"] for i in issues}


def test_data_health_categories_are_a_closed_vocabulary():
    """`category` is a UI grouping key, not free text.

    The Overview panel groups issues by this string, so a synonym
    silently creates a SECOND heading for one concept — which is what
    happened when the parser checks landed as "Data integrity" beside
    the established "Integrity". A string that becomes a grouping key is
    an enum wearing a disguise.
    """
    import ast
    import inspect

    from src.analytics import data_health as D

    allowed = {"Integrity", "Coverage", "Reconciliation"}
    found = set()
    for node in ast.walk(ast.parse(inspect.getsource(D))):
        if not isinstance(node, ast.Dict):
            continue
        for k, v in zip(node.keys, node.values):
            if (isinstance(k, ast.Constant) and k.value == "category"
                    and isinstance(v, ast.Constant)):
                found.add(v.value)
    assert found <= allowed, (
        f"unknown data_health category/ies {sorted(found - allowed)} — each "
        "one renders as its own panel heading. Reuse an existing category, "
        "or add the new one here deliberately."
    )
