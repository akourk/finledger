"""F-010 and F-011 — two ways fin quietly did less than it claimed.

Both are the shape this audit kept finding worth fixing: input or
configuration that degrades without saying so, leaving a user confident
in a protection that is not running.

* **F-010** — the coverage-gap alert looked at `history[-12:]`. That was
  written when history was sampled MONTHLY, so twelve snapshots meant a
  year. The cadence later went semimonthly and the window silently
  halved to about six months — same code, same tests, half the
  coverage, and a message still saying "last 12".
* **F-011** — a malformed `[expected ±N]` token in a Reconcile note was
  indistinguishable from no token. The user documents a known
  difference, the parse fails, the row reverts to being banded on its
  raw delta and keeps flagging. The explanation does nothing and
  nothing says so.

Synthetic round numbers only.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# F-010
# ---------------------------------------------------------------------------

def _snap(date: str, priced_pct: float = 1.0) -> dict:
    return {"date": date, "total": 1000.0, "priced_pct": priced_pct}


def _monthly(n: int, priced_pct: float = 1.0) -> list[dict]:
    """n monthly snapshots ending 2025-12-31."""
    out = []
    for i in range(n):
        y, m = divmod(11 - (n - 1 - i), 12)
        out.append(_snap(f"{2025 + y - 1 if m < 0 else 2025}-"
                         f"{(m % 12) + 1:02d}-15", priced_pct))
    return out


def _semimonthly(months: int, priced_pct: float = 1.0) -> list[dict]:
    """Two snapshots a month, ending 2025-12-31."""
    out = []
    for m in range(1, months + 1):
        out.append(_snap(f"2025-{m:02d}-15", priced_pct))
        out.append(_snap(f"2025-{m:02d}-28", priced_pct))
    return out


class TestCoverageWindowIsTimeBased:

    def _window(self, history):
        from src.analytics.alerts import (_snapshots_within,
                                          _COVERAGE_LOOKBACK_DAYS)
        return _snapshots_within(history, _COVERAGE_LOOKBACK_DAYS)

    def test_a_full_year_of_monthly_snapshots_is_all_included(self):
        hist = [_snap(f"2025-{m:02d}-15") for m in range(1, 13)]
        assert len(self._window(hist)) == 12

    def test_doubling_the_cadence_doubles_the_rows_not_halves_the_span(self):
        """The property the whole fix exists for. Sampling twice as often
        must return twice as many rows over the SAME year — a count-based
        window returns the same 12 rows covering half the time."""
        monthly = [_snap(f"2025-{m:02d}-15") for m in range(1, 13)]
        semi = _semimonthly(12)
        assert len(self._window(monthly)) == 12
        assert len(self._window(semi)) == 24, (
            "the window is still counting snapshots, so a cadence change "
            "silently moves how far back it looks"
        )

    def test_older_snapshots_are_excluded(self):
        hist = ([_snap("2020-01-15")] + [_snap(f"2025-{m:02d}-15")
                                         for m in range(1, 13)])
        got = self._window(hist)
        assert len(got) == 12
        assert all(s["date"] >= "2025-01-01" for s in got)

    def test_the_window_is_anchored_to_the_newest_snapshot(self):
        """Not to today. A stale export must report on its own final
        year rather than returning an empty window."""
        hist = [_snap(f"2019-{m:02d}-15") for m in range(1, 13)]
        assert len(self._window(hist)) == 12

    def test_the_boundary_day_is_included(self):
        """A 365-day lookback includes the day 365 days back, not 364.

        Needs a snapshot sitting exactly ON the cutoff: with every
        fixture date comfortably inside the window, `>=` and `>` are
        indistinguishable and the comparison is unpinned. Mutation found
        exactly that hole here.
        """
        from datetime import date, timedelta

        from src.analytics.alerts import _COVERAGE_LOOKBACK_DAYS

        end = date(2025, 12, 31)
        on_boundary = end - timedelta(days=_COVERAGE_LOOKBACK_DAYS)
        just_outside = on_boundary - timedelta(days=1)
        hist = [_snap(just_outside.isoformat()),
                _snap(on_boundary.isoformat()),
                _snap(end.isoformat())]
        got = {s["date"] for s in self._window(hist)}
        assert on_boundary.isoformat() in got, (
            "the snapshot exactly at the lookback boundary was excluded"
        )
        assert just_outside.isoformat() not in got, (
            "a snapshot older than the window was included"
        )

    def test_an_empty_history_is_empty(self):
        assert self._window([]) == []

    def test_an_unreadable_date_falls_back_to_everything(self):
        """A diagnostic must not disappear because a date is malformed —
        that hides the condition it exists to report."""
        hist = [_snap("2025-01-15"), {"date": None, "priced_pct": 0.5}]
        assert len(self._window(hist)) == 2

    def test_the_alert_reports_the_span_it_actually_used(self):
        """The old message said "of last 12 snapshots" while looking at
        six months of them. Whatever the window is, the text has to
        describe it."""
        from src.analytics.alerts import compute_alerts

        hist = _semimonthly(12, priced_pct=0.5)
        alerts = compute_alerts([], [], hist, {}, {}, {})
        gap = [a for a in alerts if a["kind"] == "coverage_gap"]
        assert gap, "the coverage-gap alert did not fire on a bad-coverage history"
        msg = gap[0]["message"]
        assert "24" in msg, f"message does not report the real row count: {msg}"
        assert "12 months" in msg, f"message does not state the span: {msg}"

    def test_clean_coverage_stays_silent(self):
        from src.analytics.alerts import compute_alerts

        alerts = compute_alerts([], [], _semimonthly(12, priced_pct=1.0),
                                {}, {}, {})
        assert not [a for a in alerts if a["kind"] == "coverage_gap"]


# ---------------------------------------------------------------------------
# F-011
# ---------------------------------------------------------------------------

class TestExpectedTokenParsing:

    @pytest.mark.parametrize("note,expected", [
        ("K-1 entity [expected -50.00]", -50.0),
        ("[expected 1234.56]", 1234.56),
        ("[expected +12]", 12.0),
        ("[expected -0.01] trailing text", -0.01),
    ])
    def test_well_formed_tokens_parse(self, note, expected):
        from src.analytics.reconcile import _expected_delta
        assert _expected_delta(note) == pytest.approx(expected)

    @pytest.mark.parametrize("note", [
        "", "no token here", "expected -50 without brackets",
    ])
    def test_absent_tokens_are_absent(self, note):
        from src.analytics.reconcile import _expected_delta, _malformed_expected
        assert _expected_delta(note) is None
        assert _malformed_expected(note) is False


class TestMalformedTokensAreDetected:
    """The F-011 fix. Each of these is a plausible thing to type, and
    each used to be silently identical to writing nothing."""

    MALFORMED = [
        "[expected 1,234.56]",      # thousands separator — the documented trap
        "[expected −50]",      # unicode minus, straight from a doc/spreadsheet
        "[expected]",               # forgot the number
        "[expected $50]",           # currency symbol
        "[expected 50 USD]",
        "[Expected 50]",            # wrong case
        "[expected  ]",
    ]

    @pytest.mark.parametrize("note", MALFORMED)
    def test_it_is_flagged_as_malformed(self, note):
        from src.analytics.reconcile import _expected_delta, _malformed_expected
        assert _expected_delta(note) is None
        assert _malformed_expected(note) is True, (
            f"{note!r} looks like an attempt to declare an expectation and "
            "was treated as no attempt at all"
        )

    @pytest.mark.parametrize("note", [
        "K-1 entity [expected -50.00]", "[expected 1234.56]",
    ])
    def test_valid_tokens_are_not_flagged(self, note):
        """Near-miss: the detector must not fire on the good case, or
        every explained row grows a spurious warning."""
        from src.analytics.reconcile import _malformed_expected
        assert _malformed_expected(note) is False

    def test_accepted_syntax_did_not_widen(self):
        """The fix reports malformed input rather than accepting it.
        Accepting the near-misses would grow a second, undocumented
        format alongside the one CLAUDE.md specifies."""
        from src.analytics.reconcile import _expected_delta
        for note in self.MALFORMED:
            assert _expected_delta(note) is None


class TestTheWarningReachesTheRow:
    """Detection is worth what it reaches — the same lesson as F-017
    tier 3. The message has to land on the reconciliation row the user
    is already looking at."""

    def _rows(self, note):
        from src.analytics.reconcile import compute_reconciliation

        meta = [{"kind": "realized", "account_group": "Broker",
                 "date": "2024", "amount": 1000.0, "note": note}]
        txns = [{"date": "2024-06-01", "account_group": "Broker",
                 "account_type": "Taxable", "symbol": "AAA", "action": "Sell",
                 "quantity": 1.0, "price": 1500.0, "amount": 1500.0,
                 "realized_gain": 1500.0, "fees": 0.0, "description": "",
                 "source": "t.csv"}]
        out = compute_reconciliation(txns, [], meta)
        return (out or {}).get("rows") or []

    def test_a_malformed_token_explains_itself_in_the_detail(self,
                                                             isolated_workdir):
        rows = self._rows("K-1 [expected 1,234.56]")
        assert rows, "no reconciliation row produced"
        detail = rows[0].get("detail") or ""
        assert "could not be read" in detail, (
            f"the row gives no hint that the expectation was ignored: "
            f"{detail!r}"
        )
        assert "thousands separators" in detail, (
            "the message does not say how to fix it"
        )

    def test_a_malformed_token_does_not_become_an_expectation(self,
                                                              isolated_workdir):
        rows = self._rows("K-1 [expected 1,234.56]")
        assert rows[0].get("expected") is None
        assert rows[0].get("residual") is None

    def test_a_clean_row_gets_no_such_warning(self, isolated_workdir):
        rows = self._rows("broker statement")
        assert "could not be read" not in (rows[0].get("detail") or "")

    def test_a_valid_token_still_produces_an_expectation(self,
                                                          isolated_workdir):
        """Not-vacuous guard: the fix must not have broken the feature
        it is protecting."""
        rows = self._rows("K-1 entity [expected 500.00]")
        assert rows[0].get("expected") == pytest.approx(500.0)
        assert "could not be read" not in (rows[0].get("detail") or "")
