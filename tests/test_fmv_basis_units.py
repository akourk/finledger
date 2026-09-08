"""FMV-created lots use the same contract units as position valuation."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("symbol, expected", [
    ("FICTION 12/20/2024 Call $100.00", 200.0),
    ("FICTION 12/20/2024 Put $100.00", 200.0),
    ("FICTION", 2.0),
    ("CALL", 2.0),
])
def test_one_unit_fmv_matches_normalized_symbol_units(
        isolated_workdir, symbol, expected):
    from src.basis import fmv_basis

    assert fmv_basis({"symbol": symbol, "price": 2.0}, 1.0) == expected


@pytest.mark.parametrize("override", [0.0, 150.0])
@pytest.mark.parametrize("price", [None, -2.0, 0.0, 2.0])
def test_option_fmv_override_is_already_a_dollar_total(
        isolated_workdir, override, price):
    from src.basis import fmv_basis

    txn = {"symbol": "FICTION 12/20/2024 Call $100.00",
           "price": price, "basis_override": override}
    assert fmv_basis(txn, 1.0) == override


@pytest.mark.parametrize("amount", [None, 0.0])
@pytest.mark.parametrize("symbol, unit_basis, sale_proceeds", [
    ("FICTION 12/20/2024 Call $100.00", 200.0, 250.0),
    ("FICTION 12/20/2024 Put $100.00", 200.0, 250.0),
    ("FICTION", 2.0, 2.5),
])
def test_price_only_trade_fallback_preserves_basis_and_sale_units(
        stub_prices, monkeypatch, amount, symbol, unit_basis, sale_proceeds):
    from src import basis, config, history

    monkeypatch.setenv("FIN_AS_OF_DATE", "2024-01-31")
    monkeypatch.setitem(config.ACCOUNT_TYPES, "Fictional Taxable", "Taxable")
    common = {"account": "Fictional Taxable",
              "account_group": "Fictional Taxable",
              "account_type": "Taxable", "symbol": symbol,
              "fees": 0.0, "source": "fictional.csv", "description": ""}
    txns = [
        {**common, "date": "2024-01-01", "action": "Buy",
         "quantity": 2.0, "price": 2.0},
        {**common, "date": "2024-01-20", "action": "Sell",
         "quantity": 1.0, "price": 2.5},
    ]
    if amount is not None:
        for txn in txns:
            txn["amount"] = amount
    state = basis.compute_basis_default(txns)
    snapshots = history.compute_history(txns, {symbol: "Other"})

    assert txns[0]["cost_basis"] == 2 * unit_basis
    assert txns[1]["cost_basis"] == unit_basis
    assert txns[1]["realized_gain"] == sale_proceeds - unit_basis
    assert state["realized_total"] == sale_proceeds - unit_basis
    assert snapshots[0]["positions"][0]["cost_basis"] == 2 * unit_basis
    assert snapshots[-1]["positions"][0]["cost_basis"] == unit_basis
    assert basis.state_to_holdings(state, "fifo")[0]["cost_basis"] == unit_basis


@pytest.mark.parametrize("symbol", [
    "FICTION 12/20/2024 Call $100.00", "FICTION",
])
def test_reported_trade_amount_already_includes_its_dollar_units_and_fees(
        isolated_workdir, symbol):
    from src.basis import _basis_dollars

    assert _basis_dollars({"symbol": symbol, "quantity": 2.0,
                           "price": 2.0, "amount": 403.25,
                           "fees": 3.25}) == 403.25


@pytest.mark.parametrize("arrival_action", [
    "Transfer In", "Wrap Asset In", "Reward",
])
@pytest.mark.parametrize("symbol, unit_basis, sale_amount", [
    ("FICTION 12/20/2024 Call $100.00", 200.0, 250.0),
    ("FICTION 12/20/2024 Put $100.00", 200.0, 250.0),
    ("FICTION", 2.0, 2.5),
])
def test_fmv_receipt_preserves_history_basis_and_later_sale_gain(
        stub_prices, monkeypatch, arrival_action, symbol, unit_basis,
        sale_amount):
    from src import basis, config, history

    monkeypatch.setenv("FIN_AS_OF_DATE", "2024-01-31")
    monkeypatch.setitem(config.ACCOUNT_TYPES, "Fictional Taxable", "Taxable")
    common = {"account": "Fictional Taxable",
              "account_group": "Fictional Taxable",
              "account_type": "Taxable", "symbol": symbol,
              "fees": 0.0, "source": "fictional.csv", "description": ""}
    txns = [
        {**common, "date": "2024-01-01", "action": arrival_action,
         "quantity": 2.0, "price": 2.0, "amount": 0.0},
        {**common, "date": "2024-01-20", "action": "Sell",
         "quantity": 1.0, "price": 2.5, "amount": sale_amount},
    ]
    state = basis.compute_basis_default(txns)
    snapshots = history.compute_history(txns, {symbol: "Other"})

    assert txns[0]["cost_basis"] == 2 * unit_basis
    assert txns[1]["cost_basis"] == unit_basis
    assert txns[1]["realized_gain"] == sale_amount - unit_basis
    assert state["realized_total"] == sale_amount - unit_basis
    assert snapshots[0]["date"] == "2024-01-15"
    assert snapshots[0]["positions"][0]["cost_basis"] == 2 * unit_basis
    final = basis.state_to_holdings(state, "fifo")[0]
    assert final["cost_basis"] == unit_basis
    assert snapshots[-1]["positions"][0]["cost_basis"] == unit_basis
    assert snapshots[-1]["positions"][0]["quantity"] == 1.0

    # Every what-if method must use the same receipt units; no disposal
    # order choice can change the basis of these identically priced lots.
    for method in ("fifo", "lifo", "hifo", "avg"):
        other = basis._walk(txns, method, annotate=False)
        assert other["realized_total"] == sale_amount - unit_basis
        assert basis.state_to_holdings(other, method)[0]["cost_basis"] == unit_basis
