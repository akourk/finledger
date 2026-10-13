"""Fictional quote observations, successful-fetch evidence, and run reports."""

from copy import deepcopy
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def market(isolated_workdir, monkeypatch):
    from src import prices

    monkeypatch.setenv("FIN_AS_OF_DATE", "2024-06-20")
    monkeypatch.setattr(prices, "_fetch_range", Mock(return_value={}))
    monkeypatch.setattr(prices, "_batch_fetch_ranges", Mock(return_value={}))
    monkeypatch.setattr(prices, "_fetch_splits", Mock(return_value=[]))
    monkeypatch.setattr(prices, "_fetch_dividends", Mock(return_value=[]))
    monkeypatch.setattr(prices, "_lazy_yf", Mock(side_effect=AssertionError("network forbidden")))
    return prices


def test_observation_keeps_real_bar_date_when_lookback_skips_invalid_values(market):
    market._load_prices()["FAKEA"] = {
        "2024-06-14": 80.0, "2024-06-17": float("nan"),
        "2024-06-18": "82", "2024-06-19": True,
    }
    assert market.get_price_observation("FAKEA", "2024-06-19") == {
        "price": 80.0, "date": "2024-06-14", "quote_symbol": "FAKEA",
        "estimated": False,
    }
    assert market.get_price("FAKEA", "2024-06-19") == 80.0
    assert market.get_price_observation("FAKEA", "2024-06-22") is None
    assert market.get_price_observation("USD", "2024-06-19") is None


def test_proxy_observation_matches_scaled_total_return_price(market):
    market._load_prices()["FAKEA"] = {"2024-06-14": 100.0, "2024-06-17": 120.0}
    market._load_proxy_map().update({
        "Example Fund": {"proxy": "FAKEA", "method": "scaled",
                         "anchor_date": "2024-06-14", "anchor_price": 50.0},
        "DIRECT": {"proxy": "FAKEA", "method": "direct"},
    })
    market._load_dividends()["FAKEA"] = [["2024-06-17", 2.0]]
    # Scaled proxy targets use total return: anchor close becomes 98.
    observation = market.get_price_observation("Example Fund", "2024-06-18")
    assert observation == {
        "price": pytest.approx(50.0 * 120.0 / 98.0),
        "date": "2024-06-17", "quote_symbol": "FAKEA", "estimated": True,
    }
    assert market.get_price("Example Fund", "2024-06-18") == observation["price"]
    assert market.get_price_observation("DIRECT", "2024-06-16") == {
        "price": 98.0, "date": "2024-06-14", "quote_symbol": "FAKEA",
        "estimated": False,
    }


@pytest.mark.parametrize("state", [
    {"failure_count": 1}, {"last_error": "fictional provider error"},
    {"tombstone": True},
])
def test_legacy_failure_stamp_is_not_success_evidence(market, state):
    market._load_prices()["FAKEA"] = {"2024-06-14": 80.0}
    market._load_meta()["symbols"]["FAKEA"] = {
        "last_fetch": "2024-06-20T00:00:00", **state,
    }
    assert market.last_fetch_at("FAKEA") is None


def test_legacy_clean_stamp_requires_a_usable_cached_bar(market):
    market._load_meta()["symbols"]["FAKEA"] = {"last_fetch": "2024-06-14T20:00:00"}
    assert market.last_fetch_at("FAKEA") is None
    market._load_prices()["FAKEA"] = {"2024-06-14": 80.0}
    assert market.last_fetch_at("FAKEA") == "2024-06-14T20:00:00"


def test_failed_request_preserves_prior_success_and_legacy_evidence(market):
    market._load_prices()["FAKEA"] = {"2024-06-14": 80.0}
    market._load_meta()["symbols"]["FAKEA"] = {"last_fetch": "2024-06-14T20:00:00"}
    market._record_failure("FAKEA", "fictional outage", datetime(2024, 6, 20))
    entry = market._load_meta()["symbols"]["FAKEA"]
    assert entry["last_attempt"] == "2024-06-20T00:00:00"
    assert market.last_fetch_at("FAKEA") == "2024-06-14T20:00:00"
    market._record_success("FAKEA", date(2024, 6, 14), date(2024, 6, 20),
                           datetime(2024, 6, 20, 12))
    assert market.last_fetch_at("FAKEA") == "2024-06-20T12:00:00"
    assert entry["last_attempt"] == entry["last_successful_fetch"]
    assert market.price_refresh_report() == [{
        "symbol": "FAKEA", "status": "updated", "retry_after": None, "error": None,
    }]


def test_unknown_fetchable_holding_suppresses_portfolio_refresh_stamp(market):
    market._load_meta()["symbols"]["FAKEA"] = {
        "last_successful_fetch": "2024-06-14T20:00:00"}
    assert market.oldest_last_fetch(["FAKEA", "FAKEB"]) is None
    assert market.oldest_last_fetch(["FAKEA", "USD"]) == "2024-06-14T20:00:00"


def test_coverage_report_distinguishes_cached_deferred_unavailable_and_no_fetch(market, capsys):
    market._load_prices()["FAKEA"] = {"2024-06-19": 80.0}
    market._load_meta()["symbols"].update({
        "FAKEA": {"covered_start": "2024-06-19", "covered_end": "2024-06-19",
                  "settled_through": "2024-06-19"},
        "FAKEB": {"retry_after": "2024-06-21", "last_error": "fictional outage"},
    })
    market._load_proxy_map()["DIRECT"] = {"proxy": "FAKEB", "method": "direct"}
    market.ensure_coverage(["FAKEA", "FAKEB", "DIRECT", "FAKEC", "USD"],
                           "2024-06-19", "2024-06-19")
    rows = {row["symbol"]: row for row in market.price_refresh_report()}
    assert {symbol: row["status"] for symbol, row in rows.items()} == {
        "FAKEA": "cached", "FAKEB": "deferred", "FAKEC": "unavailable",
        "USD": "not_fetchable",
    }
    assert rows["FAKEB"]["retry_after"] == "2024-06-21"
    assert market._load_meta()["symbols"]["FAKEC"]["last_successful_fetch"] is None
    assert "retry_after" not in market._load_meta()["symbols"]["FAKEC"]
    market.print_price_refresh_report()
    output = capsys.readouterr().out
    assert "1 already cached" in output and "1 deferred" in output
    assert "FAKEB: deferred; next retry 2024-06-21" in output
    assert "Price cache up to date" not in output


def test_only_deferred_quotes_do_not_claim_cache_is_up_to_date(market, capsys):
    market._load_meta()["symbols"]["FAKEA"] = {"retry_after": "2024-06-21"}
    market.ensure_coverage(["FAKEA"], "2024-06-01", "2024-06-19")
    assert "Price refresh deferred" in capsys.readouterr().out


def test_full_fetch_failure_is_reported_once_and_skip_keeps_failure(market):
    market._fetch_range.side_effect = RuntimeError("fictional outage")
    market.ensure_coverage(["FAKEA"], "2024-06-01", "2024-06-19", verbose=False)
    market.ensure_coverage(["FAKEA"], "2024-06-01", "2024-06-19", verbose=False)
    assert market.price_refresh_report() == [{
        "symbol": "FAKEA", "status": "failed", "retry_after": "2024-06-21",
        "error": "fictional outage",
    }]


def test_partial_latest_batch_reports_every_target_without_new_cooldowns(market, monkeypatch):
    import pandas as pd

    frame = pd.concat({"FAKEA": pd.DataFrame(
        {"Close": [90.0]}, index=pd.to_datetime(["2024-06-19"]))}, axis=1)
    download = Mock(return_value=frame)
    monkeypatch.setattr(market, "_lazy_yf", lambda: SimpleNamespace(download=download))
    market._load_proxy_map()["Example Fund"] = {"proxy": "FAKEA", "method": "scaled"}
    market._load_prices()["FAKEB"] = {"2024-06-14": 70.0}
    market._load_meta()["symbols"]["FAKEB"] = {"last_fetch": "2024-06-14T20:00:00"}
    market._load_meta()["symbols"]["FAKEC"] = {"retry_after": "2024-06-21"}
    assert market.fetch_latest_close_batch(
        ["Example Fund", "FAKEA", "FAKEB", "FAKEC", "USD"], verbose=False) == 1
    assert download.call_args.kwargs["tickers"] == "FAKEA FAKEB"
    rows = {row["symbol"]: row for row in market.price_refresh_report()}
    assert {symbol: row["status"] for symbol, row in rows.items()} == {
        "FAKEA": "updated", "FAKEB": "unavailable", "FAKEC": "deferred",
        "USD": "not_fetchable",
    }
    assert market.last_fetch_at("FAKEA") == "2024-06-20T00:00:00"
    assert market.last_fetch_at("FAKEB") == "2024-06-14T20:00:00"
    assert market._load_prices()["FAKEB"] == {"2024-06-14": 70.0}
    assert "retry_after" not in market._load_meta()["symbols"]["FAKEB"]


@pytest.mark.parametrize("response,status", [(None, "unavailable"),
    (RuntimeError("fictional outage"), "failed")])
def test_empty_or_failed_latest_batch_reports_targets_without_cooldown(market, monkeypatch,
                                                                       response, status):
    download = (Mock(side_effect=response) if isinstance(response, Exception)
                else Mock(return_value=response))
    monkeypatch.setattr(market, "_lazy_yf", lambda: SimpleNamespace(download=download))
    assert market.fetch_latest_close_batch(["FAKEA", "FAKEB"], verbose=False) == 0
    assert [row["status"] for row in market.price_refresh_report()] == [status, status]
    assert all("retry_after" not in entry
               for entry in market._load_meta()["symbols"].values())


def test_reporting_is_read_only_and_reset_discards_run_outcomes(market):
    market._load_meta()["symbols"]["FAKEA"] = {"retry_after": "2024-06-21"}
    market.ensure_coverage(["FAKEA"], "2024-06-01", "2024-06-19", verbose=False)
    before = deepcopy(market._load_meta())
    report = market.price_refresh_report()
    report[0]["status"] = "modified"
    market.print_price_refresh_report()
    assert market.price_refresh_report()[0]["status"] == "deferred"
    assert market._load_meta() == before
    market.reset_caches()
    assert market.price_refresh_report() == []


def test_new_run_report_reset_preserves_loaded_cache_and_retry_state(market):
    market._load_prices()["FAKEA"] = {"2024-06-14": 80.0}
    market._load_meta()["symbols"]["FAKEA"] = {"retry_after": "2024-06-21"}
    market.ensure_coverage(["FAKEA"], "2024-06-01", "2024-06-19", verbose=False)
    before_prices = market._load_prices()
    before_meta = deepcopy(market._load_meta())
    market.reset_price_refresh_report()
    assert market.price_refresh_report() == []
    assert market._load_prices() is before_prices
    assert market._load_meta() == before_meta
    assert market.get_price("FAKEA", "2024-06-14") == 80.0


def test_only_unfetchable_symbols_do_not_claim_cache_is_up_to_date(market, capsys):
    market.ensure_coverage(["USD", "Example Fund"], "2024-06-01", "2024-06-19")
    output = capsys.readouterr().out
    assert "No fetchable price quotes" in output
    assert "Price cache up to date" not in output
