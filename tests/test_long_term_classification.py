"""ST vs LT classification — Segment 7 item 4.

`_is_long_term` decides whether a realized gain is taxed at ordinary
income rates or at the (much lower) long-term capital gains rates. It is
one of the highest-consequence single predicates in the codebase, and it
had **no test**: `test_lots.py` pins `_lt_eligible_date` for the leap
day, but nothing exercised the function that consumes it, and nothing
exercised its day-count fallback at all.

The rule (IRS Topic 409): begin counting the day AFTER acquisition; the
disposal date counts. So one year of holding completes on the
anniversary, and "more than one year" — the LT test — begins the day
after. A sale exactly ON the anniversary is SHORT-term.

The naive `days > 365` test gets that wrong whenever the holding window
spans a Feb 29, which is exactly why the calendar path exists. That
disagreement is asserted directly below rather than described.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest


@pytest.fixture
def lt(isolated_workdir):
    from src.analytics.tax import _is_long_term
    return _is_long_term


class TestCalendarPath:
    """Both dates known — the calendar rule governs."""

    def test_sale_on_the_anniversary_is_short_term(self, lt):
        """One year held exactly is NOT more than one year."""
        assert lt("2023-06-15", "2024-06-15", None) is False

    def test_sale_the_day_after_the_anniversary_is_long_term(self, lt):
        assert lt("2023-06-15", "2024-06-16", None) is True

    def test_sale_the_day_before_the_anniversary_is_short_term(self, lt):
        assert lt("2023-06-15", "2024-06-14", None) is False

    def test_same_day_round_trip_is_short_term(self, lt):
        assert lt("2024-01-10", "2024-01-10", None) is False

    def test_many_years_is_long_term(self, lt):
        assert lt("2015-01-01", "2024-01-01", None) is True

    @pytest.mark.parametrize("acquired,first_lt_day", [
        ("2024-02-29", "2025-03-01"),   # leap day -> Feb 28 + 1 = Mar 1
        ("2023-02-28", "2024-02-29"),   # into a leap year -> Feb 29 exists
        ("2023-12-31", "2025-01-01"),
        ("2023-01-01", "2024-01-02"),
    ])
    def test_first_long_term_day_boundary(self, lt, acquired, first_lt_day):
        """The exact boundary, asserted from both sides."""
        d = date.fromisoformat(first_lt_day)
        day_before = (d - timedelta(days=1)).isoformat()
        assert lt(acquired, day_before, None) is False, (
            f"{acquired} sold {day_before} should be SHORT-term"
        )
        assert lt(acquired, first_lt_day, None) is True, (
            f"{acquired} sold {first_lt_day} should be LONG-term"
        )


class TestTheLeapYearDisagreement:
    """The case the calendar path exists for.

    Acquired 2024-02-28, sold 2025-02-28. Because 2024 is a leap year the
    window contains Feb 29, so it spans **366** days — and a naive
    `days > 365` test calls that long-term. It is not: the sale lands
    exactly on the one-year anniversary, which is short-term.

    Getting this backwards taxes the gain at long-term rates when the IRS
    says ordinary — a real understatement of tax, not a rounding issue.
    """

    ACQUIRED, SOLD = "2024-02-28", "2025-02-28"

    def test_the_window_really_does_span_366_days(self):
        delta = date.fromisoformat(self.SOLD) - date.fromisoformat(self.ACQUIRED)
        assert delta.days == 366, (
            "fixture no longer reproduces the disagreement — pick another "
            "leap-spanning window"
        )

    def test_naive_day_count_would_say_long_term(self):
        assert 366 > 365

    def test_but_the_calendar_rule_says_short_term(self, lt):
        assert lt(self.ACQUIRED, self.SOLD, 366) is False, (
            "a sale on the one-year anniversary is SHORT-term even when the "
            "window spans 366 days because of a leap day"
        )

    def test_and_the_next_day_is_long_term(self, lt):
        assert lt(self.ACQUIRED, "2025-03-01", 367) is True

    def test_the_days_fallback_is_ignored_when_both_dates_are_known(self, lt):
        """A wildly wrong day count must not override real dates."""
        assert lt("2023-06-15", "2024-06-15", 99999) is False
        assert lt("2023-06-15", "2024-06-16", 0) is True


class TestDayCountFallback:
    """Used when a date is missing or unparseable — e.g. a broker's
    `VARIOUS` acquired date on a consolidated 1099 row."""

    @pytest.mark.parametrize("acquired,sold", [
        (None, "2024-06-15"),
        ("2023-06-15", None),
        ("", "2024-06-15"),
        ("VARIOUS", "2024-06-15"),
        ("2023-06-15", "not-a-date"),
    ])
    def test_falls_back_when_a_date_is_unusable(self, lt, acquired, sold):
        assert lt(acquired, sold, 400) is True
        assert lt(acquired, sold, 100) is False

    def test_fallback_boundary_is_strictly_greater_than_365(self, lt):
        assert lt(None, None, 365) is False, "365 days is exactly one year"
        assert lt(None, None, 366) is True

    @pytest.mark.parametrize("bad", [None, "", "many", float("nan")])
    def test_unusable_day_count_is_not_long_term(self, lt, bad):
        """With neither dates nor a usable count, the safe answer is
        SHORT-term: it is the higher tax rate, so an error cannot
        understate what is owed."""
        assert lt(None, None, bad) is False
