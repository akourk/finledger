"""Unit prices stay precise through holdings and downstream analytics.

All quantities and prices below are independently constructed fixtures.
Dollar outputs round to cents; a unit price must remain a calculation
input because multiplying a rounded price can change a position's P&L.
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "quantity, current, previous, basis, expected_value, expected_move",
    [
        (10_000.0, 0.004, 0.003, 20.0, 40.0, 10.0),
        (10_000_000.0, 0.000004, 0.000003, 20.0, 40.0, 10.0),
        (10_000.0, 1.00004, 1.0, 5_000.0, 10_000.4, 0.4),
    ],
)
def test_holdings_price_survives_lots_and_window_pnl(
    isolated_workdir, quantity, current, previous, basis,
    expected_value, expected_move,
):
    from src.analytics.lots import compute_open_lots
    from src.analytics.position_pnl import compute_position_pnl
    from src.pipeline_stages import build_holdings
    from src.prices import _load_prices

    symbol = "DEMO-USD"
    key = ("Coinbase", symbol)
    state = {"lots": {key: [
        {"date": "2020-01-01", "qty": quantity,
         "basis_per_share": basis / quantity},
    ]}}
    _load_prices()[symbol] = {"2024-06-13": previous, "2024-06-14": current}
    holdings, by_account = build_holdings(
        {key: quantity}, {symbol: current}, {key: basis}, {},
    )

    for row in (holdings[0], by_account[0]):
        assert row["price"] == current
        assert row["value"] == pytest.approx(expected_value)

    lot = compute_open_lots(state, by_account)["positions"][0]["lots"][0]
    assert lot["price"] == current
    assert lot["value"] == pytest.approx(expected_value)
    assert lot["unrealized_gain"] == pytest.approx(expected_value - basis)

    pnl = compute_position_pnl(state, by_account, [], as_of="2024-06-14")
    for row in (pnl["by_account"][0], pnl["by_symbol"][0]):
        assert row["windows"]["1d"]["pnl"] == pytest.approx(expected_move)


def test_history_preserves_a_sub_cent_position_price(isolated_workdir, monkeypatch):
    from src import history
    from src.prices import _load_prices

    symbol = "DEMO-USD"
    _load_prices()[symbol] = {"2024-06-14": 0.000004}
    monkeypatch.setattr(history, "_sample_dates", lambda *args: ["2024-06-14"])
    txns = [{
        "date": "2024-06-14", "account_group": "Example Broker",
        "account_type": "Taxable", "symbol": symbol, "action": "Buy",
        "quantity": 10_000_000.0, "price": 0.000002, "amount": 20.0,
    }]

    result = history.compute_history(txns, {symbol: "Cryptocurrency"})
    row = result[0]["positions"][0]
    assert row["price"] == 0.000004
    assert row["value"] == 40.0
    assert row["quantity"] * row["price"] == pytest.approx(row["value"])
