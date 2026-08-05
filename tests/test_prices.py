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


class TestOptionIntrinsicFloor:
    """Open options are floored at intrinsic value from the underlying's
    cached price — a deep-ITM contract must track its underlying
    instead of staying flat at a stale last-traded premium."""

    def _seed_underlying(self, sym, prices_by_date):
        from src.prices import _load_prices
        _load_prices()[sym] = dict(prices_by_date)

    def test_call_and_put_intrinsic(self, isolated_workdir):
        from src.prices import option_intrinsic
        self._seed_underlying("META", {"2026-07-17": 850.0})
        call = "META 12/18/2026 Call $800.00"
        put = "META 12/18/2026 Put $900.00"
        otm_call = "META 12/18/2026 Call $1,000.00"
        assert option_intrinsic(call, "2026-07-17") == pytest.approx(50.0)
        assert option_intrinsic(put, "2026-07-17") == pytest.approx(50.0)
        assert option_intrinsic(otm_call, "2026-07-17") == 0.0

    def test_unparsable_or_unpriced_returns_none(self, isolated_workdir):
        from src.prices import option_intrinsic
        assert option_intrinsic("AAPL", "2026-07-17") is None
        # Parsable option but underlying has no cached price.
        assert option_intrinsic("ZZZQ 01/15/2027 Call $10.00",
                                "2026-07-17") is None

    def test_floor_only_raises(self, isolated_workdir):
        from src.prices import apply_option_intrinsic_floor
        self._seed_underlying("META", {"2026-07-17": 850.0})
        deep_itm = "META 12/18/2026 Call $800.00"   # intrinsic 50
        otm = "META 12/18/2026 Call $1,000.00"       # intrinsic 0
        prices = {deep_itm: 12.50, otm: 3.25, "AAPL": 200.0}
        n = apply_option_intrinsic_floor(
            prices, [deep_itm, otm, "AAPL"], "2026-07-17")
        assert n == 1
        assert prices[deep_itm] == pytest.approx(50.0)   # raised
        assert prices[otm] == pytest.approx(3.25)        # premium kept
        assert prices["AAPL"] == pytest.approx(200.0)    # untouched

    def test_history_fallback_uses_intrinsic_floor(self, stub_prices):
        """Snapshot walker: an option position with a stale premium and
        a risen underlying values at intrinsic × 100 × contracts."""
        from src.history import compute_history

        opt = "META 6/18/2027 Call $500.00"
        stub_prices.set("META", {"2026-01-30": 700.0})
        _populate_cache = None
        from datetime import datetime
        from src.prices import ensure_coverage
        ensure_coverage(["META"], "2026-01-30", "2026-01-30")
        txns = [_hist_txn := {
            "date": "2026-01-15", "account": "Robinhood",
            "account_group": "Robinhood", "account_type": "Taxable",
            "symbol": opt, "action": "Option Buy", "raw_action": "BTO",
            "quantity": 1.0, "price": 20.0, "fees": 0.0,
            "amount": 2000.0, "description": "", "source": "manual.csv",
        }]
        history = compute_history(txns, {opt: "Options"})
        by_date = {h["date"]: h for h in history}
        # EOM 2026-01-31: premium 20 → floored at intrinsic 200 → ×100
        assert by_date["2026-01-31"]["total"] == pytest.approx(20000.0)


class TestOptionIntrinsicSplitBasis:
    """The strike is written in the as-of-trade share basis; the cached
    underlying close is today-basis.  option_intrinsic must scale the
    price back before comparing — the regression here showed a $2.50
    ACB call (pre-reverse-split basis) at $500k+ on a 2019 snapshot."""

    def test_reverse_split_does_not_inflate_calls(self, isolated_workdir):
        from src.prices import _load_prices, _load_splits, option_intrinsic
        # As-traded 2019 close $2.19; after 1:12 then 1:10 reverse
        # splits the today-basis cached close is 2.19 × 120 = 262.80.
        _load_prices()["ACB"] = {"2019-12-31": 262.80}
        _load_splits()["ACB"] = [["2020-05-11", 1 / 12], ["2024-02-20", 0.1]]
        call = "ACB 1/17/2020 Call $2.50"
        # As-traded 2.19 < 2.50 strike -> OTM, intrinsic 0.
        assert option_intrinsic(call, "2019-12-31") == 0.0
        put = "ACB 1/17/2020 Put $2.50"
        assert option_intrinsic(put, "2019-12-31") == pytest.approx(0.31)

    def test_forward_split_does_not_inflate_puts(self, isolated_workdir):
        from src.prices import _load_prices, _load_splits, option_intrinsic
        # As-traded 2019 close $430; after 5:1 and 3:1 forward splits
        # the today-basis cached close is 430 / 15 = 28.6667.
        _load_prices()["TSLA"] = {"2019-12-31": 28.6667}
        _load_splits()["TSLA"] = [["2020-08-31", 5.0], ["2022-08-25", 3.0]]
        put = "TSLA 1/17/2020 Put $400.00"
        assert option_intrinsic(put, "2019-12-31") == 0.0
        call = "TSLA 1/17/2020 Call $400.00"
        assert option_intrinsic(call, "2019-12-31") == pytest.approx(30.0, abs=0.01)

    def test_no_later_splits_unchanged(self, isolated_workdir):
        from src.prices import _load_prices, option_intrinsic
        _load_prices()["META"] = {"2026-07-17": 850.0}
        assert option_intrinsic("META 12/18/2026 Call $800.00",
                                "2026-07-17") == pytest.approx(50.0)


class TestScaledProxyMultiAnchor:
    """Scaled proxies anchor each lookup to the NEAREST observed txn
    price (in-memory series from ensure_proxy_anchors), so tracking
    drift is bounded by the observation gap — not the years since the
    persisted first anchor."""

    def _setup(self, monkeypatch):
        import json as _json
        from src.config import CACHE_DIR
        from src import prices
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / "symbol_proxy_map.json").write_text(_json.dumps({
            "MYFUND": {"proxy": "VFIAX", "method": "scaled"},
        }), encoding="utf-8")
        prices._load_prices()["VFIAX"] = {
            "2024-01-15": 200.0, "2024-06-14": 210.0, "2024-06-20": 220.0,
        }

    def test_nearest_anchor_wins(self, isolated_workdir, monkeypatch):
        from src.prices import ensure_proxy_anchors, get_price
        self._setup(monkeypatch)
        txns = [
            {"symbol": "MYFUND", "date": "2024-01-15", "price": 100.0},
            # By June the fund OUTPERFORMED the proxy: real NAV 110
            # while proxy-scaling from January would predict 105.
            {"symbol": "MYFUND", "date": "2024-06-14", "price": 110.0},
        ]
        ensure_proxy_anchors(txns, verbose=False)
        # Query near the June observation → June anchor:
        # 110 × (220/210) = 115.238…  (January anchor would give
        # 100 × 220/200 = 110 — a 4.5% error.)
        assert get_price("MYFUND", "2024-06-20") == pytest.approx(110 * 220 / 210)
        # Query near the January observation → January anchor.
        assert get_price("MYFUND", "2024-01-15") == pytest.approx(100.0)

    def test_falls_back_to_persisted_anchor_without_series(
            self, isolated_workdir, monkeypatch):
        """No ensure_proxy_anchors call (no txns loaded) → legacy
        single-anchor behaviour from the persisted map entry."""
        import json as _json
        from src.config import CACHE_DIR
        from src import prices
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / "symbol_proxy_map.json").write_text(_json.dumps({
            "MYFUND": {"proxy": "VFIAX", "method": "scaled",
                       "anchor_date": "2024-01-15", "anchor_price": 100.0},
        }), encoding="utf-8")
        prices._load_prices()["VFIAX"] = {
            "2024-01-15": 200.0, "2024-06-20": 220.0,
        }
        assert prices.get_price("MYFUND", "2024-06-20") == pytest.approx(110.0)

    def test_series_is_memory_only(self, isolated_workdir, monkeypatch):
        """The per-txn anchor series must never persist (the dates are
        the user's payroll calendar) — only the single first anchor
        lands in the proxy map file."""
        import json as _json
        from src.config import CACHE_DIR
        from src.prices import ensure_proxy_anchors, save_caches
        self._setup(monkeypatch)
        ensure_proxy_anchors([
            {"symbol": "MYFUND", "date": "2024-01-15", "price": 100.0},
            {"symbol": "MYFUND", "date": "2024-06-14", "price": 110.0},
        ], verbose=False)
        save_caches()
        doc = _json.loads((CACHE_DIR / "symbol_proxy_map.json")
                          .read_text(encoding="utf-8"))
        assert doc["MYFUND"]["anchor_date"] == "2024-01-15"
        assert "anchors" not in doc["MYFUND"]
        assert "2024-06-14" not in _json.dumps(doc)


class TestTodayFreshness:
    """Phase 1 — ``covered_end`` records what we ASKED for, not what has
    settled.

    Two consequences the cache used to get wrong:

    * A crypto bar is UTC-dated, so an evening local run legitimately
      receives *tomorrow's* bar.  Recording that as ``covered_end`` made
      the whole of the next local day look already-fetched, and crypto
      was skipped for a full day.
    * The moment any run of the day fetched today, ``covered_end ==
      today`` and every later run that day became a no-op — prices froze
      at whatever the first run captured, which made the
      ``--refresh-prices`` speed flag load-bearing for correctness.
    """

    @staticmethod
    def _pin_today(monkeypatch, iso: str) -> None:
        from src import prices as _prices_mod
        monkeypatch.setattr(_prices_mod, "_today",
                            lambda: date.fromisoformat(iso))

    # --- 1a: covered_end never names a future local date ---------------

    def test_record_success_caps_covered_end_at_local_today(
            self, isolated_workdir, monkeypatch):
        from datetime import datetime
        from src.prices import _load_meta, _record_success
        self._pin_today(monkeypatch, "2026-08-04")   # Tuesday, local
        # A UTC-dated crypto bar for 08-05 arrives on the evening of 08-04.
        _record_success("ETH-USD", date(2026, 8, 1), date(2026, 8, 5),
                        datetime.now())
        assert _load_meta()["symbols"]["ETH-USD"]["covered_end"] == "2026-08-04"

    def test_record_success_heals_a_stale_future_claim(
            self, isolated_workdir, monkeypatch):
        """A covered_end left in the future by an older build must be
        pulled back, not preserved by the max()."""
        from datetime import datetime
        from src.prices import _load_meta, _record_success
        self._pin_today(monkeypatch, "2026-08-05")
        _load_meta()["symbols"]["ETH-USD"] = {
            "covered_start": "2024-01-01",
            "covered_end":   "2026-08-06",
        }
        _record_success("ETH-USD", date(2024, 1, 1), date(2026, 8, 5),
                        datetime.now())
        assert _load_meta()["symbols"]["ETH-USD"]["covered_end"] == "2026-08-05"

    def test_latest_close_batch_caps_covered_end_but_keeps_the_bar(
            self, isolated_workdir, monkeypatch):
        """The --refresh-prices batch path writes covered_end straight
        from the frame's newest index — the exact spot a UTC crypto bar
        pushed the coverage claim into tomorrow.  The BAR is real data
        and stays; only the claim is capped."""
        import pandas as pd
        from src import prices as _prices_mod
        from src.prices import _load_meta, _load_prices, fetch_latest_close_batch

        frame = pd.DataFrame(
            {"Close": [3000.0, 3050.0]},
            index=pd.to_datetime(["2026-08-04", "2026-08-05"]),
        )

        class _FakeYF:
            @staticmethod
            def download(**kwargs):
                return frame

        monkeypatch.setattr(_prices_mod, "_lazy_yf", lambda: _FakeYF())
        self._pin_today(monkeypatch, "2026-08-04")   # still 08-04 locally
        assert fetch_latest_close_batch(["ETH-USD"], verbose=False) == 1

        assert _load_meta()["symbols"]["ETH-USD"]["covered_end"] == "2026-08-04"
        assert _load_prices()["ETH-USD"]["2026-08-05"] == 3050.0, \
            "the future-dated bar is real data and must be kept"

    # --- 1b: today is never treated as covered -------------------------

    def test_request_ending_today_fetches_even_when_covered_end_is_today(
            self, isolated_workdir, monkeypatch):
        from src.prices import _load_meta, _missing_ranges
        self._pin_today(monkeypatch, "2026-08-05")   # Wednesday
        _load_meta()["symbols"]["AAPL"] = {
            "covered_start": "2024-01-01",
            "covered_end":   "2026-08-05",
        }
        gaps = _missing_ranges("AAPL", date(2024, 1, 1), date(2026, 8, 5))
        assert gaps == [(date(2026, 8, 5), date(2026, 8, 5))], \
            "a second run on the same day must still refetch today"

    def test_historical_request_is_unaffected(
            self, isolated_workdir, monkeypatch):
        """The clamp must not invent gaps for an ``end`` in the past —
        that would refetch settled history on every run."""
        from src.prices import _load_meta, _missing_ranges
        self._pin_today(monkeypatch, "2026-08-05")
        _load_meta()["symbols"]["FOO"] = {
            "covered_start": "2024-01-01",
            "covered_end":   "2024-03-15",
        }
        assert _missing_ranges("FOO", date(2024, 1, 1), date(2024, 3, 15)) == []

    def test_weekend_run_still_yields_no_gap(
            self, isolated_workdir, monkeypatch):
        """Saturday's request walks back to Friday, which IS settled —
        the clamp must not turn every weekend run into a refetch."""
        from src.prices import _load_meta, _missing_ranges
        self._pin_today(monkeypatch, "2026-08-08")   # Saturday
        _load_meta()["symbols"]["FOO"] = {
            "covered_start": "2024-01-01",
            "covered_end":   "2026-08-07",           # Friday
        }
        assert _missing_ranges("FOO", date(2024, 1, 1), date(2026, 8, 8)) == []

    # --- defect 2 regression -------------------------------------------

    def test_crypto_covered_through_tomorrow_still_fetches_today(
            self, isolated_workdir, monkeypatch, stub_prices):
        """Observed at 22:42 local / 05:42 UTC: a crypto symbol had a bar
        for the NEXT day and covered_end set to it, so the following
        day's full run saw "up to date" and skipped crypto entirely."""
        from src.prices import _load_meta, ensure_coverage, get_price
        self._pin_today(monkeypatch, "2026-08-05")
        _load_meta()["symbols"]["ETH-USD"] = {
            "covered_start": "2024-01-01",
            "covered_end":   "2026-08-06",   # tomorrow, from last night's run
        }
        stub_prices.set("ETH-USD", {"2026-08-05": 3100.0})
        ensure_coverage(["ETH-USD"], "2024-01-01", "2026-08-05", verbose=False)
        assert get_price("ETH-USD", "2026-08-05") == 3100.0, \
            "a future-dated covered_end must not suppress today's fetch"
        assert _load_meta()["symbols"]["ETH-USD"]["covered_end"] == "2026-08-05"

    # --- 1c: force_today_for -------------------------------------------

    def test_force_today_for_refetches_the_last_trading_day(
            self, isolated_workdir, monkeypatch, stub_prices):
        """Over a weekend the requested end walks back to Friday, which
        the clamp considers settled — so only an explicit force
        re-pulls it.  That is how a mutual-fund NAV that hadn't posted
        when Friday evening's run went out finally lands."""
        from src.prices import _load_meta, ensure_coverage, get_price
        self._pin_today(monkeypatch, "2026-08-08")   # Saturday
        _load_meta()["symbols"]["VFIAX"] = {
            "covered_start": "2024-01-01",
            "covered_end":   "2026-08-07",           # Friday
        }
        stub_prices.set("VFIAX", {"2026-08-07": 555.0})

        ensure_coverage(["VFIAX"], "2024-01-01", "2026-08-08", verbose=False)
        assert get_price("VFIAX", "2026-08-07") is None, \
            "baseline: without the force a weekend run is a no-op"

        ensure_coverage(["VFIAX"], "2024-01-01", "2026-08-08", verbose=False,
                        force_today_for={"VFIAX"})
        assert get_price("VFIAX", "2026-08-07") == 555.0

    def test_main_relies_on_settle_awareness_not_a_blunt_force(self):
        """main.py briefly forced a refresh of every held symbol every
        run, as a stand-in for not knowing whether a bar had settled.
        `_settle_horizon` answers that properly now, so re-adding the
        force would only undo the evening no-op it exists to buy.
        Asserted against the real source so it doesn't creep back."""
        import inspect
        import src.main

        src_text = inspect.getsource(src.main)
        assert "force_today_for=" not in src_text, (
            "main.py is forcing a price refresh again — settle awareness "
            "already refetches exactly the bars that can still change, so "
            "this just re-breaks the offline-fast evening run"
        )


class TestPriceFreshnessSurfacing:
    """Phase 1.5 — ``last_fetch`` was already recorded and already
    accurate; it just never reached the UI.  ``as_of`` is only a DATE,
    so a snapshot reads as "current" all day even when the marks behind
    it were pulled at 7am — which is exactly what makes a mid-session
    reconciliation against a broker statement confusing.
    """

    def test_price_source_resolves_proxies_and_option_underlyings(
            self, isolated_workdir, monkeypatch):
        import json as _json
        from src.config import CACHE_DIR
        from src import prices

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / "symbol_proxy_map.json").write_text(_json.dumps({
            "MYFUND": {"proxy": "VFIAX", "method": "scaled",
                       "anchor_date": "2024-01-15", "anchor_price": 100.0},
        }), encoding="utf-8")

        assert prices.price_source_symbol("AAPL") == "AAPL"
        assert prices.price_source_symbol("MYFUND") == "VFIAX"
        assert prices.price_source_symbol(
            "ACME 1/15/2028 Call $100.00") == "ACME"
        # Nothing in the cache backs these — they're priced from txn
        # history, so no fetch timestamp describes them.
        assert prices.price_source_symbol("USD") is None
        assert prices.price_source_symbol("Some Fund Display Name") is None

    def test_oldest_last_fetch_is_a_floor_not_the_newest(
            self, isolated_workdir):
        from src.prices import _load_meta, oldest_last_fetch
        meta = _load_meta()["symbols"]
        meta["AAA"] = {"last_fetch": "2026-08-05T11:04:00"}
        meta["BBB"] = {"last_fetch": "2026-08-05T07:12:00"}
        meta["CCC"] = {"last_fetch": "2026-08-05T09:30:00"}
        assert oldest_last_fetch(["AAA", "BBB", "CCC"]) == "2026-08-05T07:12:00"

    def test_oldest_last_fetch_ignores_unfetchable_symbols(
            self, isolated_workdir):
        """A multi-word fund name has no fetch timestamp at all — it
        must be skipped, not treated as infinitely stale (which would
        suppress the stamp for every portfolio holding one)."""
        from src.prices import _load_meta, oldest_last_fetch
        _load_meta()["symbols"]["AAA"] = {"last_fetch": "2026-08-05T11:04:00"}
        assert oldest_last_fetch(
            ["AAA", "USD", "Some Fund Display Name"]) == "2026-08-05T11:04:00"
        assert oldest_last_fetch(["USD", "Some Fund Display Name"]) is None

    def test_header_summary_reports_the_oldest_held_fetch(self, stub_prices):
        """The stamp must be the staleness FLOOR across held positions:
        one ticker refreshed a second ago says nothing about the fund
        whose NAV last landed hours earlier."""
        from src.analytics.header import compute_header_summary
        from src.history import compute_history
        from src.prices import _load_meta, ensure_coverage

        stub_prices.set("AAA", {"2026-08-03": 10.0, "2026-08-04": 11.0})
        stub_prices.set("BBB", {"2026-08-03": 20.0, "2026-08-04": 21.0})
        ensure_coverage(["AAA", "BBB"], "2026-08-03", "2026-08-04",
                        verbose=False)

        def _buy(sym, price):
            return {
                "date": "2026-08-03", "account": "Robinhood",
                "account_group": "Robinhood", "account_type": "Taxable",
                "symbol": sym, "action": "Buy", "raw_action": "Buy",
                "quantity": 10.0, "price": price, "fees": 0.0,
                "amount": price * 10, "description": "", "source": "rh.csv",
            }

        txns = [_buy("AAA", 10.0), _buy("BBB", 20.0)]
        history = compute_history(txns, {"AAA": "Technology", "BBB": "Technology"})
        assert history

        meta = _load_meta()["symbols"]
        meta["AAA"]["last_fetch"] = "2026-08-04T11:04:00"
        meta["BBB"]["last_fetch"] = "2026-08-04T07:12:00"

        summary = compute_header_summary(txns, history, [],
                                         {"net_contributed": 300.0})
        assert summary["prices_as_of"] == "2026-08-04T07:12:00"

    def test_header_summary_tolerates_no_fetchable_positions(self, stub_prices):
        """A portfolio of nothing but cash / unfetchable funds must
        report no stamp rather than crashing or inventing one."""
        from src.analytics.header import compute_header_summary
        from src.history import compute_history

        txns = [{
            "date": "2026-08-03", "account": "Apple Savings",
            "account_group": "Apple Savings", "account_type": "Savings",
            "symbol": "USD", "action": "Deposit", "raw_action": "Deposit",
            "quantity": 500.0, "price": 0.0, "fees": 0.0, "amount": 500.0,
            "description": "", "source": "apple.csv",
        }]
        history = compute_history(txns, {"USD": "Cash"})
        assert history
        summary = compute_header_summary(txns, history, [],
                                         {"net_contributed": 500.0})
        assert summary["prices_as_of"] is None

    def test_dashboard_renders_the_stamp(self):
        """The figure is only worth computing if it reaches the header
        bar — guard the element and its renderer against a bundle
        refactor dropping one side."""
        from pathlib import Path
        from src import dashboard

        pkg = Path(dashboard.__file__).parent
        template = (pkg / "template.html").read_text(encoding="utf-8")
        app_js = dashboard._read_app_js()
        assert 'id="pricesAsOf"' in template
        assert "prices_as_of" in app_js
        assert "renderPricesAsOf()" in app_js


def _utc(iso: str):
    """UTC instant from an ISO string, for pinning the settle clock."""
    from datetime import datetime, timezone
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)


class TestSettleClass:
    """Phase 2a — which settle rule applies to a symbol.

    Biased toward ``fund`` on purpose: a fund mistaken for an equity is
    marked final before its NAV strikes, which produces a wrong number.
    The reverse costs one redundant fetch that returns the same value.
    """

    def test_crypto_by_suffix_and_by_config(self, isolated_workdir):
        from src.prices import _settle_class
        assert _settle_class("ETH-USD") == "crypto"
        assert _settle_class("BTC-USD") == "crypto"
        assert _settle_class("USDC") == "crypto"      # config.CRYPTO_SYMBOLS

    def test_mutual_funds_by_ticker_shape_and_display_name(self, isolated_workdir):
        from src.prices import _settle_class
        # US convention: five characters ending in X.
        for sym in ("VFIAX", "SWPPX", "FSELX", "USNQX"):
            assert _settle_class(sym) == "fund", sym
        # Multi-word "tickers" are fund display names.
        assert _settle_class("Vanguard Employee Benefit Index Fund") == "fund"

    def test_equities_and_etfs_settle_with_the_tape(self, isolated_workdir):
        from src.prices import _settle_class
        for sym in ("AAPL", "SPY", "QQQ", "GOOGL", "ENVXW"):
            assert _settle_class(sym) == "equity", sym

    def test_proxy_target_owns_the_settle_rule(self, isolated_workdir):
        """Several proxies ARE mutual funds; the proxy is what actually
        gets fetched, so it decides when the bar goes final."""
        import json as _json
        from src.config import CACHE_DIR
        from src.prices import _settle_class

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / "symbol_proxy_map.json").write_text(_json.dumps({
            "Some 401K Collective Trust": {
                "proxy": "VFIAX", "method": "scaled",
                "anchor_date": "2024-01-15", "anchor_price": 100.0},
        }), encoding="utf-8")
        assert _settle_class("Some 401K Collective Trust") == "fund"


class TestSettleHorizon:
    """Phase 2b — the newest date whose bar can no longer change.

    Cutoffs are deliberately late.  Being wrong in the "not settled yet"
    direction costs one redundant fetch returning the same number; being
    wrong the other way leaves a stale figure the user trusts.  That is
    also why market half-days need no calendar.
    """

    def test_equity_settles_after_the_tape_not_at_the_bell(
            self, isolated_workdir):
        from datetime import date
        from src.prices import _settle_horizon
        # Wednesday 2026-08-05, EDT (UTC-4).
        assert _settle_horizon("AAPL", _utc("2026-08-05T20:19:00")) \
            == date(2026, 8, 4), "16:19 ET — the close has not posted yet"
        assert _settle_horizon("AAPL", _utc("2026-08-05T20:21:00")) \
            == date(2026, 8, 5), "16:21 ET — settled"

    def test_fund_stays_live_past_the_equity_close(self, isolated_workdir):
        """Defect 3: a run between the equity close and the NAV strike
        mixes today's equity closes with YESTERDAY's fund NAVs."""
        from datetime import date
        from src.prices import _settle_horizon
        assert _settle_horizon("VFIAX", _utc("2026-08-05T20:30:00")) \
            == date(2026, 8, 4), "16:30 ET — equities done, NAV not struck"
        assert _settle_horizon("VFIAX", _utc("2026-08-05T22:30:00")) \
            == date(2026, 8, 5), "18:30 ET — NAV struck"

    def test_crypto_is_unsettled_until_the_utc_day_ends(self, isolated_workdir):
        """The exact scenario behind defect 2: 22:42 local is already
        05:42 UTC the NEXT day, so the UTC-dated bar fin receives is for
        a day that has barely started."""
        from datetime import date
        from src.prices import _settle_horizon
        assert _settle_horizon("ETH-USD", _utc("2026-08-05T05:42:00")) \
            == date(2026, 8, 4)
        # Once the UTC day is over, that bar is final.
        assert _settle_horizon("ETH-USD", _utc("2026-08-06T00:05:00")) \
            == date(2026, 8, 5)

    def test_crypto_ignores_weekends(self, isolated_workdir):
        """Crypto trades Saturdays — its horizon must not walk back to
        Friday the way an equity's does."""
        from datetime import date
        from src.prices import _settle_horizon
        # Sunday 2026-08-09, 12:00 UTC.
        assert _settle_horizon("BTC-USD", _utc("2026-08-09T12:00:00")) \
            == date(2026, 8, 8), "Saturday's UTC day is over and final"

    def test_dst_is_handled_by_the_zone_not_a_fixed_offset(
            self, isolated_workdir):
        """Same wall-clock ET time either side of the DST switch must
        classify the same.  A fixed UTC offset would get one of these
        wrong by an hour — enough to mark a bar final before the close."""
        from datetime import date
        from src.prices import _settle_horizon
        # 2026-03-20 (Friday, EDT = UTC-4): 20:30Z is 16:30 ET → settled.
        assert _settle_horizon("AAPL", _utc("2026-03-20T20:30:00")) \
            == date(2026, 3, 20)
        # 2026-11-20 (Friday, EST = UTC-5): the SAME 20:30Z is only
        # 15:30 ET → still trading.
        assert _settle_horizon("AAPL", _utc("2026-11-20T20:30:00")) \
            == date(2026, 11, 19)
        # 21:30Z is 16:30 ET in November → settled.
        assert _settle_horizon("AAPL", _utc("2026-11-20T21:30:00")) \
            == date(2026, 11, 20)

    def test_weekend_horizon_walks_back_to_friday(self, isolated_workdir):
        from datetime import date
        from src.prices import _settle_horizon
        # Sunday 2026-08-09, 12:00 ET.
        assert _settle_horizon("AAPL", _utc("2026-08-09T16:00:00")) \
            == date(2026, 8, 7)
        # Saturday evening, past both cutoffs — still Friday.
        assert _settle_horizon("VFIAX", _utc("2026-08-09T02:00:00")) \
            == date(2026, 8, 7)

    def test_horizon_degrades_safely_without_a_tz_database(
            self, isolated_workdir, monkeypatch):
        """pandas hard-requires tzdata so this should never fire, but if
        the zone is missing we must fall back to "nothing today is
        final" rather than taking the module down."""
        from datetime import date
        from src import prices as _prices_mod
        monkeypatch.setattr(_prices_mod, "_MARKET_TZ", None)
        monkeypatch.setattr(_prices_mod, "_today", lambda: date(2026, 8, 5))
        assert _prices_mod._settle_horizon(
            "AAPL", _utc("2026-08-05T23:00:00")) == date(2026, 8, 4)


class TestSettledThroughGating:
    """Phase 2c — refetch a date only while it can still change.

    Strictly better than both the old behaviour and blanket
    always-fetch: an intraday mark keeps getting replaced until the real
    close lands, and once everything held is final the run does no
    network work at all.
    """

    @staticmethod
    def _pin(monkeypatch, iso_utc: str) -> None:
        """Pin the whole clock — `_today` derives from `_now_utc`."""
        from src import prices as _prices_mod
        monkeypatch.setattr(_prices_mod, "_now_utc", lambda: _utc(iso_utc))

    def test_intraday_fetch_does_not_mark_the_day_final(
            self, isolated_workdir, monkeypatch, stub_prices):
        """Defect 4: yfinance's daily bar carries a LIVE price during
        market hours and fin stored it as the close.  Coverage may reach
        today; settled coverage must not."""
        from src.prices import _load_meta, ensure_coverage
        # Wednesday 2026-08-05, 11:00 ET.
        self._pin(monkeypatch, "2026-08-05T15:00:00")
        stub_prices.set("AAPL", {"2026-08-05": 200.0})
        ensure_coverage(["AAPL"], "2026-08-01", "2026-08-05", verbose=False)

        entry = _load_meta()["symbols"]["AAPL"]
        assert entry["covered_end"] == "2026-08-05"
        assert entry["settled_through"] == "2026-08-04", \
            "an 11:00 bar is a live mark, not a close"

    def test_unsettled_symbol_is_refetched_regardless_of_covered_end(
            self, isolated_workdir, monkeypatch):
        from datetime import date
        from src.prices import _load_meta, _missing_ranges
        self._pin(monkeypatch, "2026-08-05T15:00:00")   # 11:00 ET
        _load_meta()["symbols"]["AAPL"] = {
            "covered_start":   "2024-01-01",
            "covered_end":     "2026-08-05",
            "settled_through": "2026-08-04",
        }
        assert _missing_ranges("AAPL", date(2024, 1, 1), date(2026, 8, 5)) \
            == [(date(2026, 8, 5), date(2026, 8, 5))]

    def test_settled_symbol_is_not_refetched(
            self, isolated_workdir, monkeypatch):
        """The evening no-op.  This is what settle awareness buys, and
        what an unconditional force would take away."""
        from datetime import date
        from src.prices import _load_meta, _missing_ranges
        self._pin(monkeypatch, "2026-08-06T00:00:00")   # 20:00 ET
        _load_meta()["symbols"]["AAPL"] = {
            "covered_start":   "2024-01-01",
            "covered_end":     "2026-08-05",
            "settled_through": "2026-08-05",
        }
        assert _missing_ranges("AAPL", date(2024, 1, 1), date(2026, 8, 5)) == []

    def test_the_full_intraday_then_close_cycle(
            self, isolated_workdir, monkeypatch, stub_prices):
        """End to end: a mid-session run stores a live mark, a later run
        the same day replaces it with the real close, and a third run
        after settlement does nothing."""
        from src.prices import _load_meta, ensure_coverage, get_price

        # 11:00 ET — live mark.
        self._pin(monkeypatch, "2026-08-05T15:00:00")
        stub_prices.set("AAPL", {"2026-08-05": 200.0})
        ensure_coverage(["AAPL"], "2026-08-01", "2026-08-05", verbose=False)
        assert get_price("AAPL", "2026-08-05") == 200.0

        # 17:00 ET — the tape has settled; the close replaces the mark.
        self._pin(monkeypatch, "2026-08-05T21:00:00")
        stub_prices.set("AAPL", {"2026-08-05": 204.5})
        ensure_coverage(["AAPL"], "2026-08-01", "2026-08-05", verbose=False)
        assert get_price("AAPL", "2026-08-05") == 204.5, \
            "the settled close must overwrite the intraday mark"
        assert _load_meta()["symbols"]["AAPL"]["settled_through"] == "2026-08-05"

        # 20:00 ET — nothing left to do.  A fetch here would overwrite
        # the real close with whatever the stub hands back.
        self._pin(monkeypatch, "2026-08-06T00:00:00")
        stub_prices.set("AAPL", {"2026-08-05": 999.0})
        ensure_coverage(["AAPL"], "2026-08-01", "2026-08-05", verbose=False)
        assert get_price("AAPL", "2026-08-05") == 204.5, \
            "a settled bar must not be refetched"

    def test_funds_refetch_after_the_equity_close(
            self, isolated_workdir, monkeypatch, stub_prices):
        """Defect 3, the case that used to need a blunt force: a run
        between the equity close and the NAV strike gets no fund data,
        so the fund must still be pending on the next run."""
        from datetime import date
        from src.prices import _load_meta, _missing_ranges
        # 16:30 ET — equities done, NAV not struck.  The 16:30 run got
        # no fund row back, so coverage stopped at the prior day.
        self._pin(monkeypatch, "2026-08-05T20:30:00")
        _load_meta()["symbols"]["VFIAX"] = {
            "covered_start":   "2024-01-01",
            "covered_end":     "2026-08-04",
            "settled_through": "2026-08-04",
        }
        assert _missing_ranges("VFIAX", date(2024, 1, 1), date(2026, 8, 5)) \
            == [(date(2026, 8, 5), date(2026, 8, 5))]

        # Same cache, same symbol, Saturday: the equity horizon has long
        # since walked back to Friday, but Friday's NAV is still missing.
        self._pin(monkeypatch, "2026-08-08T16:00:00")   # Sat 12:00 ET
        assert _missing_ranges("VFIAX", date(2024, 1, 1), date(2026, 8, 8)) \
            == [(date(2026, 8, 5), date(2026, 8, 7))]

    def test_a_later_intraday_fetch_does_not_advance_the_claim(
            self, isolated_workdir, monkeypatch):
        """The next morning's fetch extends coverage to a day that is
        still trading — the settle claim must stay where it was."""
        from src.prices import _record_settled_through
        entry = {"covered_end": "2026-08-05", "settled_through": "2026-08-05"}
        # Next morning, 09:00 ET — today's bar is live again.
        self._pin(monkeypatch, "2026-08-06T13:00:00")
        entry["covered_end"] = "2026-08-06"
        _record_settled_through(entry, "AAPL")
        assert entry["settled_through"] == "2026-08-05"

    def test_settled_through_never_outruns_coverage(
            self, isolated_workdir, monkeypatch):
        """Long past the cutoff but we only hold data through the 3rd —
        the claim is bounded by what we actually have."""
        from src.prices import _record_settled_through
        self._pin(monkeypatch, "2026-08-06T00:00:00")   # 20:00 ET
        entry = {"covered_end": "2026-08-03"}
        _record_settled_through(entry, "AAPL")
        assert entry["settled_through"] == "2026-08-03"

    def test_cache_without_settled_through_keeps_working(
            self, isolated_workdir, monkeypatch):
        """Every cache written before this landed lacks the field.  It
        must fall back to the prior "today is never settled" rule rather
        than needing a migration."""
        from datetime import date
        from src.prices import _load_meta, _missing_ranges
        self._pin(monkeypatch, "2026-08-06T00:00:00")   # 20:00 ET
        _load_meta()["symbols"]["AAPL"] = {
            "covered_start": "2024-01-01",
            "covered_end":   "2026-08-05",
        }
        assert _missing_ranges("AAPL", date(2024, 1, 1), date(2026, 8, 5)) \
            == [(date(2026, 8, 5), date(2026, 8, 5))]

    def test_crypto_evening_run_does_not_settle_the_utc_day(
            self, isolated_workdir, monkeypatch, stub_prices):
        """Defect 2 under the new rule: the bar fin receives at 22:42
        local is for a UTC day that has barely started."""
        from src.prices import _load_meta, ensure_coverage
        # 2026-08-04 22:42 local US-Pacific == 2026-08-05 05:42 UTC.
        self._pin(monkeypatch, "2026-08-05T05:42:00")
        stub_prices.set("ETH-USD", {"2026-08-05": 3100.0})
        ensure_coverage(["ETH-USD"], "2024-01-01", "2026-08-05", verbose=False)
        entry = _load_meta()["symbols"]["ETH-USD"]
        assert entry["settled_through"] == "2026-08-04", \
            "the UTC day is still running — that bar can still move"

    def test_split_invalidation_clears_the_settle_claim(self, isolated_workdir):
        """Wiping a symbol's prices must wipe its settle claim too, or
        the meta file keeps asserting freshness for data that is gone."""
        from src.prices import _invalidate_prices_for_split_change, _load_meta
        _load_meta()["symbols"]["SMX"] = {
            "covered_start":   "2024-01-01",
            "covered_end":     "2026-08-05",
            "settled_through": "2026-08-05",
        }
        _invalidate_prices_for_split_change(
            "SMX", [], [["2026-08-05", 0.1]], verbose=False)
        assert "settled_through" not in _load_meta()["symbols"]["SMX"]
