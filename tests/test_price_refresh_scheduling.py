"""Deep refresh and incremental fetch must share one retry schedule.

All symbols, prices, and cache entries below are independently fictional.
The clock and every network entry point are controlled in isolated caches.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


START = "2024-01-02"
END = "2024-06-19"


@pytest.fixture
def market(isolated_workdir, stub_prices, monkeypatch):
    from src import prices

    monkeypatch.setenv("FIN_AS_OF_DATE", "2024-06-20")
    calls = SimpleNamespace(
        prices=prices,
        history=Mock(return_value={}),
        batch=Mock(return_value={}),
        splits=Mock(return_value=[]),
        dividends=Mock(return_value=[]),
    )
    monkeypatch.setattr(prices, "_fetch_range", calls.history)
    monkeypatch.setattr(prices, "_batch_fetch_ranges", calls.batch)
    monkeypatch.setattr(prices, "_fetch_splits", calls.splits)
    monkeypatch.setattr(prices, "_fetch_dividends", calls.dividends)
    monkeypatch.setattr(
        prices, "_lazy_yf", Mock(side_effect=AssertionError("network forbidden"))
    )
    return calls


def seed_cache(prices, symbol="FAKEA", *, failures=5, retry_after="2024-06-01"):
    """Seed retained history alongside a failing fetch's persisted state."""
    prices._load_prices()[symbol] = {START: 80.0, "2024-01-31": 84.0}
    prices._mark_prices_dirty(symbol)
    entry = {
        "covered_start": START,
        "covered_end": "2024-01-31",
        "settled_through": "2024-01-31",
        "failure_count": failures,
    }
    if failures:
        entry.update({
            "last_fetch": "2024-05-01T00:00:00",
            "last_error": "fictional missing quote",
            "retry_after": retry_after,
        })
    if failures >= 5:
        entry["tombstone"] = True
    prices._load_meta()["symbols"][symbol] = entry
    prices._meta_dirty = True
    prices._load_splits()[symbol] = []
    prices._splits_dirty = True
    return entry


def refresh_and_cover(prices, symbols, *, closed=None, force=False):
    prices.revalidate_stale_caches(
        symbols, closed_symbols=closed, force=force, verbose=False
    )
    prices.ensure_coverage(symbols, START, END, verbose=False)


def assert_no_requests(market):
    for fetch in (market.history, market.batch, market.splits, market.dividends):
        fetch.assert_not_called()


@pytest.mark.parametrize("failures", [2, 5])
def test_deep_and_incremental_refresh_respect_pending_retry(market, failures):
    prices = market.prices
    entry = seed_cache(prices, failures=failures, retry_after="2024-06-28")
    # A scaled proxy makes this target eligible for dividend refresh too.
    prices._load_proxy_map()["Example Fund"] = {
        "proxy": "FAKEA", "method": "scaled"
    }
    prices._load_dividends()["FAKEA"] = [["2024-01-15", 1.0]]
    before_entry = deepcopy(entry)
    before_prices = deepcopy(prices._load_prices())
    before_dividends = deepcopy(prices._load_dividends())

    refresh_and_cover(prices, ["Example Fund", "FAKEA"])

    assert_no_requests(market)
    assert entry == before_entry
    assert prices._load_prices() == before_prices
    assert prices._load_dividends() == before_dividends
    assert prices._load_splits()["FAKEA"] == []


def test_closed_tombstone_survives_save_restart_and_later_weekly_refresh(
    market, monkeypatch
):
    prices = market.prices
    expected_entry = deepcopy(seed_cache(prices))
    expected_history = deepcopy(prices._load_prices()["FAKEA"])

    refresh_and_cover(prices, ["FAKEA"], closed={"FAKEA"})
    prices.save_caches()
    prices.reset_caches()
    monkeypatch.setenv("FIN_AS_OF_DATE", "2024-07-20")
    refresh_and_cover(prices, ["FAKEA"], closed={"FAKEA"})

    assert_no_requests(market)
    assert prices._load_meta()["symbols"]["FAKEA"] == expected_entry
    assert prices._load_prices()["FAKEA"] == expected_history
    assert prices.get_price("FAKEA", START) == 80.0


@pytest.mark.parametrize("closed", [set(), {"FAKEA"}])
def test_forced_refresh_retries_before_due_and_success_clears_failure_state(
    market, closed
):
    prices = market.prices
    seed_cache(prices, retry_after="2024-07-20")
    untouched = deepcopy(seed_cache(prices, "FAKEB"))
    prices._load_meta()["last_deep_refresh"] = "2024-06-20"
    market.history.return_value = {END: 88.0}

    refresh_and_cover(prices, ["FAKEA"], closed=closed, force=True)

    market.history.assert_called_once()
    market.splits.assert_called_once_with("FAKEA")
    market.batch.assert_not_called()
    entry = prices._load_meta()["symbols"]["FAKEA"]
    assert entry["failure_count"] == 0
    assert all(key not in entry for key in ("tombstone", "retry_after", "last_error"))
    assert prices.get_price("FAKEA", START) == 80.0
    assert prices.get_price("FAKEA", END) == 88.0
    assert prices._load_meta()["symbols"]["FAKEB"] == untouched


def test_reopened_due_position_retries_without_restarting_backoff(market, monkeypatch):
    prices = market.prices
    seed_cache(prices)
    refresh_and_cover(prices, ["FAKEA"], closed={"FAKEA"})
    assert_no_requests(market)

    # A later run now has an open position. A failed retry must retain the
    # accumulated backoff instead of reverting to the first-failure delay.
    monkeypatch.setenv("FIN_AS_OF_DATE", "2024-06-27")
    refresh_and_cover(prices, ["FAKEA"])
    entry = prices._load_meta()["symbols"]["FAKEA"]
    assert entry["failure_count"] == 6
    assert entry["tombstone"] is True
    assert entry["retry_after"] == "2024-07-27"
    market.history.assert_called_once()
    market.splits.assert_not_called()

    prices.save_caches()
    prices.reset_caches()
    monkeypatch.setenv("FIN_AS_OF_DATE", "2024-07-04")
    refresh_and_cover(prices, ["FAKEA"])
    market.history.assert_called_once()
    assert prices._load_meta()["symbols"]["FAKEA"]["retry_after"] == "2024-07-27"

    monkeypatch.setenv("FIN_AS_OF_DATE", "2024-07-27")
    refresh_and_cover(prices, ["FAKEA"])
    entry = prices._load_meta()["symbols"]["FAKEA"]
    assert market.history.call_count == 2
    assert entry["failure_count"] == 7
    assert entry["retry_after"] == "2024-08-26"
    assert prices.get_price("FAKEA", START) == 80.0


@pytest.mark.parametrize("reverse_order", [False, True])
def test_shared_proxy_stays_active_for_an_open_consumer(market, reverse_order):
    prices = market.prices
    seed_cache(prices)
    prices._load_proxy_map().update({
        "FAKE.A": {"proxy": "FAKEA", "method": "direct"},
        "Example Fund": {"proxy": "FAKEA", "method": "scaled"},
    })
    symbols = ["FAKE.A", "Example Fund", "FAKEA", "Example Fund"]
    if reverse_order:
        symbols.reverse()
    market.history.return_value = {END: 88.0}

    refresh_and_cover(prices, symbols, closed={"FAKE.A", "FAKEA"})

    market.history.assert_called_once()
    assert market.history.call_args.args[0] == "FAKEA"
    market.batch.assert_not_called()
    market.splits.assert_called_once_with("FAKEA")
    market.dividends.assert_called_once_with("FAKEA")
    assert prices._load_meta()["symbols"]["FAKEA"]["failure_count"] == 0


def test_healthy_direct_and_scaled_targets_refresh_only_once(market):
    prices = market.prices
    seed_cache(prices, failures=0)
    prices._load_proxy_map().update({
        "FAKE.A": {"proxy": "FAKEA", "method": "direct"},
        "Example Fund": {"proxy": "FAKEA", "method": "scaled"},
    })

    prices.revalidate_stale_caches(
        ["Example Fund", "FAKE.A", "FAKEA", "Example Fund"], verbose=False
    )

    market.splits.assert_called_once_with("FAKEA")
    market.dividends.assert_called_once_with("FAKEA")


def test_split_change_still_refetches_entire_adjusted_history(market):
    prices = market.prices
    seed_cache(prices, failures=0)
    market.splits.return_value = [["2024-06-03", 2.0]]
    market.history.return_value = {START: 40.0, "2024-01-31": 42.0, END: 44.0}

    prices.revalidate_stale_caches(["FAKEA"], verbose=False)

    assert "FAKEA" not in prices._load_prices()
    entry = prices._load_meta()["symbols"]["FAKEA"]
    assert all(key not in entry for key in (
        "covered_start", "covered_end", "settled_through"
    ))
    prices.ensure_coverage(["FAKEA"], START, END, verbose=False)
    market.history.assert_called_once_with(
        "FAKEA", date.fromisoformat(START), date.fromisoformat(END)
    )

    prices.save_caches()
    prices.reset_caches()
    assert prices.get_price("FAKEA", START) == 40.0
    assert prices.get_price("FAKEA", "2024-01-31") == 42.0
    assert prices._load_splits()["FAKEA"] == [["2024-06-03", 2.0]]
    assert prices._load_meta()["symbols"]["FAKEA"]["covered_end"] == END
