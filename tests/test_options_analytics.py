"""Options outcomes use independently fictional account/contract activity."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest


CALL = "FICTION 12/19/2025 Call $100.00"
PUT = "FICTION 12/19/2025 Put $100.00"


def _txn(day, action, qty, price, *, account="Fictional A", symbol=CALL, **extra):
    return {"date": day, "action": action, "quantity": qty, "price": price,
            "amount": qty * price * 100, "account": account,
            "account_group": account, "symbol": symbol, **extra}


@pytest.fixture
def analyze(isolated_workdir, monkeypatch):
    from src.analytics.options import compute_options_analytics
    from src.basis import compute_basis_default

    monkeypatch.setenv("FIN_AS_OF_DATE", "2025-06-30")

    def run(rows, *, annotate=True, **kwargs):
        rows = deepcopy(rows)
        if annotate:
            compute_basis_default(rows, **kwargs)
        return compute_options_analytics(rows)
    return run


def test_position_and_contract_results_do_not_depend_on_close_batching(analyze):
    entry = _txn("2025-01-02", "Option Buy", 4, 2)
    batched = analyze([
        entry, _txn("2025-01-10", "Option Sell", 3, 3),
        _txn("2025-01-15", "Option Sell", 1, 1),
    ])
    split = analyze([
        entry, *[_txn(f"2025-01-{day:02}", "Option Sell", 1, 3) for day in (8, 9, 10)],
        _txn("2025-01-15", "Option Sell", 1, 1),
    ])
    for result in (batched, split):
        assert result["stats"]["trades"] == 1
        assert result["stats"]["win_rate"] == 1
        assert result["stats"]["expectancy"] == 200
        assert result["stats"]["avg_winner"] == 200
        assert result["stats"]["completed_pnl"] == 200
        assert result["stats"]["realized_pnl"] == 200
        assert result["contract_stats"]["win_rate"] == .75
        assert result["contract_stats"]["coverage_complete"] is True
        assert result["completed_positions"][0]["qty"] == 4
        assert result["completed_positions"][0]["hold_days"] == 13
    assert batched["stats"]["closed_count"] == 2
    assert split["stats"]["closed_count"] == 4


def test_net_breakeven_close_can_contain_both_winning_and_losing_contracts(analyze):
    result = analyze([
        _txn("2025-01-02", "Option Buy", 1, 1),
        _txn("2025-01-03", "Option Buy", 1, 3),
        _txn("2025-01-10", "Option Sell", 2, 2),
    ])
    assert result["stats"]["breakevens"] == 1
    assert result["stats"]["win_rate"] is None
    assert result["contract_stats"] == {
        "wins": 1, "losses": 1, "breakevens": 0, "unknown": 0,
        "contracts": 2, "win_rate": .5, "coverage_complete": True,
    }
    assert [r["outcome"] for r in result["closed_trades"][0]["lot_outcomes"]] == ["win", "loss"]


def test_scaled_entries_partial_exits_and_current_remaining_basis(analyze):
    rows = [
        _txn("2025-01-02", "Option Buy", 2, 2),
        _txn("2025-01-04", "Option Sell", 1, 3),
        _txn("2025-01-06", "Option Buy", 1, 4),
    ]
    partial = analyze(rows)
    assert partial["stats"]["trades"] == 0
    assert partial["stats"]["realized_pnl"] == 100
    assert partial["open_contracts"][0]["entry_price"] == 3
    assert partial["open_contracts"][0]["basis"] == 600
    assert partial["open_contracts"][0]["qty"] == 2
    final = analyze(rows + [_txn("2025-01-12", "Option Sell", 2, 2)])
    assert final["stats"]["trades"] == 1
    assert final["stats"]["losses"] == 1
    assert final["stats"]["completed_pnl"] == -100
    assert final["stats"]["avg_hold_days"] == 10
    assert final["completed_positions"][0]["basis"] == 800
    assert final["completed_positions"][0]["proceeds"] == 700
    assert all(row["completed_position"] for row in final["closed_trades"])


def test_reopening_starts_a_new_episode_and_open_date(analyze):
    result = analyze([
        _txn("2025-01-02", "Option Buy", 1, 2),
        _txn("2025-01-03", "Option Sell", 1, 3),
        _txn("2025-02-02", "Option Buy", 2, 4),
    ])
    assert result["stats"]["trades"] == 1
    assert result["open_contracts"][0]["open_date"] == "2025-02-02"
    assert result["open_contracts"][0]["entry_price"] == 4
    assert result["stats"]["open_count"] == 1
    assert result["stats"]["open_contract_count"] == 2


def test_identical_contracts_never_mix_between_accounts(analyze):
    result = analyze([
        _txn("2025-01-02", "Option Buy", 1, 1, account="Fictional A"),
        _txn("2025-01-04", "Option Buy", 2, 3, account="Fictional B"),
        _txn("2025-01-08", "Option Sell", 1, 2, account="Fictional A"),
        _txn("2025-02-01", "Option Buy", 1, 4, account="Fictional A"),
    ])
    assert result["completed_positions"][0]["account_group"] == "Fictional A"
    positions = {r["account_group"]: r for r in result["open_contracts"]}
    assert positions["Fictional A"]["entry_price"] == 4
    assert positions["Fictional A"]["open_date"] == "2025-02-01"
    assert positions["Fictional B"]["entry_price"] == 3
    assert positions["Fictional B"]["open_date"] == "2025-01-04"
    assert result["stats"]["open_count"] == 2
    assert result["stats"]["open_contract_count"] == 3


@pytest.mark.parametrize("exit_price, expected", [
    (0, {"losses": 1, "wins": 0, "breakevens": 0, "win_rate": 0, "profit_factor": 0}),
    (2, {"losses": 0, "wins": 0, "breakevens": 1, "win_rate": None, "profit_factor": None}),
    (4, {"losses": 0, "wins": 1, "breakevens": 0, "win_rate": 1, "profit_factor": None}),
])
def test_all_win_loss_and_breakeven_denominators_are_consistent(analyze, exit_price, expected):
    result = analyze([
        _txn("2025-01-02", "Option Buy", 1, 2, symbol=PUT),
        _txn("2025-01-04", "Option Expire" if exit_price == 0 else "Option Sell", 1, exit_price, symbol=PUT),
    ])
    assert {key: result["stats"][key] for key in expected} == expected
    assert result["stats"]["put"]["win_rate"] == expected["win_rate"]
    assert result["by_underlying"][0]["win_rate"] == expected["win_rate"]
    assert result["annual_summary"][0]["win_rate"] == expected["win_rate"]
    assert result["stats"]["call"]["win_rate"] is None
    json.dumps(result, allow_nan=False)


def test_empty_statistics_are_unavailable_ratios_and_real_zero_totals(analyze):
    result = analyze([])
    assert result["stats"]["realized_pnl"] == 0
    assert result["stats"]["trades"] == 0
    assert result["stats"]["win_rate"] is None
    assert result["stats"]["expectancy"] is None
    assert result["contract_stats"]["win_rate"] is None
    assert result["contract_stats"]["unknown"] == 0
    assert result["contract_stats"]["coverage_complete"] is True
    json.dumps(result, allow_nan=False)


def test_completion_year_and_execution_year_pnl_are_distinct(analyze):
    result = analyze([
        _txn("2024-12-20", "Option Buy", 2, 2),
        _txn("2024-12-31", "Option Sell", 1, 3),
        _txn("2025-01-02", "Option Sell", 1, 1),
    ])
    years = {r["year"]: r for r in result["annual_summary"]}
    assert years["2024"]["trades"] == 0
    assert years["2024"]["realized"] == 100
    assert years["2024"]["completed_pnl"] == 0
    assert years["2025"]["trades"] == 1
    assert years["2025"]["realized"] == -100
    assert years["2025"]["completed_pnl"] == 0
    assert result["cumulative_pnl"] == [
        {"date": "2024-12-31", "value": 100}, {"date": "2025-01-02", "value": 0},
    ]


def test_same_day_activity_follows_ledger_order_and_chart_collapses_dates(analyze):
    rows = [
        _txn("2025-01-02", "Option Buy", 1, 2, seq=1),
        _txn("2025-01-03", "Option Sell", 1, 3, seq=2),
        _txn("2025-01-03", "Option Buy", 1, 2, seq=3),
        _txn("2025-01-03", "Option Sell", 1, 1, seq=4),
    ]
    result = analyze(list(reversed(rows)))
    assert result["stats"]["trades"] == 1
    assert result["stats"]["breakevens"] == 1
    assert result["completed_positions"][0]["close_count"] == 2
    assert result["cumulative_pnl"] == [{"date": "2025-01-03", "value": 0}]


def test_as_of_ignores_future_activity_and_preserves_current_episode(analyze):
    result = analyze([
        _txn("2025-06-02", "Option Buy", 2, 2),
        _txn("2025-07-02", "Option Sell", 2, 4),
        _txn("2025-08-02", "Option Buy", 3, 1, account="Fictional B"),
    ])
    assert result["closed_trades"] == []
    assert result["completed_positions"] == []
    assert len(result["open_contracts"]) == 1
    assert result["open_contracts"][0]["qty"] == 2
    assert result["open_contracts"][0]["basis"] == 400
    assert result["open_contracts"][0]["dte"] == 172


def test_unmatched_close_retains_realized_but_never_invents_a_win(analyze):
    result = analyze([_txn("2025-01-02", "Option Sell", 2, 2)])
    assert result["stats"]["realized_pnl"] == 400
    assert result["stats"]["trades"] == 0
    assert result["contract_stats"]["unknown"] == 2
    assert result["contract_stats"]["win_rate"] is None
    assert result["contract_stats"]["coverage_complete"] is False


def test_transferred_episode_excluded_while_matched_contract_pnl_survives(analyze):
    result = analyze([
        _txn("2025-01-02", "Option Buy", 1, 2),
        _txn("2025-01-04", "Transfer Out", 1, 2),
        _txn("2025-01-04", "Transfer In", 1, 2, account="Fictional B"),
        _txn("2025-01-08", "Option Sell", 1, 3, account="Fictional B"),
    ])
    assert result["stats"]["trades"] == 0
    assert result["stats"]["realized_pnl"] == 100
    assert result["stats"]["open_count"] == 0
    assert result["contract_stats"]["wins"] == 1
    assert result["contract_stats"]["coverage_complete"] is True


@pytest.mark.parametrize("damage", ["missing", "insufficient", "excess", "basis", "proceeds"])
def test_incomplete_or_unreconciled_lot_annotations_do_not_guess_contract_signs(analyze, damage):
    from src.basis import compute_basis_default
    rows = [_txn("2025-01-02", "Option Buy", 2, 2),
            _txn("2025-01-04", "Option Sell", 2, 3)]
    compute_basis_default(rows)
    if damage == "missing":
        rows[1].pop("lot_breakdown")
    else:
        field = {"insufficient": "qty", "excess": "qty", "basis": "cost_basis", "proceeds": "proceeds"}[damage]
        rows[1]["lot_breakdown"][0][field] += -1 if damage == "insufficient" else 1
    result = analyze(rows, annotate=False)
    assert result["contract_stats"]["unknown"] == 2
    assert result["contract_stats"]["wins"] == 0
    assert result["stats"]["realized_pnl"] == 200
    if damage != "missing":
        assert result["completed_positions"] == []


def test_partial_lot_vector_does_not_allocate_all_proceeds_to_known_contract(analyze):
    result = analyze([
        _txn("2025-01-02", "Option Buy", 1, 3),
        _txn("2025-01-04", "Option Sell", 2, 2),
    ])
    assert result["stats"]["realized_pnl"] == 100
    assert result["stats"]["trades"] == 0
    assert result["contract_stats"]["unknown"] == 2
    assert result["contract_stats"]["wins"] == 0


def test_annotation_basis_and_price_fallback_respect_option_multiplier(analyze):
    result = analyze([
        _txn("2025-01-02", "Buy", 2, 2, amount=0, basis_override=300),
        _txn("2025-01-04", "Sell", 1, 2.5, amount=0),
    ])
    close = result["closed_trades"][0]
    assert close["proceeds"] == 250
    assert close["basis"] == 150
    assert close["realized"] == 100
    assert result["open_contracts"][0]["entry_price"] == 1.5
    assert result["open_contracts"][0]["basis"] == 150


def test_non_fifo_lot_annotations_control_contract_signs_and_remaining_basis(analyze):
    result = analyze([
        _txn("2025-01-02", "Option Buy", 1, 1),
        _txn("2025-01-03", "Option Buy", 1, 3),
        _txn("2025-01-04", "Option Sell", 1, 2),
    ], account_methods={"Fictional A": "hifo"})
    assert result["contract_stats"]["losses"] == 1
    assert result["closed_trades"][0]["open_date"] == "2025-01-03"
    assert result["open_contracts"][0]["entry_price"] == 1


def test_execution_holding_period_weights_matched_lots_and_requires_all_dates(analyze):
    from src.basis import compute_basis_default
    rows = [
        _txn("2025-01-02", "Option Buy", 2, 2),
        _txn("2025-01-05", "Option Buy", 1, 3),
        _txn("2025-01-10", "Option Sell", 3, 4),
    ]
    compute_basis_default(rows)
    result = analyze(rows, annotate=False)
    assert result["closed_trades"][0]["hold_days"] == 7
    assert result["completed_positions"][0]["hold_days"] == 8
    rows[-1]["lot_breakdown"][0]["days"] = None
    result = analyze(rows, annotate=False)
    assert result["closed_trades"][0]["hold_days"] is None
    assert result["completed_positions"][0]["hold_days"] == 8


def test_unmatched_negative_inventory_does_not_invent_remaining_basis(analyze):
    result = analyze([
        _txn("2025-01-02", "Option Sell", 2, 2),
        _txn("2025-01-04", "Option Buy", 5, 1),
    ])
    assert result["open_contracts"][0]["qty"] == 3
    assert result["open_contracts"][0]["open_date"] is None
    assert result["open_contracts"][0]["basis"] is None
    assert result["open_contracts"][0]["entry_price"] is None


def test_missing_and_nonfinite_financial_values_stay_unavailable(analyze):
    rows = [
        _txn("2025-01-02", "Option Buy", 1, 2, basis_effect="add"),
        _txn("2025-01-04", "Option Sell", 1, 3, cost_basis=200,
             realized_gain=float("nan"), basis_effect="remove"),
    ]
    result = analyze(rows, annotate=False)
    assert result["stats"]["realized_pnl"] is None
    assert result["completed_positions"] == []
    assert result["contract_stats"]["unknown"] == 1
    assert result["cumulative_pnl"][0]["value"] is None
    assert result["by_underlying"][0]["realized"] is None
    json.dumps(result, allow_nan=False)
    open_only = analyze(rows[:1], annotate=False)
    assert open_only["open_contracts"][0]["entry_price"] is None
    assert open_only["open_contracts"][0]["basis"] is None
