"""Asset calendars must preserve live weekend crypto marks and settled closes."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest


def _pin(monkeypatch, iso_utc):
    from src import prices

    instant = datetime.fromisoformat(iso_utc).replace(tzinfo=timezone.utc)
    monkeypatch.setattr(prices, "_now_utc", lambda: instant)
    monkeypatch.setattr(prices, "_today", lambda: instant.astimezone(
        ZoneInfo("America/New_York")).date())
    monkeypatch.setattr(prices.clock, "now", lambda **kwargs: instant.replace(
        tzinfo=None))


def _seed_settled(symbol, end="2026-08-07"):
    from src import prices

    prices._load_prices()[symbol] = {end: 100.0}
    prices._load_meta()["symbols"][symbol] = {
        "covered_start": end, "covered_end": end, "settled_through": end,
    }
    prices._load_splits()[symbol] = []


@pytest.fixture
def calendar_quotes(stub_prices, monkeypatch):
    """Only return fictional rows inside the actual requested interval."""
    from src import prices

    calls = []

    def fetch(symbol, start, end):
        calls.append((symbol, start.isoformat(), end.isoformat()))
        return {day: value for day, value in
                stub_prices.registered.get(symbol, {}).items()
                if start.isoformat() <= day <= end.isoformat()}

    def batch(symbols, start, end):
        return {symbol: {"prices": values, "split_event": False}
                for symbol in symbols if (values := fetch(symbol, start, end))}

    monkeypatch.setattr(prices, "_fetch_range", fetch)
    monkeypatch.setattr(prices, "_batch_fetch_ranges", batch)
    return stub_prices, calls


@pytest.mark.parametrize("end", ["2026-08-08", "2026-08-09"])
@pytest.mark.parametrize("symbol", ["BTC-USD", "USDC"])
def test_weekend_crypto_coverage_extends_beyond_friday(
        calendar_quotes, monkeypatch, symbol, end):
    from src import prices

    quotes, calls = calendar_quotes
    _pin(monkeypatch, end + "T16:00:00")
    _seed_settled(symbol)
    quotes.set(symbol, {"2026-08-08": 110.0, "2026-08-09": 120.0})

    prices.ensure_coverage([symbol], "2026-08-07", end, verbose=False)

    assert calls == [(symbol, "2026-08-08", end)]
    assert prices.get_price(symbol, end) == quotes.registered[symbol][end]
    assert prices._load_meta()["symbols"][symbol]["covered_end"] == end
    assert prices.any_provisional([symbol]) is True


@pytest.mark.parametrize("symbol", ["BTC-USD", "USDC"])
def test_first_request_for_only_a_weekend_crypto_day_is_not_empty(
        isolated_workdir, monkeypatch, symbol):
    from src import prices

    _pin(monkeypatch, "2026-08-08T16:00:00")
    saturday = date(2026, 8, 8)
    assert prices._missing_ranges(symbol, saturday, saturday) == [
        (saturday, saturday)]


def test_weekend_crypto_mark_refetches_until_utc_settlement(
        calendar_quotes, monkeypatch):
    from src import prices

    quotes, calls = calendar_quotes
    _seed_settled("BTC-USD")
    _pin(monkeypatch, "2026-08-08T23:59:00")  # Saturday afternoon locally.
    quotes.set("BTC-USD", {"2026-08-08": 110.0})
    prices.ensure_coverage(["BTC-USD"], "2026-08-07", "2026-08-08", verbose=False)
    assert prices.any_provisional(["BTC-USD"]) is True

    # UTC midnight finalizes Saturday while the user's local date is still Saturday.
    _pin(monkeypatch, "2026-08-09T00:01:00")
    quotes.set("BTC-USD", {"2026-08-08": 112.0})
    prices.ensure_coverage(["BTC-USD"], "2026-08-07", "2026-08-08", verbose=False)
    assert prices.get_price("BTC-USD", "2026-08-08") == 112.0
    assert prices._load_meta()["symbols"]["BTC-USD"]["settled_through"] == "2026-08-08"
    assert prices.any_provisional(["BTC-USD"]) is False

    quotes.set("BTC-USD", {"2026-08-08": 999.0})
    prices.ensure_coverage(["BTC-USD"], "2026-08-07", "2026-08-08", verbose=False)
    assert calls == [("BTC-USD", "2026-08-08", "2026-08-08")] * 2
    assert prices.get_price("BTC-USD", "2026-08-08") == 112.0


@pytest.mark.parametrize("symbol", ["AAPL", "VFIAX"])
@pytest.mark.parametrize("end", ["2026-08-08", "2026-08-09"])
def test_settled_equities_and_funds_remain_idle_over_the_weekend(
        calendar_quotes, monkeypatch, symbol, end):
    from src import prices

    _, calls = calendar_quotes
    _pin(monkeypatch, end + "T16:00:00")
    _seed_settled(symbol)
    prices.ensure_coverage([symbol], "2026-08-07", end, verbose=False)
    assert calls == []
    assert prices.get_price(symbol, end) == 100.0
    assert prices.any_provisional([symbol]) is False


@pytest.mark.parametrize("symbol,live,settled", [
    ("AAPL", "2026-08-07T20:19:00", "2026-08-07T20:20:00"),
    ("VFIAX", "2026-08-07T21:59:00", "2026-08-07T22:00:00"),
])
def test_weekday_cutoffs_still_replace_a_live_mark_once(
        calendar_quotes, monkeypatch, symbol, live, settled):
    from src import prices

    quotes, calls = calendar_quotes
    _seed_settled(symbol, "2026-08-06")
    _pin(monkeypatch, live)
    quotes.set(symbol, {"2026-08-07": 105.0})
    prices.ensure_coverage([symbol], "2026-08-06", "2026-08-07", verbose=False)
    assert prices.any_provisional([symbol]) is True

    _pin(monkeypatch, settled)
    quotes.set(symbol, {"2026-08-07": 107.0})
    prices.ensure_coverage([symbol], "2026-08-06", "2026-08-07", verbose=False)
    assert prices.get_price(symbol, "2026-08-07") == 107.0
    assert prices.any_provisional([symbol]) is False

    prices.ensure_coverage([symbol], "2026-08-06", "2026-08-07", verbose=False)
    assert calls == [(symbol, "2026-08-07", "2026-08-07")] * 2


def test_forced_crypto_refresh_uses_its_calendar_not_friday(
        calendar_quotes, monkeypatch):
    from src import prices

    quotes, calls = calendar_quotes
    _pin(monkeypatch, "2026-08-09T00:01:00")
    _seed_settled("BTC-USD", "2026-08-08")
    quotes.set("BTC-USD", {"2026-08-07": 99.0, "2026-08-08": 115.0})

    prices.ensure_coverage(["BTC-USD"], "2026-08-08", "2026-08-08",
                           verbose=False, force_today_for={"BTC-USD"})

    assert calls == [("BTC-USD", "2026-08-08", "2026-08-08")]
    assert prices.get_price("BTC-USD", "2026-08-08") == 115.0


def test_mixed_batch_keeps_equities_idle_and_updates_both_crypto_symbols(
        calendar_quotes, monkeypatch):
    from src import prices

    quotes, calls = calendar_quotes
    _pin(monkeypatch, "2026-08-09T16:00:00")
    for symbol in ["AAPL", "BTC-USD", "ETH-USD"]:
        _seed_settled(symbol)
        quotes.set(symbol, {"2026-08-08": 110.0, "2026-08-09": 120.0})

    prices.ensure_coverage(["AAPL", "BTC-USD", "ETH-USD"],
                           "2026-08-07", "2026-08-09", verbose=False)

    assert calls == [(symbol, "2026-08-08", "2026-08-09")
                     for symbol in ["BTC-USD", "ETH-USD"]]
    assert prices.get_price("AAPL", "2026-08-09") == 100.0


def test_weekend_crypto_respects_closed_position_end_even_when_forced(
        calendar_quotes, monkeypatch):
    from src import prices

    quotes, calls = calendar_quotes
    _pin(monkeypatch, "2026-08-09T16:00:00")
    _seed_settled("BTC-USD")
    quotes.set("BTC-USD", {"2026-08-08": 110.0, "2026-08-09": 120.0})

    prices.ensure_coverage(["BTC-USD"], "2026-08-07", "2026-08-09", verbose=False,
                           symbol_end_overrides={"BTC-USD": "2026-08-08"},
                           force_today_for={"BTC-USD"})

    assert calls == [("BTC-USD", "2026-08-08", "2026-08-08")]
    assert prices._load_meta()["symbols"]["BTC-USD"]["covered_end"] == "2026-08-08"


def test_proxy_uses_target_crypto_calendar_for_forced_refresh(
        calendar_quotes, monkeypatch):
    from src import prices

    quotes, calls = calendar_quotes
    _pin(monkeypatch, "2026-08-09T00:01:00")
    alias = "Fictional Crypto Reference"
    prices._load_proxy_map()[alias] = {"proxy": "BTC-USD", "method": "direct"}
    _seed_settled("BTC-USD", "2026-08-08")
    quotes.set("BTC-USD", {"2026-08-08": 115.0})

    prices.ensure_coverage([alias], "2026-08-08", "2026-08-08", verbose=False,
                           force_today_for={alias})

    assert calls == [("BTC-USD", "2026-08-08", "2026-08-08")]
    assert prices.get_price(alias, "2026-08-08") == 115.0


def test_legacy_crypto_metadata_does_not_freeze_a_weekend_live_mark(
        calendar_quotes, monkeypatch):
    from src import prices

    quotes, calls = calendar_quotes
    _pin(monkeypatch, "2026-08-08T16:00:00")
    _seed_settled("BTC-USD", "2026-08-08")
    prices._load_meta()["symbols"]["BTC-USD"].pop("settled_through")
    quotes.set("BTC-USD", {"2026-08-08": 115.0})

    prices.ensure_coverage(["BTC-USD"], "2026-08-08", "2026-08-08", verbose=False)

    assert calls == [("BTC-USD", "2026-08-08", "2026-08-08")]
    assert prices.get_price("BTC-USD", "2026-08-08") == 115.0
    assert prices.any_provisional(["BTC-USD"]) is True
