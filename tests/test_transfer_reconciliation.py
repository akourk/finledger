"""Fictional transfer receipts need compatible share units and known basis.

The quantity written by the broker stays authoritative. An approximate match
is not evidence of a fee or a corporate action; supported splits change only
the transferred lots, while unsupported movements remain explicit coverage gaps.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math

import pytest


SOURCE = "Reconcile Z Source"
DESTINATION = "Reconcile A Destination"
THIRD = "Reconcile C Bridge"
SYMBOL = "PRISM"


@pytest.fixture
def market(isolated_workdir, monkeypatch):
    from src import history, prices, valuation
    from src.config import ACCOUNT_TYPES

    monkeypatch.setenv("FIN_AS_OF_DATE", "2024-03-10")
    ACCOUNT_TYPES.update({group: "Taxable" for group in (SOURCE, DESTINATION, THIRD)})
    state = {"price": 100.0, "splits": [], "missing": set()}

    def quote(symbol, day):
        if symbol != SYMBOL:
            return 100.0
        return None if day in state["missing"] else state["price"]

    def factor(symbol, day):
        return math.prod(ratio for effective, ratio in state["splits"]
                         if symbol == SYMBOL and effective > day)

    monkeypatch.setattr(prices, "get_price", quote)
    monkeypatch.setattr(history, "get_price", quote)
    monkeypatch.setattr(valuation, "get_price", quote)
    monkeypatch.setattr(prices, "split_factor_since", factor)
    monkeypatch.setattr(valuation, "split_factor_since", factor)
    monkeypatch.setattr(valuation, "option_intrinsic", lambda symbol, day: None)
    return state


def _row(group, action, qty, *, day="2024-01-01", price=100.0,
         amount=None, **extra):
    from src.basis import txn_external_cash_flow

    row = {"date": day, "account": group, "account_group": group,
           "account_type": "Taxable", "symbol": SYMBOL, "action": action,
           "raw_action": action, "quantity": qty, "price": price,
           "amount": qty * price if amount is None else amount,
           "fees": 0.0, "description": "", "source": "fictional-reconciliation",
           **extra}
    row["cash_flow"] = txn_external_cash_flow(row)
    return row


def _sequence(rows):
    for index, row in enumerate(rows):
        row["seq"] = index
    return rows


def _walk(rows, method="fifo"):
    from src.basis import compute_basis_default
    from src.history import compute_history
    from src.return_flows import annotate_account_transfers

    methods = {group: method for group in (SOURCE, DESTINATION, THIRD)}
    state = compute_basis_default(rows, account_methods=methods)
    annotate_account_transfers(rows)
    snapshots = compute_history(rows, {SYMBOL: "Other"}, cadence="day",
                                account_methods=methods)
    return state, snapshots


def _holding(state, group, method="fifo"):
    from src.basis import state_to_holdings

    return next((p for p in state_to_holdings(state, method)
                 if p["account_group"] == group and p["symbol"] == SYMBOL),
                {"quantity": 0.0, "cost_basis": 0.0})


def _assert_history_basis(state, snapshots):
    for position in snapshots[-1]["positions"]:
        assert position["cost_basis"] == _holding(
            state, position["account_group"])["cost_basis"]


def _warnings(rows, snapshots):
    from src.analytics.data_health import compute_data_health
    from src.config import CACHE_DIR

    holdings = [{**p, "account_type": "Taxable", "sector": "Other"}
                for p in snapshots[-1]["positions"]]
    return compute_data_health(rows, holdings, snapshots, {}, CACHE_DIR,
                               parse_report=[])


def _approximate_case(*, same_day=False, same_group=False, override=None):
    destination = SOURCE if same_group else DESTINATION
    rows = [_row(SOURCE, "Buy", 600, price=10),
            _row(SOURCE, "Buy", 500, day="2024-01-10", price=20),
            _row(destination, "Buy", 7, day="2024-01-20", price=30),
            _row(SOURCE, "Transfer Out", 1000, day="2024-02-27", amount=1000),
            _row(destination, "Transfer In", 999.5,
                 day="2024-02-27" if same_day else "2024-03-05",
                 price=40, amount=999.5)]
    if override is not None:
        rows[-1]["basis_override"] = override
    return _sequence(rows)


@pytest.mark.parametrize("method,remaining_basis", [("fifo", 2000),
                                                    ("lifo", 1000),
                                                    ("hifo", 1000)])
@pytest.mark.parametrize("same_day", [False, True])
@pytest.mark.parametrize("override", [None, 12000])
def test_approximate_receipt_is_unpaired_without_phantom_lots(
        market, method, remaining_basis, same_day, override):
    from src.basis import _pair_transfers, txn_external_cash_flow

    rows = _approximate_case(same_day=same_day, override=override)
    pairings = _pair_transfers(rows)
    assert not pairings["tin_to_tout"]
    assert pairings["issues"][0]["reason"] == "quantity_mismatch"
    state, snapshots = _walk(rows, method)
    source = _holding(state, SOURCE)
    destination = _holding(state, DESTINATION)
    assert source["quantity"] == 100
    assert source["cost_basis"] == remaining_basis
    assert destination["quantity"] == 1006.5
    assert destination["cost_basis"] == 210 + (39980 if override is None else override)
    assert state["realized_total"] == 0
    assert rows[-1]["basis_effect"] == "transfer_in_unpaired"
    assert not any(t.get("account_transfer") for t in rows)
    assert not any(h.get("in_transit") for h in snapshots)
    assert all(txn_external_cash_flow(t) == 0 for t in rows[-2:])
    assert any("transfer" in issue["kind"] and issue["severity"] == "warn"
               for issue in _warnings(rows, snapshots))
    _assert_history_basis(state, snapshots)

    # Selling every posted destination share must not leave a hidden half-lot.
    sold = deepcopy(rows)
    sold.append(_row(DESTINATION, "Sell", 1006.5, day="2024-03-08", price=50))
    sold_state, _ = _walk(_sequence(sold), method)
    assert _holding(sold_state, DESTINATION)["quantity"] == 0
    assert _holding(sold_state, DESTINATION)["cost_basis"] == 0


@pytest.mark.parametrize("same_day", [False, True])
def test_same_group_approximate_move_is_not_an_intra_group_noop(market, same_day):
    from src.basis import _pair_transfers
    from src.pipeline_stages import walk_balances

    rows = _approximate_case(same_group=True, same_day=same_day)
    assert not _pair_transfers(rows)["intra_group"]
    state, snapshots = _walk(rows)
    _, balances = walk_balances(rows)
    assert _holding(state, SOURCE)["quantity"] == balances[(SOURCE, SYMBOL)] == 1106.5
    assert _holding(state, SOURCE)["cost_basis"] == 42190
    assert rows[-1]["basis_effect"] == "transfer_in_unpaired"
    _assert_history_basis(state, snapshots)


def test_average_method_approximate_receipt_keeps_actual_posted_quantity(market):
    from src.basis import compute_basis_all_methods

    state = compute_basis_all_methods(_approximate_case())["avg"]
    assert _holding(state, SOURCE, "avg")["quantity"] == 100
    assert _holding(state, SOURCE, "avg")["cost_basis"] == 1454.55
    assert _holding(state, DESTINATION, "avg")["quantity"] == 1006.5
    assert _holding(state, DESTINATION, "avg")["cost_basis"] == 40190


def _split_case(ratio=2.0, *, arrival="2024-03-05", source_qty=10):
    return _sequence([
        _row(SOURCE, "Contribution", source_qty),
        _row(SOURCE, "Transfer Out", 10, day="2024-02-27", price=0, amount=10),
        _row(DESTINATION, "Transfer In", 10 * ratio, day=arrival,
             price=0, amount=10 * ratio),
    ])


@pytest.mark.parametrize("events,ratio", [
    ([("2024-03-01", 2.0)], 2.0),
    ([("2024-03-01", 0.5)], 0.5),
    ([("2024-03-05", 2.0)], 2.0),
    ([("2024-03-01", 2.0), ("2024-03-03", 1.5)], 3.0),
    ([("2024-03-01", 2.0), ("2024-03-03", 0.5)], 1.0),
])
def test_verified_split_preserves_basis_and_daily_exposure(market, events, ratio):
    from src.analytics._shared import _value_at_date
    from src.basis import _pair_transfers, _sort_key
    from src.history import compute_daily_totals
    from src.return_flows import scope_snapshot_basis, scope_snapshot_value

    market.update(splits=events, price=100 / ratio)
    rows = _split_case(ratio)
    pairings = _pair_transfers(rows)
    assert pairings["tin_to_tout"][id(rows[-1])] is rows[-2]
    assert pairings["share_ratios"][id(rows[-1])] == ratio
    state, snapshots = _walk(rows)
    assert _holding(state, DESTINATION)["quantity"] == 10 * ratio
    assert _holding(state, DESTINATION)["cost_basis"] == 1000
    assert state["realized_total"] == 0
    assert rows[-1]["cost_basis"] == rows[-2]["cost_basis"] == 1000
    assert rows[-2]["account_transfer"]["flow"] == pytest.approx(-1000)
    assert rows[-1]["account_transfer"]["flow"] == pytest.approx(1000)
    lots = state["lots"][(DESTINATION, SYMBOL)]
    assert all(lot["date"] == "2024-01-01" for lot in lots)
    assert all(lot["origin"] == "reconstructed" for lot in lots)
    daily = dict(compute_daily_totals(rows))
    for day in ("2024-02-26", "2024-02-27", "2024-03-01", "2024-03-04", "2024-03-05"):
        snapshot = next(h for h in snapshots if h["date"] == day)
        assert scope_snapshot_value(snapshot) == 1000
        assert scope_snapshot_basis(snapshot) == 1000
        assert daily[day] == 1000
        assert _value_at_date(sorted(rows, key=_sort_key), day, None, []) == pytest.approx(1000)
        assert not any(p.get("valuation_issue") for p in snapshot.get("in_transit", []))
    assert not any("transfer" in issue["kind"] for issue in _warnings(rows, snapshots))
    _assert_history_basis(state, snapshots)


@pytest.mark.parametrize("method,source_basis,received_basis", [
    ("fifo", 500, 1000), ("lifo", 500, 1000), ("hifo", 500, 1000),
])
def test_split_receipt_leaves_source_remainder_and_destination_lots_intact(
        market, method, source_basis, received_basis):
    market.update(splits=[("2024-03-01", 2.0)], price=50)
    rows = _split_case(source_qty=15)
    rows.extend([_row(SOURCE, "Split", 5, day="2024-03-01", price=0, amount=0),
                 _row(DESTINATION, "Buy", 7, day="2024-03-02", price=30)])
    state, snapshots = _walk(_sequence(rows), method)
    assert _holding(state, SOURCE) == {"account_group": SOURCE, "symbol": SYMBOL,
                                      "quantity": 10, "cost_basis": source_basis}
    assert _holding(state, DESTINATION)["quantity"] == 27
    assert _holding(state, DESTINATION)["cost_basis"] == received_basis + 210
    original = next(lot for lot in state["lots"][(DESTINATION, SYMBOL)]
                    if lot["date"] == "2024-03-02")
    assert original["qty"] == 7
    assert original["basis_per_share"] == 30
    _assert_history_basis(state, snapshots)


@pytest.mark.parametrize("method,source_basis,received_basis", [
    ("fifo", 2000, 14000), ("lifo", 1000, 15000),
    ("hifo", 1000, 15000), ("avg", 1454.55, 14545.45),
])
def test_split_conversion_preserves_distinct_lot_selection(
        market, method, source_basis, received_basis):
    from src.basis import compute_basis_all_methods

    market.update(splits=[("2024-03-01", 2.0)], price=50)
    rows = _sequence([
        _row(SOURCE, "Buy", 600, price=10),
        _row(SOURCE, "Buy", 500, day="2024-01-10", price=20),
        _row(SOURCE, "Transfer Out", 1000, day="2024-02-27", price=0, amount=1000),
        _row(SOURCE, "Split", 100, day="2024-03-01", price=0, amount=0),
        _row(DESTINATION, "Buy", 7, day="2024-03-02", price=30),
        _row(DESTINATION, "Transfer In", 2000, day="2024-03-05", price=0, amount=2000),
    ])
    if method == "avg":
        state = compute_basis_all_methods(rows)[method]
    else:
        state, snapshots = _walk(rows, method)
        _assert_history_basis(state, snapshots)
        assert rows[-1]["cost_basis"] == rows[2]["cost_basis"] == received_basis
        carried = {lot["date"]: lot for lot in state["lots"][(DESTINATION, SYMBOL)]}
        assert carried["2024-01-01"]["qty"] == (1200 if method == "fifo" else 1000)
        assert carried["2024-01-10"]["qty"] == (800 if method == "fifo" else 1000)
        assert carried["2024-01-01"]["basis_per_share"] == 5
        assert carried["2024-01-10"]["basis_per_share"] == 10
    assert _holding(state, SOURCE, method)["quantity"] == 200
    assert _holding(state, SOURCE, method)["cost_basis"] == source_basis
    assert _holding(state, DESTINATION, method)["quantity"] == 2007
    assert _holding(state, DESTINATION, method)["cost_basis"] == received_basis + 210
    assert state["realized_total"] == 0


def test_split_conversion_preserves_broker_basis_origin(market):
    market.update(splits=[("2024-03-01", 2.0)], price=50)
    rows = _split_case()
    rows[0]["basis_override"] = 725
    state, snapshots = _walk(rows)
    lot, = state["lots"][(DESTINATION, SYMBOL)]
    assert lot["origin"] == "broker"
    assert lot["date"] == "2024-01-01"
    assert lot["qty"] == 20
    assert lot["basis_per_share"] == 36.25
    assert _holding(state, DESTINATION)["cost_basis"] == 725
    _assert_history_basis(state, snapshots)


@pytest.mark.parametrize("same_group", [False, True])
def test_unresolved_split_bookkeeping_is_diagnostic_without_inferred_ownership(market, same_group):
    from src.basis import _pair_transfers

    market.update(splits=[("2024-03-01", 2.0)], price=50)
    rows = _split_case()
    if same_group:
        rows[-1].update(account=SOURCE, account_group=SOURCE)
    else:
        rows.append(_row(DESTINATION, "Split", 3, day="2024-03-01", price=0, amount=0))
    _sequence(rows)
    pairings = _pair_transfers(rows)
    assert not pairings["tin_to_tout"]
    assert pairings["issues"][0]["reason"] == "unsupported_split_transfer"
    _, snapshots = _walk(rows)
    assert not any(t.get("account_transfer") for t in rows)
    assert not any(h.get("in_transit") for h in snapshots)
    assert any("transfer" in issue["kind"] and issue["severity"] == "warn"
               for issue in _warnings(rows, snapshots))


@pytest.mark.parametrize("ratio", [1.0, 2.0])
def test_missing_source_lots_are_not_stretched_to_fill_a_receipt(market, ratio):
    from src.basis import compute_basis_all_methods

    market.update(splits=[] if ratio == 1 else [("2024-03-01", ratio)], price=100 / ratio)
    rows = _split_case(ratio, source_qty=4)
    state, snapshots = _walk(rows)
    destination = _holding(state, DESTINATION)
    assert destination["quantity"] == 4 * ratio
    assert destination["cost_basis"] == 400
    posted = next(p for p in snapshots[-1]["positions"] if p["account_group"] == DESTINATION)
    assert posted["quantity"] == 10 * ratio
    assert posted["cost_basis"] == 400
    for method, result in compute_basis_all_methods(deepcopy(rows)).items():
        assert _holding(result, DESTINATION, method)["quantity"] == 4 * ratio
        assert _holding(result, DESTINATION, method)["cost_basis"] == 400


def test_cache_miss_after_split_does_not_reuse_a_pre_split_transaction_price(market):
    market.update(splits=[("2024-03-01", 2.0)], price=50, missing={"2024-03-02"})
    rows = _split_case()
    _, snapshots = _walk(rows)
    before = next(h for h in snapshots if h["date"] == "2024-02-29")
    missing = next(h for h in snapshots if h["date"] == "2024-03-02")
    assert before["in_transit"][0]["value"] == 1000
    assert missing["in_transit"][0]["value"] is None
    assert missing["in_transit"][0]["valuation_issue"] == "unpriced"


@pytest.mark.parametrize("same_day", [False, True])
def test_verified_receipt_preserves_annotation_reconstruction_and_export_order(market, same_day):
    from src.basis import derive_basis_by_key_from_txns
    from src.export import export_json
    from src.config import EXPORT_DIR

    rows = _split_case(1, arrival="2024-02-27" if same_day else "2024-03-05")
    # Retained source basis makes source annotation parity non-vacuous.
    rows[0].update(quantity=15, amount=1500)
    state, snapshots = _walk(rows)
    reconstructed = derive_basis_by_key_from_txns(rows)
    assert reconstructed[(SOURCE, SYMBOL)] == _holding(state, SOURCE)["cost_basis"] == 500
    assert reconstructed[(DESTINATION, SYMBOL)] == 1000
    output = EXPORT_DIR / "fictional-reconciled-transfer.json"
    export_json(rows, output, history=snapshots)
    loaded = json.loads(output.read_text(encoding="utf-8"))
    again, rebuilt = _walk(list(reversed(loaded["transactions"])))
    assert _holding(again, SOURCE) == _holding(state, SOURCE)
    assert _holding(again, DESTINATION) == _holding(state, DESTINATION)
    assert rebuilt == snapshots


def test_verified_split_can_continue_through_a_second_transfer(market):
    market.update(splits=[("2024-03-01", 2.0)], price=50)
    rows = _split_case()
    rows.extend([_row(DESTINATION, "Transfer Out", 20, day="2024-03-05", price=0, amount=20),
                 _row(THIRD, "Transfer In", 20, day="2024-03-07", price=0, amount=20)])
    state, snapshots = _walk(_sequence(rows))
    assert _holding(state, DESTINATION)["quantity"] == 0
    assert _holding(state, THIRD)["quantity"] == 20
    assert _holding(state, THIRD)["cost_basis"] == 1000
    assert state["realized_total"] == 0
    assert all(lot["date"] == "2024-01-01" for lot in state["lots"][(THIRD, SYMBOL)])
    from src.return_flows import scope_snapshot_value

    assert all(scope_snapshot_value(h) == 1000 for h in snapshots)
    _assert_history_basis(state, snapshots)
