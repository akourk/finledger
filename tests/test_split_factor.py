"""`split_factor_since` — the function CLAUDE.md says gives "a wildly
wrong number" when skipped, and which nothing asserted.

It converts an as-of-date share count into today's basis so it can be
multiplied against yfinance's historical `Close`, which is *always*
split-adjusted regardless of `auto_adjust`. The cache therefore stores
one price per date in today's basis, and the correction happens on the
BALANCE side:

    today_basis_qty = as_of_qty * split_factor_since(sym, date)
    value           = today_basis_qty * close_on(date)

Every snapshot in the history chart, every daily-total walk and every
TWR period boundary runs through it.

Coverage said the loop executed; mutation said nothing checked it. Two
mutants survived the whole suite:

* `if d > target` -> `if d >= target` — counting a split that happened
  ON the sample date. yfinance's close for that date is already
  post-split, so counting it double-applies the ratio to exactly one
  snapshot.
* dropping the direct-proxy forward, which silently un-splits every
  proxied holding.

**Reverse splits are the reason the direction matters most.** A 1-for-10
reverse split has ratio 0.1, so a pre-split balance must SHRINK to reach
today's basis while the historical close is correspondingly larger.
Invert the operation and the position is off by 100x. CLAUDE.md names
reverse-split penny stocks specifically, and the sample cannot reach
this path — the shipped snapshot carries no split history, since splits
come from yfinance rather than from the CSVs.

Synthetic tickers and round ratios only.
"""

from __future__ import annotations

import json

import pytest


def _seed_splits(workdir, mapping: dict) -> None:
    """Write `cache/splits_cache.json` directly.

    Splits come from yfinance, never from transaction data, so seeding
    the cache is the only way to reach this function offline.
    """
    (workdir / "cache" / "splits_cache.json").write_text(
        json.dumps(mapping), encoding="utf-8")


@pytest.fixture
def factor(isolated_workdir):
    def go(mapping: dict, symbol: str, as_of: str, proxies: dict | None = None):
        from src import prices as P

        P.reset_caches()
        _seed_splits(isolated_workdir, mapping)
        if proxies is not None:
            (isolated_workdir / "cache" / "symbol_proxy_map.json").write_text(
                json.dumps(proxies), encoding="utf-8")
        return P.split_factor_since(symbol, as_of)
    return go


class TestForwardSplits:

    def test_no_split_history_is_one(self, factor):
        assert factor({}, "AAA", "2024-01-01") == 1.0

    def test_an_empty_split_list_is_one(self, factor):
        assert factor({"AAA": []}, "AAA", "2024-01-01") == 1.0

    def test_a_later_split_scales_the_quantity_up(self, factor):
        """10 shares held before a 2-for-1 are 20 in today's basis."""
        assert factor({"AAA": [["2024-06-01", 2.0]]}, "AAA",
                      "2024-01-01") == pytest.approx(2.0)

    def test_an_earlier_split_is_not_counted(self, factor):
        """Already reflected in the share count on that date."""
        assert factor({"AAA": [["2023-06-01", 2.0]]}, "AAA",
                      "2024-01-01") == pytest.approx(1.0)

    def test_successive_splits_multiply(self, factor):
        assert factor({"AAA": [["2024-06-01", 2.0], ["2025-06-01", 4.0]]},
                      "AAA", "2024-01-01") == pytest.approx(8.0)

    def test_only_the_splits_after_the_date_multiply(self, factor):
        """Two of each: one split on either side of the sample date, with
        DIFFERENT ratios so which ones are counted is observable.

        With equal ratios, counting the wrong subset can still land on
        the right product.
        """
        assert factor({"AAA": [["2023-06-01", 3.0], ["2025-06-01", 5.0]]},
                      "AAA", "2024-01-01") == pytest.approx(5.0)

    def test_unrelated_symbols_are_ignored(self, factor):
        assert factor({"BBB": [["2024-06-01", 2.0]]}, "AAA",
                      "2024-01-01") == 1.0


class TestTheStrictlyAfterBoundary:
    """The surviving mutant. The docstring says splits *strictly after*
    the date, and it has to be strict: yfinance's close for the split
    date is already the post-split price, so a balance dated that same
    day needs no correction. Count it and exactly one snapshot is off by
    the full ratio — the shape that looks like a one-day spike rather
    than a systematic error, which is what makes it hard to spot.
    """

    SPLIT = {"AAA": [["2024-06-01", 2.0]]}

    def test_a_split_on_the_sample_date_is_not_counted(self, factor):
        assert factor(self.SPLIT, "AAA", "2024-06-01") == pytest.approx(1.0), (
            "a split dated the same day as the balance was applied — the "
            "close on the split date is already post-split"
        )

    def test_the_day_before_is_counted(self, factor):
        """Near-miss, one day the other side of the boundary."""
        assert factor(self.SPLIT, "AAA", "2024-05-31") == pytest.approx(2.0)

    def test_the_day_after_is_not_counted(self, factor):
        assert factor(self.SPLIT, "AAA", "2024-06-02") == pytest.approx(1.0)


class TestReverseSplits:
    """A reverse split has ratio < 1, so a pre-split balance must SHRINK
    to reach today's basis.

    This is the direction that fails loudest and the one the sample
    cannot reach. It is also the case where a sign or reciprocal error
    stops being a rounding difference: at 1-for-10, inverting the
    operation misvalues the position by 100x.
    """

    def test_a_reverse_split_scales_the_quantity_down(self, factor):
        """100 shares before a 1-for-10 are 10 in today's basis."""
        assert factor({"AAA": [["2024-06-01", 0.1]]}, "AAA",
                      "2024-01-01") == pytest.approx(0.1)

    def test_the_value_is_preserved_across_a_reverse_split(self, factor):
        """The property the whole mechanism exists for.

        Held 100 shares at $2 (= $200) before a 1-for-10. yfinance
        restates that date's close as $20, so the adjusted quantity must
        come out at 10 for the snapshot to still read $200.
        """
        f = factor({"AAA": [["2024-06-01", 0.1]]}, "AAA", "2024-01-01")
        as_of_qty, restated_close = 100.0, 20.0
        assert as_of_qty * f * restated_close == pytest.approx(200.0), (
            "the reverse split did not preserve the snapshot's value — an "
            "inverted ratio here is a 100x error, not a rounding one"
        )

    def test_a_reverse_then_forward_split_multiplies_both(self, factor):
        assert factor({"AAA": [["2024-06-01", 0.1], ["2025-06-01", 3.0]]},
                      "AAA", "2024-01-01") == pytest.approx(0.3)

    def test_a_reverse_split_before_the_date_is_ignored(self, factor):
        assert factor({"AAA": [["2023-06-01", 0.1]]}, "AAA",
                      "2024-01-01") == pytest.approx(1.0)


class TestProxiedSymbols:
    """The second surviving mutant.

    A DIRECT proxy (BRK.B -> BRK-B) is the same security under a
    different ticker string, so the proxy's splits are the user's
    splits and must forward.

    A SCALED proxy is a different fund tracked by return ratio; its
    splits are already inside that ratio, and the user's shares of the
    original fund are unaffected by them. Forwarding there would apply a
    split that never happened to the holding.
    """

    SPLITS = {"REAL": [["2024-06-01", 2.0]]}

    def test_a_direct_proxy_forwards_the_split_history(self, factor):
        got = factor(self.SPLITS, "FAKE", "2024-01-01",
                     proxies={"FAKE": {"proxy": "REAL", "method": "direct"}})
        assert got == pytest.approx(2.0), (
            "a direct proxy did not inherit its target's splits — the "
            "holding is the same security under another ticker"
        )

    def test_a_scaled_proxy_does_not_forward(self, factor):
        got = factor(self.SPLITS, "FAKE", "2024-01-01",
                     proxies={"FAKE": {"proxy": "REAL", "method": "scaled",
                                       "anchor_date": "2024-01-01",
                                       "anchor_price": 100.0}})
        assert got == pytest.approx(1.0), (
            "a scaled proxy inherited its target's splits — the user's "
            "shares of the original fund never split"
        )

    def test_an_unmapped_symbol_uses_its_own_history(self, factor):
        """Not-vacuous guard: without this, both proxy tests would pass
        on a function that ignored the proxy map entirely."""
        assert factor(self.SPLITS, "REAL", "2024-01-01",
                      proxies={}) == pytest.approx(2.0)


class TestSplitAdjustQty:
    """The thin convenience wrapper, which was on the never-executed
    list."""

    def test_it_applies_the_factor(self, isolated_workdir):
        from src import prices as P

        P.reset_caches()
        _seed_splits(isolated_workdir, {"AAA": [["2024-06-01", 2.0]]})
        assert P.split_adjust_qty("AAA", 10.0, "2024-01-01") == \
            pytest.approx(20.0)

    def test_it_shrinks_across_a_reverse_split(self, isolated_workdir):
        from src import prices as P

        P.reset_caches()
        _seed_splits(isolated_workdir, {"AAA": [["2024-06-01", 0.1]]})
        assert P.split_adjust_qty("AAA", 100.0, "2024-01-01") == \
            pytest.approx(10.0)
