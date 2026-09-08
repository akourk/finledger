"""Fictional date and share-unit boundaries for repricing current positions."""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def quotes(isolated_workdir, monkeypatch):
    from src import valuation, prices
    from src.analytics import header

    monkeypatch.setattr(valuation, "get_price", lambda symbol, day: None)
    monkeypatch.setattr(valuation, "option_intrinsic", lambda symbol, day: None)
    monkeypatch.setattr(prices, "get_series", lambda symbol, start, end: {})
    monkeypatch.setattr(header, "any_provisional", lambda symbols: False)
    monkeypatch.setattr(header, "oldest_last_fetch", lambda symbols: None)
    return monkeypatch


def _row(day, price, *, symbol="PRISM", seq=0):
    return {"date": day, "symbol": symbol, "price": price, "seq": seq,
            "account_group": "Fictional Fund", "action": "Buy"}


def _history(*, symbol="PRISM", qty=10, value=1200):
    return [{"date": "2024-06-05", "total": value, "positions": [
        {"symbol": symbol, "quantity": qty, "value": value,
         "account_group": "Fictional Fund", "cost_basis": 1000}]}]


def _results(rows, history=None):
    from src.analytics.header import compute_header_summary
    from src.analytics.daily_pnl import compute_daily_pnl

    history = history or _history()
    return (compute_header_summary(rows, history, []),
            compute_daily_pnl(history, rows, window_days=4))


def test_current_transaction_price_does_not_erase_prior_daily_movement(quotes):
    header, daily = _results([_row("2024-06-01", 100), _row("2024-06-05", 120)])
    assert header["prev_day_value"] == 1000
    assert header["change_1d"] == 200
    assert header["unpriced_1d"] == 0
    assert [(p["date"], p["value"], p["change"]) for p in daily] == [
        ("2024-06-02", 1000, 0), ("2024-06-03", 1000, 0),
        ("2024-06-04", 1000, 0), ("2024-06-05", 1200, 200)]


def test_future_quotes_and_reversed_export_order_do_not_rewrite_pnl(quotes):
    rows = [_row("2024-06-01", 100), _row("2024-06-04", 110, seq=9),
            _row("2024-06-04", 130, seq=8), _row("2024-06-05", 120)]
    expected = _results(rows)
    assert expected[0]["prev_day_value"] == 1100
    assert expected[1][-2]["change"] == 100
    assert _results(list(reversed(rows)) + [_row("2024-06-06", 900)]) == expected


@pytest.mark.parametrize("price", [0, -100, float("inf"), float("nan")])
def test_invalid_quotes_do_not_replace_a_known_positive_price(quotes, price):
    rows = [_row("2024-06-01", 100), _row("2024-06-04", price),
            _row("2024-06-05", 120)]
    header, daily = _results(rows)
    assert header["prev_day_value"] == 1000
    assert daily[-1]["change"] == 200
    json.dumps([header, daily], allow_nan=False)


def test_no_earlier_price_is_reported_as_missing_coverage(quotes):
    header, daily = _results([_row("2024-06-05", 120)])
    assert header["unpriced_1d"] == 1200
    assert header["change_1d"] == 0
    assert daily == []


def test_changing_price_coverage_does_not_create_a_profit_bar(quotes):
    history = _history(value=1500)
    history[0]["positions"][0]["value"] = 1000
    history[0]["positions"].append({"symbol": "SECOND", "quantity": 10, "value": 500})
    _, daily = _results([_row("2024-06-01", 100),
                         _row("2024-06-03", 50, symbol="SECOND")], history)
    assert [(p["date"], p["value"], p["change"]) for p in daily] == [
        ("2024-06-04", 1500, 0), ("2024-06-05", 1500, 0)]


def test_missing_intermediate_mark_resets_the_daily_comparison(quotes):
    from src import valuation

    quotes.setattr(valuation, "get_price", lambda sym, day: None if day == "2024-06-03" else 100)
    _, daily = _results([], _history(value=1000))
    assert [(p["date"], p["change"]) for p in daily] == [
        ("2024-06-02", 0), ("2024-06-05", 0)]


def test_missing_current_mark_is_not_reported_as_a_total_loss(quotes):
    history = _history(value=0)
    history[0]["positions"][0]["value"] = None
    header, daily = _results([_row("2024-06-01", 100)], history)
    assert header["change_1d"] == 0
    assert header["change_1d_pct"] is None
    assert header["unpriced_1d"] == 1000
    assert daily == []


def test_unsupported_transit_units_do_not_produce_partial_market_returns(quotes):
    history = _history(value=1000)
    history[0]["in_transit"] = [{"symbol": "PRISM", "quantity": 5,
                                "start_date": "2024-06-01", "value": None,
                                "valuation_issue": "split_during_transfer"}]
    header, daily = _results([_row("2024-06-01", 100)], history)
    assert header["change_1d"] == 0
    assert daily == []


def test_pre_split_trade_price_matches_current_share_units(quotes, isolated_workdir):
    from src import prices

    (isolated_workdir / "cache" / "splits_cache.json").write_text(
        json.dumps({"PRISM": [["2024-06-03", 4.0]]}), encoding="utf-8")
    prices.reset_caches()
    header, daily = _results([_row("2024-06-01", 100), _row("2024-06-04", 25)],
                             _history(qty=40, value=1000))
    assert header["change_1d"] == 0
    assert all(p["value"] == 1000 and p["change"] == 0 for p in daily)


@pytest.mark.parametrize("cached", [False, True])
def test_later_split_preserves_a_historical_snapshot_reprice(quotes, isolated_workdir, cached):
    from src import prices, valuation

    history = _history(qty=10, value=1000)
    rows = [_row("2024-06-01", 100)]
    before = _results(rows, history)
    (isolated_workdir / "cache" / "splits_cache.json").write_text(
        json.dumps({"PRISM": [["2024-06-06", 4.0]]}), encoding="utf-8")
    prices.reset_caches()
    if cached:
        quotes.setattr(valuation, "get_price", lambda symbol, day: 25)
    after = _results(rows, history)
    assert after == before
    assert history[0]["positions"][0]["quantity"] == 10
    assert after[0]["change_1d"] == 0
    assert all(p["value"] == 1000 for p in after[1])


def test_option_multiplier_and_dated_intrinsic_floor_still_apply(quotes):
    from src import valuation

    symbol = "PRISM 12/20/2024 Call $100.00"
    quotes.setattr(valuation, "option_intrinsic", lambda sym, day: 3 if day >= "2024-06-04" else 1)
    header, daily = _results([_row("2024-06-01", 2, symbol=symbol),
                             _row("2024-06-06", 10, symbol=symbol)],
                             _history(symbol=symbol, qty=1, value=300))
    assert header["prev_day_value"] == 300
    assert [p["value"] for p in daily] == [200, 200, 300, 300]
    assert [p["change"] for p in daily] == [0, 0, 100, 0]
