"""The yfinance boundary: what happens when the network misbehaves.

Segment 8's error-path sweep, and the last of F-005. Six functions —
`prices._fetch_splits`, `prices._fetch_dividends`,
`prices._batch_fetch_ranges`, `sectors._lazy_yf`,
`sectors._fetch_from_yfinance`, `sectors.get_sector` — had **zero
executed lines**. They are the only code in `fin` that talks to the
outside world, so they are the only code whose inputs the project does
not control, and none of their failure handling had ever run.

The tests inject a fake `yfinance` into the module-level `_yf` global
that `_lazy_yf` memoises. No network, no new dependency, and the fakes
misbehave in the specific ways a real outage does: raising, returning
nothing, returning NaN, returning a partly-populated frame.

**The contract that matters is the split between raising and
swallowing**, and it is deliberately different per call site:

* `sectors` NEVER raises — a sector is cosmetic, and a lookup failure
  must not stop a pipeline run. It degrades to `"Other"`.
* `prices._fetch_*` DO raise — the caller (`ensure_coverage`) owns
  retry, backoff and tombstoning, and it can only do that if it hears
  about the failure. Swallowing here would silently mark a symbol
  covered with no data.
* `_batch_fetch_ranges` returns `None` rather than raising, because
  `None` means one specific thing to its caller: fall back to the
  per-symbol serial path, which owns the bookkeeping. A raise would
  take the whole run down over an outage the serial path can absorb.

Getting those three backwards is invisible until the day yfinance is
down, which is exactly when you least want to find out.
"""

from __future__ import annotations

import math

import pytest


class _FakeSeries:
    """Minimal stand-in for the pandas Series yfinance returns."""

    def __init__(self, items):
        self._items = list(items)

    @property
    def empty(self):
        return not self._items

    def items(self):
        return iter(self._items)


class _FakeDate:
    """An index entry that behaves like a pandas Timestamp."""

    def __init__(self, iso):
        self._iso = iso

    def date(self):
        import datetime
        return datetime.date.fromisoformat(self._iso)


class _FakeHistory:
    """Daily quotes plus action rows, including zero split-event rows."""

    def __init__(self, splits, *, close=100.0, include_splits=True):
        self.empty = splits is None
        self.columns = ["Close"] + (["Stock Splits"] if include_splits else [])
        self._data = {
            "Close": _FakeSeries([(_FakeDate("2024-06-03"), close)]),
            "Stock Splits": splits,
        }

    def __getitem__(self, key):
        return self._data[key]


class _FakeTicker:
    def __init__(self, *, splits=None, dividends=None, info=None,
                 info_raises=False, history=None, history_raises=False):
        self._splits = splits
        self._dividends = dividends
        self._info = info
        self._info_raises = info_raises
        self._history = history
        self._history_raises = history_raises
        self.history_calls = []

    def history(self, **kwargs):
        self.history_calls.append(kwargs)
        if self._history_raises:
            raise RuntimeError("simulated quote failure")
        if self._history is not None:
            return self._history
        return _FakeHistory(self._splits)

    @property
    def splits(self):
        return self._splits

    @property
    def dividends(self):
        return self._dividends

    @property
    def info(self):
        if self._info_raises:
            raise RuntimeError("simulated yfinance outage")
        return self._info


class _FakeYF:
    def __init__(self, ticker=None, download=None):
        self._ticker = ticker
        self._download = download
        self.ticker_calls = []

    def Ticker(self, symbol):
        self.ticker_calls.append(symbol)
        return self._ticker if self._ticker is not None else _FakeTicker()

    def download(self, **kw):
        if callable(self._download):
            return self._download(**kw)
        return self._download


@pytest.fixture
def prices_yf(isolated_workdir, monkeypatch):
    """Install a fake yfinance into `prices`, bypassing `_lazy_yf`'s
    import."""
    from src import prices as P

    P.reset_caches()

    def install(fake):
        monkeypatch.setattr(P, "_yf", fake if fake is not None else False)
        return P
    return install


@pytest.fixture
def sectors_yf(isolated_workdir, monkeypatch):
    from src import sectors as S

    S.reset_cache()

    def install(fake):
        monkeypatch.setattr(S, "_yf", fake if fake is not None else False)
        return S
    return install


# ---------------------------------------------------------------------------
# prices._fetch_splits
# ---------------------------------------------------------------------------

class TestFetchSplits:

    def test_a_normal_split_history_is_converted(self, prices_yf):
        P = prices_yf(_FakeYF(_FakeTicker(
            splits=_FakeSeries([(_FakeDate("2024-06-01"), 4.0)]))))
        assert P._fetch_splits("AAA") == [["2024-06-01", 4.0]]

    def test_a_reverse_split_ratio_is_preserved(self, prices_yf):
        """Ratio is new/old, so a 1-for-10 is 0.1. Coercing it to a whole
        number or an absolute value would silently invert every
        historical balance on that symbol."""
        P = prices_yf(_FakeYF(_FakeTicker(
            splits=_FakeSeries([(_FakeDate("2024-06-01"), 0.1)]))))
        assert P._fetch_splits("AAA") == [["2024-06-01", 0.1]]

    def test_no_split_history_is_empty_not_an_error(self, prices_yf):
        P = prices_yf(_FakeYF(_FakeTicker(splits=_FakeSeries([]))))
        assert P._fetch_splits("AAA") == []

    def test_unavailable_history_raises(self, prices_yf):
        P = prices_yf(_FakeYF(_FakeTicker(splits=None)))
        with pytest.raises(RuntimeError, match="unavailable"):
            P._fetch_splits("AAA")

    def test_requests_errors_instead_of_silent_empty_splits(self, prices_yf):
        ticker = _FakeTicker(history_raises=True)
        P = prices_yf(_FakeYF(ticker))
        with pytest.raises(RuntimeError, match="quote failure"):
            P._fetch_splits("AAA")
        assert ticker.history_calls == [{
            "period": "max", "auto_adjust": False,
            "actions": True, "raise_errors": True,
        }]

    def test_missing_action_column_does_not_claim_no_splits(self, prices_yf):
        ticker = _FakeTicker(history=_FakeHistory(
            _FakeSeries([]), include_splits=False))
        P = prices_yf(_FakeYF(ticker))
        with pytest.raises(RuntimeError, match="unavailable"):
            P._fetch_splits("AAA")

    def test_action_only_history_is_not_a_successful_quote(self, prices_yf):
        ticker = _FakeTicker(history=_FakeHistory(
            _FakeSeries([]), close=float("nan")))
        P = prices_yf(_FakeYF(ticker))
        with pytest.raises(RuntimeError, match="no valid prices"):
            P._fetch_splits("AAA")

    def test_zero_action_rows_are_not_split_events(self, prices_yf):
        P = prices_yf(_FakeYF(_FakeTicker(splits=_FakeSeries([
            (_FakeDate("2024-06-01"), 4.0),
            (_FakeDate("2024-06-03"), 0.0),
        ]))))
        assert P._fetch_splits("AAA") == [["2024-06-01", 4.0]]

    @pytest.mark.parametrize("ratio", [float("nan"), float("inf"), -1.0])
    def test_invalid_split_ratio_is_not_cached(self, prices_yf, ratio):
        P = prices_yf(_FakeYF(_FakeTicker(splits=_FakeSeries([
            (_FakeDate("2024-06-01"), ratio),
        ]))))
        with pytest.raises(ValueError, match="invalid split ratio"):
            P._fetch_splits("AAA")

    @pytest.mark.parametrize("raises", [False, True])
    def test_failed_refresh_preserves_split_evidence_and_prices(
            self, prices_yf, raises):
        ticker = _FakeTicker(splits=None, history_raises=raises)
        P = prices_yf(_FakeYF(ticker))
        old_prices = {"2024-05-31": 25.0}
        old_splits = [["2024-06-01", 4.0]]
        P._load_prices()["AAA"] = dict(old_prices)
        P._load_splits()["AAA"] = list(old_splits)
        P._load_meta()["symbols"]["AAA"] = {
            "covered_start": "2024-05-31", "covered_end": "2024-06-03",
        }
        P.revalidate_stale_caches(["AAA"], force=True, verbose=False)
        assert P._load_splits()["AAA"] == old_splits
        assert P._load_prices()["AAA"] == old_prices
        assert P._load_meta()["symbols"]["AAA"]["covered_start"] == "2024-05-31"

    @pytest.mark.parametrize("raises", [False, True])
    def test_failed_backfill_stays_missing_until_verified(
            self, prices_yf, monkeypatch, raises):
        monkeypatch.setenv("FIN_AS_OF_DATE", "2024-06-10")
        ticker = _FakeTicker(splits=None, history_raises=raises)
        P = prices_yf(_FakeYF(ticker))
        old_prices = {"2024-06-03": 100.0, "2024-06-07": 105.0}
        P._load_prices()["AAA"] = dict(old_prices)
        P._load_meta()["symbols"]["AAA"] = {
            "covered_start": "2024-06-03", "covered_end": "2024-06-07",
            "settled_through": "2024-06-07",
        }

        P.ensure_coverage(["AAA"], "2024-06-03", "2024-06-07", verbose=False)
        assert "AAA" not in P._load_splits()
        assert not P._splits_dirty
        assert P._load_prices()["AAA"] == old_prices
        assert len(ticker.history_calls) == 1
        entry = P._load_meta()["symbols"]["AAA"]
        assert entry["split_retry_after"] == "2024-06-11"
        assert entry["split_failure_count"] == 1

        # Recovery really reports no split events, which may now be cached.
        ticker._history_raises = False
        ticker._splits = _FakeSeries([])
        P.ensure_coverage(["AAA"], "2024-06-03", "2024-06-07", verbose=False)
        assert "AAA" not in P._load_splits()
        assert len(ticker.history_calls) == 1
        monkeypatch.setenv("FIN_AS_OF_DATE", "2024-06-11")
        P.ensure_coverage(["AAA"], "2024-06-03", "2024-06-07", verbose=False)
        assert P._load_splits()["AAA"] == []
        assert P._splits_dirty
        assert len(ticker.history_calls) == 2
        assert "split_retry_after" not in entry
        assert "split_failure_count" not in entry
        P.ensure_coverage(["AAA"], "2024-06-03", "2024-06-07", verbose=False)
        assert len(ticker.history_calls) == 2

    def test_a_caret_suffix_is_stripped_before_the_lookup(self, prices_yf):
        """Robinhood writes delisted/preferred placeholders as `AKRO^`.
        yfinance has never heard of that ticker."""
        fake = _FakeYF(_FakeTicker(splits=_FakeSeries([])))
        P = prices_yf(fake)
        P._fetch_splits("AKRO^")
        assert fake.ticker_calls == ["AKRO"]

    def test_missing_yfinance_raises(self, prices_yf):
        """It must RAISE, not return []. An empty list is
        indistinguishable from "this symbol has never split", which
        would be cached as fact."""
        P = prices_yf(None)
        with pytest.raises(RuntimeError, match="yfinance"):
            P._fetch_splits("AAA")

    def test_an_index_without_a_date_method_falls_back_to_a_string(
            self, prices_yf):
        P = prices_yf(_FakeYF(_FakeTicker(
            splits=_FakeSeries([("2024-06-01T00:00:00", 2.0)]))))
        assert P._fetch_splits("AAA") == [["2024-06-01", 2.0]]


class TestSplitFetchBackoff:
    @staticmethod
    def _seed(P):
        series = {"2024-06-03": 100.0, "2024-06-07": 105.0}
        P._load_prices()["AAA"] = dict(series)
        entry = {
            "covered_start": "2024-06-03", "covered_end": "2024-06-07",
            "settled_through": "2024-06-07", "last_fetch": "2024-06-07T20:00:00",
            "failure_count": 0,
        }
        P._load_meta()["symbols"]["AAA"] = dict(entry)
        return series, entry

    @staticmethod
    def _backfill(P):
        P.ensure_coverage(["AAA"], "2024-06-03", "2024-06-07", verbose=False)

    def test_split_only_failures_back_off_without_failing_quotes(
            self, prices_yf, monkeypatch):
        from datetime import date, timedelta

        ticker = _FakeTicker(history_raises=True)
        P = prices_yf(_FakeYF(ticker))
        series, original_entry = self._seed(P)
        day = date(2024, 6, 10)
        for failures, days in enumerate([1, 2, 4, 8, 16, 30, 30], start=1):
            monkeypatch.setenv("FIN_AS_OF_DATE", day.isoformat())
            self._backfill(P)
            entry = P._load_meta()["symbols"]["AAA"]
            assert entry["split_failure_count"] == failures
            assert entry["split_retry_after"] == (day + timedelta(days=days)).isoformat()
            assert {k: v for k, v in entry.items()
                    if not k.startswith("split_")} == original_entry
            assert "AAA" not in P._load_splits()
            assert P._load_prices()["AAA"] == series
            self._backfill(P)
            assert len(ticker.history_calls) == failures
            day += timedelta(days=days)

    @pytest.mark.parametrize("first", ["deep", "backfill"])
    def test_deep_refresh_and_backfill_share_the_cooldown(
            self, prices_yf, monkeypatch, first):
        monkeypatch.setenv("FIN_AS_OF_DATE", "2024-06-10")
        ticker = _FakeTicker(history_raises=True)
        P = prices_yf(_FakeYF(ticker))
        series, original_entry = self._seed(P)
        if first == "deep":
            P.revalidate_stale_caches(["AAA"], verbose=False)
        else:
            self._backfill(P)
        self._backfill(P)
        P._load_meta().pop("last_deep_refresh", None)
        P.revalidate_stale_caches(["AAA"], verbose=False)
        assert len(ticker.history_calls) == 1
        entry = P._load_meta()["symbols"]["AAA"]
        assert entry["split_failure_count"] == 1
        assert {k: v for k, v in entry.items()
                if not k.startswith("split_")} == original_entry
        assert P._load_prices()["AAA"] == series

        # An explicit deep refresh can retry early; it still cannot convert
        # an unavailable history into confirmed no-split evidence.
        P.revalidate_stale_caches(["AAA"], force=True, verbose=False)
        assert len(ticker.history_calls) == 2
        assert entry["split_failure_count"] == 2
        assert entry["split_retry_after"] == "2024-06-12"
        assert "AAA" not in P._load_splits()

        ticker._history_raises = False
        ticker._splits = _FakeSeries([])
        P.revalidate_stale_caches(["AAA"], force=True, verbose=False)
        assert len(ticker.history_calls) == 3
        assert P._load_splits()["AAA"] == []
        assert "split_failure_count" not in entry
        assert "split_retry_after" not in entry

    def test_force_does_not_fetch_splits_before_unblocking_price_tombstone(
            self, prices_yf, monkeypatch):
        monkeypatch.setenv("FIN_AS_OF_DATE", "2024-06-10")
        ticker = _FakeTicker(history_raises=True)
        P = prices_yf(_FakeYF(ticker))
        self._seed(P)
        entry = P._load_meta()["symbols"]["AAA"]
        entry.update({"failure_count": 5, "tombstone": True,
                      "retry_after": "2024-06-30", "split_failure_count": 2,
                      "split_retry_after": "2024-06-12"})
        P.revalidate_stale_caches(["AAA"], force=True, verbose=False)
        assert ticker.history_calls == []
        assert entry["split_failure_count"] == 2
        assert entry["failure_count"] == 5
        assert not entry.get("tombstone")

    @pytest.mark.parametrize("split_error", [False, True])
    def test_fresh_quotes_require_split_check_even_during_split_backoff(
            self, prices_yf, monkeypatch, split_error):
        from datetime import date, datetime

        monkeypatch.setenv("FIN_AS_OF_DATE", "2024-06-10")
        ticker = _FakeTicker(splits=_FakeSeries([]), history_raises=split_error)
        P = prices_yf(_FakeYF(ticker))
        self._seed(P)
        entry = P._load_meta()["symbols"]["AAA"]
        entry.update({"split_failure_count": 4, "split_retry_after": "2024-06-18"})
        P._apply_fetch_success("AAA", {"2024-06-10": 106.0},
                               date(2024, 6, 10), date(2024, 6, 10),
                               datetime(2024, 6, 10, 20), verbose=False)
        assert len(ticker.history_calls) == 1
        assert entry["covered_end"] == "2024-06-10"
        assert entry["failure_count"] == 0
        assert P._load_prices()["AAA"]["2024-06-10"] == 106.0
        if split_error:
            assert "AAA" not in P._load_splits()
            assert entry["split_failure_count"] == 5
            assert entry["split_retry_after"] == "2024-06-26"
        else:
            assert P._load_splits()["AAA"] == []
            assert "split_failure_count" not in entry
            assert "split_retry_after" not in entry


# ---------------------------------------------------------------------------
# prices._fetch_dividends
# ---------------------------------------------------------------------------

class TestFetchDividends:

    def test_a_normal_dividend_history_is_converted(self, prices_yf):
        P = prices_yf(_FakeYF(_FakeTicker(
            dividends=_FakeSeries([(_FakeDate("2024-03-15"), 1.25)]))))
        assert P._fetch_dividends("AAA") == [["2024-03-15", 1.25]]

    def test_no_dividends_is_empty(self, prices_yf):
        P = prices_yf(_FakeYF(_FakeTicker(dividends=_FakeSeries([]))))
        assert P._fetch_dividends("AAA") == []

    @pytest.mark.parametrize("amount", [float("nan"), 0.0, -1.0])
    def test_unusable_amounts_are_dropped(self, prices_yf, amount):
        """A NaN dividend would propagate into the total-return factor
        and from there into every price the symbol reports. Zero and
        negative are not dividends."""
        P = prices_yf(_FakeYF(_FakeTicker(
            dividends=_FakeSeries([(_FakeDate("2024-03-15"), amount)]))))
        assert P._fetch_dividends("AAA") == []

    def test_a_non_numeric_amount_is_dropped(self, prices_yf):
        P = prices_yf(_FakeYF(_FakeTicker(
            dividends=_FakeSeries([(_FakeDate("2024-03-15"), "n/a")]))))
        assert P._fetch_dividends("AAA") == []

    def test_good_entries_survive_beside_bad_ones(self, prices_yf):
        """Not-vacuous guard: the filter must drop the bad row, not the
        whole series."""
        P = prices_yf(_FakeYF(_FakeTicker(dividends=_FakeSeries([
            (_FakeDate("2024-03-15"), float("nan")),
            (_FakeDate("2024-06-15"), 1.50),
        ]))))
        assert P._fetch_dividends("AAA") == [["2024-06-15", 1.5]]

    def test_missing_yfinance_raises(self, prices_yf):
        P = prices_yf(None)
        with pytest.raises(RuntimeError, match="yfinance"):
            P._fetch_dividends("AAA")


# ---------------------------------------------------------------------------
# prices._batch_fetch_ranges
# ---------------------------------------------------------------------------

class TestBatchFetchFallsBackRatherThanFailing:
    """`None` is a specific instruction to the caller: use the serial
    path, which owns retry and failure bookkeeping. Raising instead
    would take a whole run down over an outage the serial path handles.
    """

    def _dates(self):
        from datetime import date
        return date(2024, 6, 1), date(2024, 6, 30)

    def test_missing_yfinance_returns_none(self, prices_yf):
        P = prices_yf(None)
        assert P._batch_fetch_ranges(["AAA"], *self._dates()) is None

    def test_a_download_exception_returns_none(self, prices_yf):
        def boom(**kw):
            raise RuntimeError("simulated yfinance outage")

        P = prices_yf(_FakeYF(download=boom))
        assert P._batch_fetch_ranges(["AAA", "BBB"], *self._dates()) is None

    def test_an_empty_frame_is_no_rows_not_a_failure(self, prices_yf):
        """A weekend or holiday range legitimately returns nothing. That
        is `{}` — zero symbols fetched — and NOT `None`, which would
        send the caller down the serial path to re-ask and book a
        failure for every symbol."""
        class _EmptyDF:
            empty = True

        P = prices_yf(_FakeYF(download=lambda **kw: _EmptyDF()))
        assert P._batch_fetch_ranges(["AAA"], *self._dates()) == {}

    def test_a_none_frame_is_also_no_rows(self, prices_yf):
        P = prices_yf(_FakeYF(download=lambda **kw: None))
        assert P._batch_fetch_ranges(["AAA"], *self._dates()) == {}


# ---------------------------------------------------------------------------
# sectors
# ---------------------------------------------------------------------------

class TestSectorLookupNeverRaises:
    """A sector is cosmetic. A lookup failure must never stop a run, so
    every path here degrades to "Other" instead of propagating."""

    def test_missing_yfinance_degrades_to_other(self, sectors_yf):
        S = sectors_yf(None)
        assert S._fetch_from_yfinance("AAA") == "Other"

    def test_an_api_exception_degrades_to_other(self, sectors_yf):
        S = sectors_yf(_FakeYF(_FakeTicker(info_raises=True)))
        assert S._fetch_from_yfinance("AAA") == "Other"

    def test_a_sector_string_is_returned(self, sectors_yf):
        S = sectors_yf(_FakeYF(_FakeTicker(info={"sector": "Technology"})))
        assert S._fetch_from_yfinance("AAA") == "Technology"

    def test_a_blank_sector_falls_through_to_quote_type(self, sectors_yf):
        S = sectors_yf(_FakeYF(_FakeTicker(
            info={"sector": "   ", "quoteType": "etf"})))
        assert S._fetch_from_yfinance("AAA") == "ETFs"

    @pytest.mark.parametrize("qt,expected", [
        ("ETF", "ETFs"),
        ("MUTUALFUND", "Mutual Funds"),
        ("CRYPTOCURRENCY", "Cryptocurrency"),
        ("EQUITY", "Other"),
        ("", "Other"),
    ])
    def test_quote_type_mapping(self, sectors_yf, qt, expected):
        S = sectors_yf(_FakeYF(_FakeTicker(info={"quoteType": qt})))
        assert S._fetch_from_yfinance("AAA") == expected

    def test_a_none_info_degrades_to_other(self, sectors_yf):
        S = sectors_yf(_FakeYF(_FakeTicker(info=None)))
        assert S._fetch_from_yfinance("AAA") == "Other"

    def test_a_caret_suffix_is_stripped(self, sectors_yf):
        fake = _FakeYF(_FakeTicker(info={"sector": "Healthcare"}))
        S = sectors_yf(fake)
        assert S._fetch_from_yfinance("AKRO^") == "Healthcare"
        assert fake.ticker_calls == ["AKRO"]


class TestGetSector:

    def test_a_hard_rule_short_circuits_before_any_fetch(self, sectors_yf):
        """`_classify_no_fetch` answers for cash, crypto and multi-word
        fund names. Those must never reach the network."""
        fake = _FakeYF(_FakeTicker(info={"sector": "WRONG"}))
        S = sectors_yf(fake)
        assert S.get_sector("USD") == "Cash"
        assert S.get_sector("BTC-USD") == "Cryptocurrency"
        assert fake.ticker_calls == [], "a hard-rule symbol hit the network"

    def test_a_cached_symbol_is_not_refetched(self, sectors_yf):
        fake = _FakeYF(_FakeTicker(info={"sector": "Technology"}))
        S = sectors_yf(fake)
        assert S.get_sector("AAA") == "Technology"
        assert S.get_sector("AAA") == "Technology"
        assert fake.ticker_calls == ["AAA"], (
            "the second lookup refetched — misses must cache too, or a "
            "run re-asks for every unresolvable symbol"
        )

    def test_a_miss_is_cached_so_no_symbol_is_fetched_twice(self,
                                                            sectors_yf):
        """CLAUDE.md: both hits and misses cache, so no symbol is fetched
        twice per run."""
        fake = _FakeYF(_FakeTicker(info={}))
        S = sectors_yf(fake)
        assert S.get_sector("AAA") == "Other"
        assert S.get_sector("AAA") == "Other"
        assert fake.ticker_calls == ["AAA"]

    def test_a_caret_symbol_reuses_its_base_from_cache(self, sectors_yf):
        """`AKRO^` and `AKRO` are the same company. Once the base is
        known, the placeholder must not cost a second lookup."""
        fake = _FakeYF(_FakeTicker(info={"sector": "Healthcare"}))
        S = sectors_yf(fake)
        assert S.get_sector("AKRO") == "Healthcare"
        assert S.get_sector("AKRO^") == "Healthcare"
        assert fake.ticker_calls == ["AKRO"], (
            "the caret variant triggered its own fetch"
        )


class TestAnOutageDoesNotStopARun:
    """The integration claim behind all of the above."""

    def test_sector_enrichment_survives_a_total_outage(self, sectors_yf):
        S = sectors_yf(_FakeYF(_FakeTicker(info_raises=True)))
        holdings = [{"symbol": "AAA", "quantity": 1.0},
                    {"symbol": "BBB", "quantity": 2.0}]
        out = S.enrich_holdings(holdings)
        assert [h["sector"] for h in out] == ["Other", "Other"], (
            "a yfinance outage during sector enrichment must degrade, "
            "not raise — sectors are cosmetic and the run has real work "
            "left to do"
        )

    def test_price_coverage_records_a_failure_rather_than_crashing(
            self, isolated_workdir, monkeypatch):
        """`ensure_coverage` is the layer that owns retry bookkeeping, so
        an outage there must land as a recorded failure with a backoff —
        not an exception, and not silent success."""
        from datetime import date, timedelta

        from src import prices as P

        P.reset_caches()
        monkeypatch.setattr(P, "_batch_fetch_ranges",
                            lambda *a, **k: None)   # batching unavailable

        def boom(*a, **k):
            raise RuntimeError("simulated yfinance outage")

        monkeypatch.setattr(P, "_fetch_range", boom)
        monkeypatch.setattr(P, "_fetch_splits", lambda s: [])

        end = date.today()
        P.ensure_coverage(["AAA"], end - timedelta(days=60), end)

        meta = P._load_meta().get("symbols", {}).get("AAA", {})
        assert meta.get("failure_count", 0) >= 1, (
            "an outage left no failure recorded — the backoff schedule "
            "never starts and the next run hammers the same dead symbol"
        )
        assert meta.get("retry_after"), "no backoff was scheduled"
