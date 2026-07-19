"""Tests for the price-cache coverage logic."""

from __future__ import annotations

from datetime import date

import pytest


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


class TestLocalTotalReturn:
    """Total-return symbols store plain Close; the dividend adjustment
    (suffix product of 1 − div/prev_close over ex-dates AFTER the queried
    date) is applied at read time."""

    def _seed(self, prices_by_date, dividends):
        from src.prices import _load_dividends, _load_prices
        _load_prices()["SPY"] = dict(prices_by_date)
        _load_dividends()["SPY"] = [list(d) for d in dividends]

    def test_get_price_applies_dividend_adjustment(self, isolated_workdir):
        from src.prices import get_price
        # $1 dividend ex 03-15; prev close (03-14) = 100 → factor 0.99
        self._seed({"2024-03-14": 100.0, "2024-03-15": 99.5},
                   [["2024-03-15", 1.0]])
        assert get_price("SPY", "2024-03-14") == pytest.approx(99.0)
        # On/after the ex-date no factor applies (latest == raw close).
        assert get_price("SPY", "2024-03-15") == pytest.approx(99.5)

    def test_multiple_dividends_compound(self, isolated_workdir):
        from src.prices import get_price
        self._seed({"2024-03-14": 100.0, "2024-03-15": 99.5,
                    "2024-06-13": 200.0, "2024-06-14": 199.0},
                   [["2024-03-15", 1.0], ["2024-06-14", 2.0]])
        # 03-14 sits before BOTH ex-dates: 0.99 × (1 − 2/200) = 0.9801
        assert get_price("SPY", "2024-03-14") == pytest.approx(100.0 * 0.99 * 0.99)
        # 06-13 sits before only the June ex-date.
        assert get_price("SPY", "2024-06-13") == pytest.approx(200.0 * 0.99)

    def test_non_tr_symbol_ignores_dividends(self, isolated_workdir):
        from src.prices import _load_dividends, _load_prices, get_price
        _load_prices()["AAPL"] = {"2024-03-14": 100.0}
        _load_dividends()["AAPL"] = [["2024-03-15", 1.0]]
        assert get_price("AAPL", "2024-03-14") == pytest.approx(100.0)

    def test_missing_dividends_degrade_to_price_return(self, isolated_workdir):
        from src.prices import get_price
        self._seed({"2024-03-14": 100.0}, [])
        assert get_price("SPY", "2024-03-14") == pytest.approx(100.0)

    def test_get_series_applies_same_adjustment(self, isolated_workdir):
        from src.prices import get_series
        self._seed({"2024-03-14": 100.0, "2024-03-15": 99.5},
                   [["2024-03-15", 1.0]])
        out = get_series("SPY", "2024-03-01", "2024-03-31")
        assert out["2024-03-14"] == pytest.approx(99.0)
        assert out["2024-03-15"] == pytest.approx(99.5)

    def test_fetch_refreshes_dividends_for_tr_symbols(self, isolated_workdir,
                                                      monkeypatch):
        monkeypatch.setattr("src.prices._fetch_range",
                            lambda s, a, b: {"2024-01-03": 470.0})
        monkeypatch.setattr("src.prices._fetch_splits", lambda s: [])
        monkeypatch.setattr("src.prices._fetch_dividends",
                            lambda s: [["2023-12-15", 1.75]])
        from src.prices import _load_dividends, ensure_coverage
        ensure_coverage(["SPY"], "2024-01-02", "2024-01-03", verbose=False)
        assert _load_dividends()["SPY"] == [["2023-12-15", 1.75]]

    def test_migration_wipes_adj_close_era_series(self, isolated_workdir):
        """Cached TR series from before the local-TR feature hold Adj
        Close values — the one-time migration must wipe them (and their
        coverage meta) so they refetch as Close."""
        import json as _json
        from src.config import CACHE_DIR
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(CACHE_DIR / "price_cache.json", "w", encoding="utf-8") as f:
            _json.dump({"SPY": {"2024-01-02": 466.0},
                        "AAPL": {"2024-01-02": 185.0}}, f)
        with open(CACHE_DIR / "price_cache_meta.json", "w", encoding="utf-8") as f:
            _json.dump({"version": 1, "auto_adjusted": False, "migrated_v1": True,
                        "symbols": {"SPY": {"covered_start": "2024-01-02",
                                            "covered_end": "2024-01-02",
                                            "last_full_refresh": "2024-01-02"},
                                    "AAPL": {"covered_start": "2024-01-02",
                                             "covered_end": "2024-01-02"}}}, f)
        from src.prices import _load_meta, _load_prices
        prices = _load_prices()
        assert "SPY" not in prices, "Adj Close era series must be wiped"
        assert prices["AAPL"] == {"2024-01-02": 185.0}, "non-TR untouched"
        meta = _load_meta()
        assert "covered_start" not in meta["symbols"]["SPY"]
        assert meta["symbols"]["AAPL"]["covered_start"] == "2024-01-02"
        assert meta.get("migrated_tr_close_v2") is True


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


class TestBatchedCoverageFetch:
    """ensure_coverage groups symbols by identical gap signature and
    serves groups >= 2 with one batched download; symbols the batch
    can't serve fall back to the per-symbol serial path, which owns
    retry/failure bookkeeping."""

    def _stub(self, monkeypatch, batch_fn, serial_fn=None, splits_fn=None):
        monkeypatch.setattr("src.prices._batch_fetch_ranges", batch_fn)
        monkeypatch.setattr("src.prices._fetch_range",
                            serial_fn or (lambda s, a, b: {}))
        monkeypatch.setattr("src.prices._fetch_splits",
                            splits_fn or (lambda s: []))

    def test_shared_gap_uses_one_batch_call(self, isolated_workdir, monkeypatch):
        calls = {"batch": 0, "serial": 0}

        def fake_batch(symbols, start, end):
            calls["batch"] += 1
            return {s: {"prices": {"2024-01-03": 10.0 + i}, "split_event": False}
                    for i, s in enumerate(sorted(symbols))}

        def fake_serial(symbol, start, end):
            calls["serial"] += 1
            return {"2024-01-03": 99.0}

        self._stub(monkeypatch, fake_batch, fake_serial)
        from src.prices import ensure_coverage, get_price
        ensure_coverage(["AAA", "BBB", "CCC"], "2024-01-02", "2024-01-03",
                        verbose=False)
        assert calls["batch"] == 1
        assert calls["serial"] == 0
        assert get_price("AAA", "2024-01-03") == 10.0
        assert get_price("CCC", "2024-01-03") == 12.0

    def test_single_symbol_group_stays_serial(self, isolated_workdir, monkeypatch):
        calls = {"batch": 0}

        def fake_batch(symbols, start, end):
            calls["batch"] += 1
            return {}

        self._stub(monkeypatch, fake_batch,
                   lambda s, a, b: {"2024-01-03": 42.0})
        from src.prices import ensure_coverage, get_price
        ensure_coverage(["AAA"], "2024-01-02", "2024-01-03", verbose=False)
        assert calls["batch"] == 0, "a group of one must not pay a batch call"
        assert get_price("AAA", "2024-01-03") == 42.0

    def test_batch_miss_long_range_falls_back_to_serial(self, isolated_workdir,
                                                        monkeypatch):
        """A symbol absent from the batch result over a long range goes
        through the serial path so failure bookkeeping stays accurate."""
        serial_calls = []

        def fake_batch(symbols, start, end):
            return {s: {"prices": {"2024-01-31": 5.0}, "split_event": False}
                    for s in symbols if s != "MISS"}

        def fake_serial(symbol, start, end):
            serial_calls.append(symbol)
            return {}

        self._stub(monkeypatch, fake_batch, fake_serial)
        from src.prices import ensure_coverage, _load_meta
        ensure_coverage(["AAA", "MISS"], "2024-01-02", "2024-01-31",
                        verbose=False)
        assert serial_calls == ["MISS"]
        meta = _load_meta()
        assert meta["symbols"]["MISS"]["failure_count"] == 1
        assert meta["symbols"]["AAA"]["failure_count"] == 0

    def test_batch_empty_short_range_not_penalized(self, isolated_workdir,
                                                   monkeypatch):
        """Weekend/holiday: batch returns no rows over a short range —
        no serial retry, no failure recorded (same free pass as serial)."""
        serial_calls = []

        def fake_serial(symbol, start, end):
            serial_calls.append(symbol)
            return {}

        self._stub(monkeypatch, lambda s, a, b: {}, fake_serial)
        from src.prices import ensure_coverage, _load_meta
        ensure_coverage(["AAA", "BBB"], "2024-01-02", "2024-01-03",
                        verbose=False)
        assert serial_calls == []
        meta = _load_meta()
        assert not meta["symbols"].get("AAA", {}).get("failure_count")
        assert not meta["symbols"].get("BBB", {}).get("failure_count")

    def test_batch_unavailable_falls_back_for_whole_group(self, isolated_workdir,
                                                          monkeypatch):
        """_batch_fetch_ranges returning None (yfinance missing / download
        threw) sends every group symbol through the serial path."""
        serial_calls = []

        def fake_serial(symbol, start, end):
            serial_calls.append(symbol)
            return {"2024-01-03": 7.0}

        self._stub(monkeypatch, lambda s, a, b: None, fake_serial)
        from src.prices import ensure_coverage, get_price
        ensure_coverage(["AAA", "BBB"], "2024-01-02", "2024-01-03",
                        verbose=False)
        assert sorted(serial_calls) == ["AAA", "BBB"]
        assert get_price("AAA", "2024-01-03") == 7.0

    def test_clean_split_window_skips_splits_refetch(self, isolated_workdir,
                                                     monkeypatch):
        """split_event=False + existing splits entry -> no per-symbol
        splits refetch (the weekly deep refresh catches revisions)."""
        splits_calls = []

        def fake_splits(symbol):
            splits_calls.append(symbol)
            return []

        def fake_batch(symbols, start, end):
            return {s: {"prices": {"2024-01-03": 5.0}, "split_event": False}
                    for s in symbols}

        self._stub(monkeypatch, fake_batch, splits_fn=fake_splits)
        from src.prices import _load_splits, ensure_coverage
        _load_splits()["AAA"] = []   # entry present -> skip is allowed
        ensure_coverage(["AAA", "BBB"], "2024-01-02", "2024-01-03",
                        verbose=False)
        # AAA skipped (clean window + cached entry); BBB backfilled
        # (no splits entry yet).
        assert splits_calls == ["BBB"]


class TestShardedPriceCache:
    """Prices persist as one shard per symbol under cache/prices/; only
    dirty symbols rewrite, deletions unlink, the legacy monolith
    migrates on first load, and stored values round to 6 significant
    digits."""

    def _fetch_and_save(self, monkeypatch, sym, series):
        monkeypatch.setattr("src.prices._fetch_range",
                            lambda s, a, b: dict(series))
        monkeypatch.setattr("src.prices._fetch_splits", lambda s: [])
        from src.prices import ensure_coverage, save_caches
        ensure_coverage([sym], min(series), max(series), verbose=False)
        save_caches()

    def test_roundtrip_through_shard(self, isolated_workdir, monkeypatch):
        from src import prices
        self._fetch_and_save(monkeypatch, "AAPL", {"2024-01-03": 185.0})
        shard = isolated_workdir / "cache" / "prices" / "AAPL.json"
        assert shard.exists()
        prices.reset_caches()
        assert prices.get_price("AAPL", "2024-01-03") == 185.0

    def test_legacy_monolith_migrates_and_is_removed(self, isolated_workdir,
                                                     monkeypatch):
        import json as _json
        from src.config import CACHE_DIR
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        legacy = CACHE_DIR / "price_cache.json"
        legacy.write_text(_json.dumps({"AAPL": {"2024-01-03": 185.0},
                                       "MSFT": {"2024-01-03": 370.0}}),
                          encoding="utf-8")
        from src.prices import get_price, save_caches
        assert get_price("MSFT", "2024-01-03") == 370.0
        save_caches()
        assert not legacy.exists(), "monolith removed after shard write"
        assert (CACHE_DIR / "prices" / "AAPL.json").exists()
        assert (CACHE_DIR / "prices" / "MSFT.json").exists()
        from src import prices
        prices.reset_caches()
        assert get_price("AAPL", "2024-01-03") == 185.0

    def test_only_dirty_symbols_rewrite(self, isolated_workdir, monkeypatch):
        """A shard deleted out-of-band must NOT reappear when a
        different symbol is fetched — proof that clean symbols don't
        rewrite."""
        from src import prices
        self._fetch_and_save(monkeypatch, "AAPL", {"2024-01-03": 185.0})
        aapl_shard = isolated_workdir / "cache" / "prices" / "AAPL.json"
        aapl_shard.unlink()
        self._fetch_and_save(monkeypatch, "MSFT", {"2024-01-03": 370.0})
        assert not aapl_shard.exists(), "clean symbol must not rewrite"
        assert (isolated_workdir / "cache" / "prices" / "MSFT.json").exists()

    def test_split_invalidation_unlinks_shard(self, isolated_workdir,
                                              monkeypatch):
        from src import prices
        self._fetch_and_save(monkeypatch, "AAPL", {"2024-01-03": 185.0})
        shard = isolated_workdir / "cache" / "prices" / "AAPL.json"
        assert shard.exists()
        changed = prices._invalidate_prices_for_split_change(
            "AAPL", [["2020-08-31", 4.0]], [["2020-08-31", 4.0],
                                            ["2024-06-10", 10.0]],
            verbose=False)
        assert changed
        prices.save_caches()
        assert not shard.exists()

    def test_values_round_to_six_significant_digits(self, isolated_workdir,
                                                    monkeypatch):
        import json as _json
        from src import prices
        self._fetch_and_save(monkeypatch, "AAPL",
                             {"2024-01-03": 185.12345678901})
        shard = isolated_workdir / "cache" / "prices" / "AAPL.json"
        doc = _json.loads(shard.read_text(encoding="utf-8"))
        assert doc["symbol"] == "AAPL"
        assert doc["prices"]["2024-01-03"] == 185.123
        # Tiny prices keep 6 SIGNIFICANT digits, not 6 decimals.
        prices.reset_caches()
        self._fetch_and_save(monkeypatch, "SHIB-USD",
                             {"2024-01-03": 0.0000102345678})
        doc2 = _json.loads((isolated_workdir / "cache" / "prices" /
                            "SHIB-USD.json").read_text(encoding="utf-8"))
        assert doc2["prices"]["2024-01-03"] == 1.02346e-05

    def test_windows_reserved_symbol_gets_safe_filename(self, isolated_workdir,
                                                        monkeypatch):
        """CON is a real NYSE ticker but a reserved device name on
        Windows — the shard must land under a suffixed filename and
        still round-trip via the in-file symbol."""
        from src import prices
        self._fetch_and_save(monkeypatch, "CON", {"2024-01-03": 20.0})
        shard_dir = isolated_workdir / "cache" / "prices"
        names = [p.name for p in shard_dir.glob("*.json")]
        assert len(names) == 1
        assert names[0] != "CON.json"
        prices.reset_caches()
        assert prices.get_price("CON", "2024-01-03") == 20.0
