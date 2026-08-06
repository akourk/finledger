"""A damaged price cache must never yield a non-number.

Segment 8's error-path sweep. CLAUDE.md documents these shards as
hand-editable and safe to delete:

    Hand-editable (plain JSON; each price shard is
    `{"symbol": ..., "prices": {date: close}}`). Delete freely to force
    a refetch.

So malformed entries are a matter of when, not if — and a hand-edit that
quotes a number is the easiest mistake to make. Before this, `get_price`
guarded only `isinstance(val, float) and isnan(val)`, so a string, a
bool, or an infinity was returned unchanged into
`val *= _tr_factor_after(...)` and then into every `qty * price`.

The rule: anything that is not a finite real number is a **gap**, and
the lookback keeps walking to an earlier real close. That is strictly
better than returning None immediately — a single bad entry no longer
blinds the surrounding days.

Synthetic round numbers only.
"""

from __future__ import annotations

import json

import pytest

GOOD_DAY = "2024-02-28"
BAD_DAY = "2024-02-29"


def _write_shard(workdir, entries: dict) -> None:
    """Write a price shard, allowing non-JSON literals like NaN."""
    p = workdir / "cache" / "prices" / "AAA.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    body = '{"symbol": "AAA", "prices": {' + ", ".join(
        f'"{k}": {v}' for k, v in entries.items()) + "}}"
    p.write_text(body, encoding="utf-8")


@pytest.fixture
def price_of(isolated_workdir):
    def go(entries: dict):
        from src import prices as P
        P.reset_caches()
        _write_shard(isolated_workdir, entries)
        return P.get_price("AAA", BAD_DAY)
    return go


class TestMalformedEntriesAreTreatedAsGaps:

    def test_a_good_entry_is_returned(self, price_of):
        assert price_of({GOOD_DAY: "100.0", BAD_DAY: "110.0"}) == 110.0

    @pytest.mark.parametrize("bad", [
        '"110.0"',      # hand-edit quoted the number — the likely mistake
        "null",
        "NaN",
        "Infinity",
        "-Infinity",
        "true",
        '"n/a"',
        "[110.0]",
        '{"close": 110.0}',
    ])
    def test_a_malformed_entry_falls_back_to_the_prior_day(self, price_of,
                                                           bad):
        """The lookback must WALK PAST the bad entry, not stop at it."""
        got = price_of({GOOD_DAY: "100.0", BAD_DAY: bad})
        assert got == 100.0, (
            f"entry {bad} should have been skipped as a gap, leaving the "
            f"prior day's 100.0; got {got!r}"
        )

    @pytest.mark.parametrize("bad", ['"110.0"', "NaN", "Infinity", "true"])
    def test_a_malformed_entry_with_no_prior_day_returns_none(self, price_of,
                                                              bad):
        got = price_of({BAD_DAY: bad})
        assert got is None, f"expected None, got {got!r}"

    def test_the_result_is_always_a_real_number_or_none(self, price_of):
        """The contract callers rely on. `qty * price` must never meet a
        str, a bool, a list or an infinity."""
        import math

        for bad in ('"110.0"', "null", "NaN", "Infinity", "true", "[1]"):
            got = price_of({GOOD_DAY: "100.0", BAD_DAY: bad})
            assert got is None or (
                isinstance(got, (int, float))
                and not isinstance(got, bool)
                and math.isfinite(got)
            ), f"get_price returned {got!r} ({type(got).__name__}) for {bad}"

    def test_an_integer_price_is_accepted(self, price_of):
        """Ints are legitimate — the guard must not reject them along
        with the junk."""
        assert price_of({BAD_DAY: "110"}) == 110

    def test_zero_is_a_real_price_not_a_gap(self, price_of):
        """A genuine 0.0 close (a written-off position) must survive;
        rejecting falsy values would silently substitute the prior day."""
        assert price_of({GOOD_DAY: "100.0", BAD_DAY: "0.0"}) == 0.0


class TestUnreadableShards:
    """Whole-file damage, as distinct from a bad entry."""

    @pytest.mark.parametrize("body", [
        '{"symbol": "AAA", "pri',   # truncated mid-write
        "",                          # empty
        "[1, 2, 3]",                 # valid JSON, wrong shape
    ])
    def test_unreadable_shard_yields_none_not_a_crash(self, isolated_workdir,
                                                      body):
        from src import prices as P

        P.reset_caches()
        p = isolated_workdir / "cache" / "prices" / "AAA.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        assert P.get_price("AAA", BAD_DAY) is None
