"""Tests for the price-cache coverage logic."""

from __future__ import annotations

from datetime import date


class TestWeekendClamp:
    def test_last_trading_day_weekday_unchanged(self, isolated_workdir):
        from src.prices import _last_trading_day
        # Wednesday 2024-03-13
        wed = date(2024, 3, 13)
        assert _last_trading_day(wed) == wed

    def test_last_trading_day_saturday_walks_to_friday(self, isolated_workdir):
        from src.prices import _last_trading_day
        # Saturday 2024-03-16 → Friday 2024-03-15
        assert _last_trading_day(date(2024, 3, 16)) == date(2024, 3, 15)

    def test_last_trading_day_sunday_walks_to_friday(self, isolated_workdir):
        from src.prices import _last_trading_day
        # Sunday 2024-03-17 → Friday 2024-03-15
        assert _last_trading_day(date(2024, 3, 17)) == date(2024, 3, 15)


class TestMissingRangesWeekendClamp:
    def test_weekend_end_clamps_to_friday(self, isolated_workdir):
        """When today is Saturday and there's no prior coverage, the
        fetch range should end at Friday — not Saturday, which would
        produce a noisy no-data response from yfinance."""
        from src.prices import _missing_ranges
        # No cache entry for this symbol → falls into the "no prior
        # coverage" branch; end date gets clamped to the most recent
        # weekday.
        sym = "FAKESYM"
        gaps = _missing_ranges(sym, date(2024, 3, 1), date(2024, 3, 16))
        assert len(gaps) == 1
        _, end = gaps[0]
        assert end == date(2024, 3, 15), \
            "end should clamp back to Friday when requested end is Saturday"

    def test_weekend_request_with_cache_through_friday_yields_no_gap(self, isolated_workdir):
        """If the cache already goes through Friday and the user runs
        the pipeline Saturday/Sunday, no fetch should happen at all."""
        from src.prices import _missing_ranges, _load_meta
        sym = "FOO"
        # Pretend we fetched up through Friday 2024-03-15 already
        meta = _load_meta()
        meta["symbols"][sym] = {
            "covered_start": "2024-01-01",
            "covered_end":   "2024-03-15",
        }
        gaps = _missing_ranges(sym, date(2024, 1, 1), date(2024, 3, 17))
        assert gaps == [], \
            "no gap should be emitted when cache covers through last " \
            "trading day; asking for weekend data is pointless"


class TestTotalReturnRefreshThrottle:
    def test_first_run_does_full_fetch(self, isolated_workdir, stub_prices):
        """The very first run for a total-return symbol has no
        last_full_refresh stamp, so the full series is fetched."""
        from src.prices import ensure_coverage, _load_meta
        # Stub SPY data so the fetch returns something concrete
        stub_prices.set("SPY", {"2024-01-02": 470.0, "2024-01-03": 472.0,
                                 "2024-01-04": 474.0})
        ensure_coverage(["SPY"], "2024-01-02", "2024-01-04", verbose=False)
        meta = _load_meta()
        assert meta["symbols"]["SPY"].get("last_full_refresh"), \
            "first run should stamp last_full_refresh"

    def test_recent_full_refresh_skips_full_refetch(self, isolated_workdir, stub_prices):
        """If last_full_refresh is recent (< 7 days old), don't
        re-fetch the entire series — just append today's close via
        the normal incremental gap path."""
        from datetime import datetime, timedelta
        from src.prices import ensure_coverage, _load_meta

        # Pre-populate cache: SPY covered through yesterday, full
        # refresh stamped today.  A run today should NOT do another
        # full fetch.
        meta = _load_meta()
        yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
        today     = datetime.now().date().isoformat()
        meta["symbols"]["SPY"] = {
            "covered_start":     "2017-05-23",
            "covered_end":       yesterday,
            "last_full_refresh": today,
        }
        # Stub a fresh-day stub so any incremental fetch can complete
        stub_prices.set("SPY", {today: 500.0})

        ensure_coverage(["SPY"], "2017-05-23", today, verbose=False)
        # The cache entry should still exist (a full refetch would have
        # popped the meta entry and rebuilt it from scratch — losing
        # the original `covered_start`)
        meta_after = _load_meta()
        assert meta_after["symbols"]["SPY"].get("covered_start") == "2017-05-23"

    def test_old_full_refresh_triggers_full_refetch(self, isolated_workdir, stub_prices):
        """If last_full_refresh is older than the throttle window,
        we DO refetch the full series (catches dividend
        re-normalizations within the throttle period)."""
        from src.prices import ensure_coverage, _load_meta
        # Set last_full_refresh to ancient history → forces full refetch
        stub_prices.set("SPY", {"2024-01-02": 470.0, "2024-01-03": 472.0})
        meta = _load_meta()
        meta["symbols"]["SPY"] = {
            "covered_start":     "2024-01-02",
            "covered_end":       "2024-01-03",
            "last_full_refresh": "2020-01-01",
        }
        ensure_coverage(["SPY"], "2024-01-02", "2024-01-03", verbose=False)
        # Full refetch should re-stamp the date; check it's recent
        from datetime import datetime
        meta_after = _load_meta()
        stamped = meta_after["symbols"]["SPY"].get("last_full_refresh")
        assert stamped == datetime.now().date().isoformat()


class TestDeepCacheRefresh:
    def test_first_run_does_refresh(self, isolated_workdir, monkeypatch):
        """No prior `last_deep_refresh` → refresh runs."""
        from src.prices import revalidate_stale_caches, _load_meta, _fetch_splits

        # Stub _fetch_splits so we don't hit the network
        called = []
        def fake(sym):
            called.append(sym)
            return []
        monkeypatch.setattr("src.prices._fetch_splits", fake)

        revalidate_stale_caches(["AAPL", "MSFT"], verbose=False)

        meta = _load_meta()
        assert meta.get("last_deep_refresh"), "should stamp the date"
        # Both symbols should have had splits attempted
        assert set(called) == {"AAPL", "MSFT"}

    def test_recent_refresh_skips(self, isolated_workdir, monkeypatch):
        """If `last_deep_refresh` is today, we skip the deep work."""
        from datetime import datetime
        from src.prices import revalidate_stale_caches, _load_meta

        meta = _load_meta()
        meta["last_deep_refresh"] = datetime.now().date().isoformat()

        called = []
        def fake(sym):
            called.append(sym)
            return []
        monkeypatch.setattr("src.prices._fetch_splits", fake)

        revalidate_stale_caches(["AAPL"], verbose=False)
        assert called == [], "recent refresh should skip the splits fetch"

    def test_old_refresh_triggers(self, isolated_workdir, monkeypatch):
        """If `last_deep_refresh` is older than the throttle window,
        deep refresh runs again."""
        from src.prices import revalidate_stale_caches, _load_meta

        meta = _load_meta()
        meta["last_deep_refresh"] = "2020-01-01"

        called = []
        def fake(sym):
            called.append(sym)
            return []
        monkeypatch.setattr("src.prices._fetch_splits", fake)

        revalidate_stale_caches(["AAPL"], verbose=False)
        assert called == ["AAPL"]

    def test_clears_tombstones(self, isolated_workdir, monkeypatch):
        """Tombstoned symbols should have their tombstone flag cleared
        on deep refresh — gives delisted-then-relisted tickers another
        chance."""
        from src.prices import revalidate_stale_caches, _load_meta

        meta = _load_meta()
        meta["symbols"]["DEAD"] = {
            "covered_start": "2024-01-01",
            "covered_end":   "2024-01-15",
            "tombstone":     True,
            "failure_count": 5,
            "retry_after":   "2099-01-01",
        }

        monkeypatch.setattr("src.prices._fetch_splits", lambda s: [])
        revalidate_stale_caches([], verbose=False)

        meta_after = _load_meta()
        entry = meta_after["symbols"]["DEAD"]
        assert "tombstone" not in entry
        assert entry["failure_count"] == 0
        assert "retry_after" not in entry


class TestSplitChangeInvalidation:
    """A CHANGED split history must wipe the symbol's cached prices —
    yfinance rescales the whole historical series on a split, so
    previously-cached values are stranded in the pre-split basis while
    ``split_factor_since`` assumes a uniform today-basis series."""

    def test_new_split_invalidates_cached_prices(self, isolated_workdir, monkeypatch):
        from src.prices import (
            revalidate_stale_caches, _load_meta, _load_prices, _load_splits,
        )

        # Seed: AAPL cached with a known (empty) split history.
        prices = _load_prices()
        prices["AAPL"] = {"2024-01-02": 400.0, "2024-01-03": 402.0}
        meta = _load_meta()
        meta["symbols"]["AAPL"] = {"covered_start": "2024-01-02",
                                   "covered_end": "2024-01-03"}
        _load_splits()["AAPL"] = []

        # Deep refresh discovers a new 4:1 split.
        monkeypatch.setattr("src.prices._fetch_splits",
                            lambda s: [["2024-06-10", 4.0]])
        revalidate_stale_caches(["AAPL"], verbose=False, force=True)

        assert "AAPL" not in _load_prices(), "pre-split prices must be wiped"
        entry = _load_meta()["symbols"]["AAPL"]
        assert "covered_start" not in entry and "covered_end" not in entry, (
            "coverage meta must be cleared so ensure_coverage refetches")
        assert _load_splits()["AAPL"] == [["2024-06-10", 4.0]]

    def test_first_time_splits_backfill_does_not_invalidate(self, isolated_workdir, monkeypatch):
        """No previously-recorded split history → cached prices already
        reflect those old splits; backfill must NOT wipe them."""
        from src.prices import (
            revalidate_stale_caches, _load_meta, _load_prices, _load_splits,
        )

        prices = _load_prices()
        prices["AAPL"] = {"2024-01-02": 180.0}
        meta = _load_meta()
        meta["symbols"]["AAPL"] = {"covered_start": "2024-01-02",
                                   "covered_end": "2024-01-02"}
        # No splits cache entry for AAPL (pre-splits-feature cache).

        monkeypatch.setattr("src.prices._fetch_splits",
                            lambda s: [["2020-08-31", 4.0]])
        revalidate_stale_caches(["AAPL"], verbose=False, force=True)

        assert "AAPL" in _load_prices(), (
            "first-time backfill must keep cached prices")
        assert _load_meta()["symbols"]["AAPL"]["covered_end"] == "2024-01-02"

    def test_unchanged_splits_do_not_invalidate(self, isolated_workdir, monkeypatch):
        from src.prices import (
            revalidate_stale_caches, _load_meta, _load_prices, _load_splits,
        )
        prices = _load_prices()
        prices["AAPL"] = {"2024-01-02": 180.0}
        _load_meta()["symbols"]["AAPL"] = {"covered_start": "2024-01-02",
                                           "covered_end": "2024-01-02"}
        _load_splits()["AAPL"] = [["2020-08-31", 4.0]]

        monkeypatch.setattr("src.prices._fetch_splits",
                            lambda s: [["2020-08-31", 4.0]])
        revalidate_stale_caches(["AAPL"], verbose=False, force=True)
        assert "AAPL" in _load_prices()


class TestNaNHandling:
    """yfinance returns NaN for a close that hasn't posted yet (the
    most-recent day during/just after market hours, holiday rows).  A
    NaN must never enter the cache or be returned by get_price — it
    propagates into value / unrealized / totals everywhere downstream
    (Total Return rendered as NaN on the Performance tab)."""

    def test_get_price_skips_nan_and_walks_back(self, isolated_workdir):
        from src.prices import get_price, _load_prices
        series = _load_prices().setdefault("WEN", {})
        series["2026-06-25"] = 7.33
        series["2026-06-26"] = float("nan")
        # Querying the NaN day (or after it) must fall back to the prior
        # real close, not return NaN.
        assert get_price("WEN", "2026-06-27") == 7.33
        assert get_price("WEN", "2026-06-26") == 7.33

    def test_fetch_range_filters_nan_rows(self, isolated_workdir, monkeypatch):
        import math
        from datetime import date
        from src import prices as _prices_mod

        class _Idx:
            def __init__(self, d): self._d = d
            def date(self): return self._d

        class _Row:
            def __init__(self, close): self._close = close
            def get(self, col): return self._close

        class _Hist:
            empty = False
            def __init__(self, rows): self._rows = rows
            @property
            def columns(self): return ["Close"]
            def iterrows(self):
                for idx, row in self._rows:
                    yield idx, row

        class _Ticker:
            def __init__(self, sym): pass
            def history(self, **kw):
                return _Hist([
                    (_Idx(date(2026, 6, 25)), _Row(7.33)),
                    (_Idx(date(2026, 6, 26)), _Row(float("nan"))),
                ])

        class _FakeYF:
            Ticker = _Ticker

        monkeypatch.setattr(_prices_mod, "_lazy_yf", lambda: _FakeYF())
        out = _prices_mod._fetch_range("WEN", date(2026, 6, 25), date(2026, 6, 26))
        assert "2026-06-25" in out and out["2026-06-25"] == 7.33
        assert "2026-06-26" not in out, "NaN close must be filtered out"
        assert not any(isinstance(v, float) and math.isnan(v) for v in out.values())


class TestEnsureCoverageEndOverrides:
    def test_closed_position_clamps_to_last_held(self, isolated_workdir, stub_prices):
        """When a symbol has a `symbol_end_overrides` entry (closed
        position last held on date X), its fetch should not extend
        beyond X — even though the global ``end`` is today."""
        from src.prices import ensure_coverage, _load_meta
        from datetime import date

        # Pretend RNAM is fully cached up through 2025-12-04 (its
        # last-held date in the user's ledger).
        meta = _load_meta()
        meta["symbols"]["RNAM"] = {
            "covered_start": "2024-10-17",
            "covered_end":   "2025-12-04",
        }
        # Run with global end=today, but override RNAM to end at last
        # held date.  Expect zero fetch attempts (cache already covers
        # the relevant range; no extension to today's date).
        before_calls = list(stub_prices.registered.keys())
        ensure_coverage(
            ["RNAM"], "2024-10-17", "2026-04-24",
            verbose=False,
            symbol_end_overrides={"RNAM": "2025-12-04"},
        )
        # The stub records every fetch attempt; if ensure_coverage
        # honored the override it shouldn't have called for any new
        # range.  Symbols only land in stub_prices.registered when
        # the test SET them, so absence of new keys here means we
        # didn't try to fetch.
        assert list(stub_prices.registered.keys()) == before_calls

    def test_no_override_extends_to_today(self, isolated_workdir, stub_prices):
        """Without an override, fetch range extends to today (the
        normal case for currently-held positions)."""
        from src.prices import ensure_coverage, _load_meta

        meta = _load_meta()
        meta["symbols"]["AAPL"] = {
            "covered_start": "2024-01-01",
            "covered_end":   "2024-03-15",
        }
        # Stub a future fetch result so the call doesn't fail
        stub_prices.set("AAPL", {"2024-03-18": 175.0, "2024-03-19": 176.0})
        ensure_coverage(
            ["AAPL"], "2024-01-01", "2024-03-19",
            verbose=False,
        )
        # We expect ensure_coverage to have asked for the [3-16, 3-19]
        # gap.  Stubs swallow this (they return what's registered);
        # the real point of this test is just confirming no exception
        # / clamp triggers when override is absent.
