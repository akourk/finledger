"""Dated fallback quotes retain value when posted transfer share units change."""
from __future__ import annotations

import math

import pytest


@pytest.fixture
def quote_market(isolated_workdir, monkeypatch):
    from src import history, prices, valuation
    from src.config import ACCOUNT_TYPES

    monkeypatch.setenv("FIN_AS_OF_DATE", "2024-03-06")
    ACCOUNT_TYPES.update({group: "Taxable" for group in
                          ("Fictional Source", "Fictional Arrival", "Fictional Quote")})
    market = {"ratio": 2.0}

    def factor(symbol, day):
        return market["ratio"] if symbol == "PRISM" and day < "2024-03-01" else 1.0

    def quote(symbol, day):
        if symbol == "PRISM":
            return None if day >= "2024-03-05" else 100 / market["ratio"]
        return 100.0

    monkeypatch.setattr(prices, "split_factor_since", factor)
    monkeypatch.setattr(valuation, "split_factor_since", factor)
    for module in (prices, valuation, history):
        monkeypatch.setattr(module, "get_price", quote)
    monkeypatch.setattr(valuation, "option_intrinsic", lambda *_: None)
    return market


def _row(group, action, quantity, day, price=0):
    return {"account": group, "account_group": group, "account_type": "Taxable",
            "symbol": "PRISM", "date": day, "action": action, "raw_action": action,
            "quantity": quantity, "price": price, "amount": quantity * price,
            "fees": 0, "description": "", "source": "fictional-quote-units"}


@pytest.mark.parametrize("ratio", [2.0, 0.5, 1.5])
@pytest.mark.parametrize("new_quote", [None, 60.0])
def test_arrival_cache_miss_uses_quote_date_units_in_every_valuation(
        quote_market, ratio, new_quote):
    from src import basis, history
    from src.analytics._shared import _value_at_date
    from src.return_flows import annotate_account_transfers, scope_snapshot_value

    quote_market["ratio"] = ratio
    rows = [_row("Fictional Source", "Contribution", 10, "2024-01-01", 100),
            _row("Fictional Source", "Transfer Out", 10, "2024-02-27"),
            _row("Fictional Arrival", "Transfer In", 10 * ratio, "2024-03-05")]
    arrival = rows[-1]
    if new_quote is not None:
        rows.append(_row("Fictional Quote", "Neutral", 0, "2024-03-05", new_quote))
    rows.append(_row("Fictional Quote", "Neutral", 0, "2024-03-06", 9000))
    for index, row in enumerate(rows):
        row["seq"] = index
    rows.reverse()
    basis.compute_basis_default(rows)
    annotate_account_transfers(rows)
    snapshots = history.compute_history(rows, {"PRISM": "Other"}, cadence="day")
    before = next(h for h in snapshots if h["date"] == "2024-02-26")
    snapshot = next(h for h in snapshots if h["date"] == "2024-03-05")
    expected = 1000 if new_quote is None else 10 * ratio * new_quote
    assert scope_snapshot_value(before) == 1000
    assert scope_snapshot_value(snapshot) == expected
    assert dict(history.compute_daily_totals(rows))["2024-03-05"] == expected
    ordered = sorted(rows, key=basis._sort_key)
    assert _value_at_date(ordered, "2024-03-05", None, []) == pytest.approx(expected)
    assert _value_at_date(ordered, "2024-03-05", {"Fictional Arrival"}, []) == pytest.approx(expected)
    assert arrival["account_transfer"]["flow"] == pytest.approx(expected)


def test_quote_conversion_does_not_mutate_or_repeatedly_scale_originals(quote_market):
    from src.valuation import rebase_transaction_prices

    raw = {"PRISM": 100.0}
    dates = {"PRISM": "2024-01-01"}
    assert rebase_transaction_prices(raw, dates, "2024-02-01") == {"PRISM": 100.0}
    assert rebase_transaction_prices(raw, dates, "2024-03-01") == {"PRISM": 50.0}
    assert rebase_transaction_prices(raw, dates, "2024-03-05") == {"PRISM": 50.0}
    assert raw == {"PRISM": 100.0}
    assert dates == {"PRISM": "2024-01-01"}


@pytest.mark.parametrize("price,quoted", [(100, None), (100, ""),
                                         (100, "2024-03-07"), (math.inf, "2024-01-01"),
                                         (math.nan, "2024-01-01"), (-1, "2024-01-01")])
def test_unknown_future_and_invalid_quotes_are_not_fallbacks(quote_market, price, quoted):
    from src.valuation import rebase_transaction_prices

    assert rebase_transaction_prices({"PRISM": price}, {"PRISM": quoted}, "2024-03-05") == {}


def test_option_fallback_keeps_single_contract_multiplier(quote_market, monkeypatch):
    from src import valuation

    symbol = "PRISM 12/20/2024 Call $50"
    monkeypatch.setattr(valuation, "get_price", lambda *_: None)
    fallback = valuation.rebase_transaction_prices(
        {symbol: 3.0}, {symbol: "2024-01-01"}, "2024-03-05")
    assert valuation.mark(symbol, 2, "2024-03-05", fallback).value == 600.0
