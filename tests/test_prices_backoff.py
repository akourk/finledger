"""Failure backoff and coverage-claim monotonicity.

Both behaviours are documented in CLAUDE.md and neither had a test —
found by mutation, where `min` → `max` in the backoff and `max` → `min`
in the `covered_end` merge both survived the full suite.

Why they matter:

* **Backoff** is documented as `1d → 2d → 4d → 8d → 16d, capped at 30d`.
  Swapping the `min` for a `max` inverts the cap into a *floor*, so a
  symbol's FIRST transient failure waits a month instead of a day. Every
  delisted-looking-but-alive ticker would go dark for 30 days at a time,
  and the dashboard would price it from stale marks with nothing flagged
  (`failure_count` is only surfaced as an `info` alert).
* **`covered_end` must never move backwards.** CLAUDE.md records two
  separate bugs in this field. If a later successful fetch could shrink
  the claim, `_missing_ranges` would keep reporting the same gap and the
  symbol would be refetched on every run forever.

Synthetic values only.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest


@pytest.fixture
def prices(isolated_workdir):
    from src import prices as P
    P.reset_caches()
    return P


class TestFailureBackoffSchedule:

    def test_backoff_doubles_then_caps(self, prices):
        """The documented sequence, asserted as a sequence.

        Each failure is applied in turn and the resulting `retry_after`
        measured as a delta from the same instant, so this pins the
        SHAPE of the curve, not one point on it.
        """
        now = datetime(2026, 1, 1, 12, 0, 0)
        expected = [1, 2, 4, 8, 16, 30, 30, 30]

        got = []
        for _ in expected:
            prices._record_failure("FAKE", "boom", now)
            entry = prices._load_meta()["symbols"]["FAKE"]
            delta = date.fromisoformat(entry["retry_after"]) - now.date()
            got.append(delta.days)

        assert got == expected, (
            f"backoff schedule drifted: {got} != {expected} — CLAUDE.md "
            "documents 1/2/4/8/16 doubling, capped at 30 days"
        )

    def test_first_failure_retries_the_next_day(self, prices):
        """The single most important point on the curve: a transient
        network blip must not silence a symbol for weeks."""
        now = datetime(2026, 1, 1, 12, 0, 0)
        prices._record_failure("FAKE", "boom", now)
        entry = prices._load_meta()["symbols"]["FAKE"]
        assert entry["retry_after"] == "2026-01-02"

    def test_backoff_never_exceeds_the_cap(self, prices):
        now = datetime(2026, 1, 1, 12, 0, 0)
        for _ in range(20):
            prices._record_failure("FAKE", "boom", now)
        entry = prices._load_meta()["symbols"]["FAKE"]
        delta = date.fromisoformat(entry["retry_after"]) - now.date()
        assert delta.days <= 30, (
            "an unbounded backoff means a re-listed ticker is never retried"
        )

    def test_repeated_failures_tombstone_the_symbol(self, prices):
        now = datetime(2026, 1, 1, 12, 0, 0)
        for i in range(1, 6):
            prices._record_failure("FAKE", "boom", now)
            entry = prices._load_meta()["symbols"]["FAKE"]
            if i < 5:
                assert not entry.get("tombstone"), (
                    f"tombstoned after only {i} failure(s) — a symbol with a "
                    "flaky feed would be abandoned"
                )
        assert entry.get("tombstone") is True


class TestCoveredEndNeverMovesBackwards:

    def test_a_later_fetch_does_not_shrink_the_claim(self, prices):
        """`covered_end` is a high-water mark.

        Fetching an OLDER range afterwards (a backfill) must leave the
        claim where it was — otherwise `_missing_ranges` re-reports the
        same forward gap on every run.
        """
        now = datetime(2026, 1, 1, 12, 0, 0)

        # Real code path, not a re-implementation of it. The first draft
        # of this test computed `max(ce, end_s)` itself and asserted on
        # its own arithmetic — so mutating the source line left it green.
        # A test must drive the function it is protecting.
        prices._record_success("FAKE", date(2024, 1, 1), date(2024, 6, 1), now)
        assert prices._load_meta()["symbols"]["FAKE"]["covered_end"] == "2024-06-01"

        # Now a BACKFILL of an earlier window.
        prices._record_success("FAKE", date(2023, 1, 1), date(2024, 3, 1), now)
        entry = prices._load_meta()["symbols"]["FAKE"]

        assert entry["covered_end"] == "2024-06-01", (
            "a backfill shrank the coverage claim — _missing_ranges would "
            "re-report the same forward gap on every run, forever"
        )
        assert entry["covered_start"] == "2023-01-01", (
            "covered_start must extend BACKWARDS to take in the backfill"
        )

    def test_the_claim_is_capped_at_today(self, prices):
        """A future-dated crypto bar (UTC-dated, received on an evening
        local run) is kept in the shard but must not be CLAIMED —
        otherwise tomorrow looks already-fetched and the symbol is
        skipped for a whole local day."""
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        assert prices._cap_covered_end(tomorrow) == date.today().isoformat()

    def test_a_past_claim_passes_through_unchanged(self, prices):
        assert prices._cap_covered_end("2020-05-05") == "2020-05-05"
