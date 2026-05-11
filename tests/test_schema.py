"""Schema shape tests — validate that the TypedDict field set matches
the shape parsers and annotators actually produce.  Not a type-checker
replacement, just a runtime sanity check that catches field-name
drift (typos, renames, etc.).
"""

from __future__ import annotations

from typing import get_type_hints


def test_transaction_has_core_fields():
    """Every parser writes these — they're the core 10-field schema
    described in the module docstring."""
    from src.schema import Transaction
    hints = get_type_hints(Transaction)
    required = {
        "date", "account", "symbol", "action",
        "quantity", "price", "fees", "amount",
        "description", "source",
    }
    missing = required - set(hints)
    assert not missing, f"Transaction TypedDict missing core fields: {missing}"


def test_transaction_has_enrichment_fields():
    """Fields added by later pipeline stages."""
    from src.schema import Transaction
    hints = get_type_hints(Transaction)
    expected = {
        "cusip", "account_group", "account_type",
        "cost_basis", "realized_gain", "holding_days", "basis_effect",
    }
    missing = expected - set(hints)
    assert not missing, f"Transaction TypedDict missing enrichment fields: {missing}"


def test_parser_output_conforms_to_core_schema(isolated_workdir):
    """Every Robinhood parser output dict has all 10 core keys."""
    from src.parsers import parse_robinhood
    from tests.conftest import write_robinhood_csv

    csv = isolated_workdir / "data" / "robinhood-1.csv"
    write_robinhood_csv(csv, [
        {"Activity Date": "1/1/2024", "Trans Code": "Buy",
         "Instrument": "AAPL", "Description": "Apple",
         "Quantity": "1", "Price": "$150.00", "Amount": "($150.00)"},
    ])
    txns = parse_robinhood(csv)
    assert txns
    core = {"date", "account", "symbol", "action", "quantity",
            "price", "fees", "amount", "description", "source"}
    for t in txns:
        missing = core - set(t)
        assert not missing, f"parser output missing fields: {missing}"


def test_holding_typed_dict_fields():
    from src.schema import Holding
    hints = get_type_hints(Holding)
    core = {"account_group", "symbol", "quantity", "price", "value",
            "cost_basis", "unrealized_gain"}
    assert core <= set(hints)


def test_snapshot_typed_dict_fields():
    from src.schema import Snapshot
    hints = get_type_hints(Snapshot)
    core = {"date", "total", "by_account_group", "by_account_type",
            "by_sector", "total_cost_basis", "priced_pct",
            "benchmark_spy", "net_contributed", "positions"}
    assert core <= set(hints)
