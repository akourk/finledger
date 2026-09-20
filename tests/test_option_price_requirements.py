"""Quote coverage follows all direct and option consumers of each ticker.

Contracts, quantities, dates, and prices below are independently fictional.
No market-data requests or account walks are needed to plan these windows.
"""

from __future__ import annotations

import pytest

from src.pipeline_stages import (
    compute_position_endings, extend_option_price_requirements,
)


CALL = "FICT 12/20/2024 Call $20.00"
PUT = "FICT 12/20/2024 Put $25.00"


def _txn(date, symbol, action, quantity=1.0):
    return {"date": date, "symbol": symbol, "action": action,
            "quantity": quantity, "price": 2.0}


def test_closed_option_only_fetches_underlying_through_its_disposal():
    txns = [
        _txn("2024-01-02", CALL, "Option Buy"),
        _txn("2024-03-18", CALL, "Option Sell"),
    ]
    endings, trivial = compute_position_endings(txns)
    assert CALL not in trivial
    assert endings[CALL] == "2024-03-18"

    symbols, adjusted = extend_option_price_requirements({CALL}, endings)
    assert symbols == {CALL, "FICT"}
    assert adjusted == {CALL: "2024-03-18", "FICT": "2024-03-18"}


def test_open_option_overrides_earlier_underlying_disposal():
    txns = [
        _txn("2024-01-02", "FICT", "Buy"),
        _txn("2024-02-02", "FICT", "Sell"),
        _txn("2024-03-01", CALL, "Option Buy"),
    ]
    endings, _ = compute_position_endings(txns)
    assert endings == {"FICT": "2024-02-02"}

    _, adjusted = extend_option_price_requirements({"FICT", CALL}, endings)
    assert "FICT" not in adjusted


def test_open_underlying_keeps_current_quotes_after_option_closes():
    symbols, adjusted = extend_option_price_requirements(
        {"FICT", CALL}, {CALL: "2024-03-18"})
    assert symbols == {"FICT", CALL}
    assert "FICT" not in adjusted
    assert adjusted[CALL] == "2024-03-18"


@pytest.mark.parametrize("security_end,option_end,expected", [
    ("2024-02-02", "2024-03-18", "2024-03-18"),
    ("2024-04-02", "2024-03-18", "2024-04-02"),
])
def test_closed_direct_and_option_consumers_take_latest_end(
        security_end, option_end, expected):
    _, adjusted = extend_option_price_requirements(
        {"FICT", CALL}, {"FICT": security_end, CALL: option_end})
    assert adjusted["FICT"] == expected


def test_multiple_closed_contracts_share_one_underlying_window():
    symbols, adjusted = extend_option_price_requirements(
        [CALL, PUT], {CALL: "2024-03-18", PUT: "2024-06-21"})
    assert symbols == {CALL, PUT, "FICT"}
    assert adjusted["FICT"] == "2024-06-21"


@pytest.mark.parametrize("open_contract", [None, "SPXW", "XSP"])
def test_index_contract_roots_aggregate_at_mapped_quote_target(open_contract):
    contracts = {
        "SPXW": "SPXW 12/20/2024 Call $5000.00",
        "XSP": "XSP 12/20/2024 Put $500.00",
    }
    endings = {contracts["SPXW"]: "2024-03-18", contracts["XSP"]: "2024-06-21"}
    if open_contract is not None:
        endings.pop(contracts[open_contract])
    symbols, adjusted = extend_option_price_requirements(contracts.values(), endings)
    assert symbols == {*contracts.values(), "^GSPC"}
    assert "SPXW" not in symbols and "XSP" not in symbols
    if open_contract is None:
        assert adjusted["^GSPC"] == "2024-06-21"
    else:
        assert "^GSPC" not in adjusted


def test_benchmark_demand_keeps_option_target_current():
    contract = "SPY 12/20/2024 Call $500.00"
    # The caller inserts benchmarks and clears their disposal overrides first.
    _, adjusted = extend_option_price_requirements(
        {"SPY", contract}, {contract: "2024-03-18"})
    assert "SPY" not in adjusted


def test_literal_security_symbols_do_not_use_option_root_aliases():
    symbols, adjusted = extend_option_price_requirements(
        {"SPXW"}, {"SPXW": "2024-03-18"})
    assert symbols == {"SPXW"}
    assert adjusted == {"SPXW": "2024-03-18"}


def test_end_map_alone_does_not_create_a_direct_security_requirement():
    # A trivial direct position may have been removed from the requested set.
    _, adjusted = extend_option_price_requirements(
        {CALL}, {CALL: "2024-03-18", "FICT": "2024-06-21"})
    assert adjusted["FICT"] == "2024-03-18"


def test_open_option_removes_stale_target_clamp_without_changing_inputs():
    source_symbols = {CALL}
    source_endings = {"FICT": "2024-03-18", "UNRELATED": "2024-02-02"}
    symbols, adjusted = extend_option_price_requirements(
        iter(source_symbols), source_endings)
    assert symbols == {CALL, "FICT"}
    assert adjusted == {"UNRELATED": "2024-02-02"}
    assert source_symbols == {CALL}
    assert source_endings == {"FICT": "2024-03-18", "UNRELATED": "2024-02-02"}
