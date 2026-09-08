"""Split-reconciled Python exports retain their meaning in the shipped bundle."""
from __future__ import annotations

import json

import pytest

from tests.test_frontend_review_regressions import payload, run_js
from tests.test_transfer_reconciliation import (
    DESTINATION, SOURCE, SYMBOL, _split_case, _walk, market,
)


def _exported_payload(rows, snapshots):
    data = payload(snapshots, rows)
    data.update(as_of="2024-03-10", generated="2024-03-10T12:00:00",
                holdings=snapshots[-1]["positions"],
                holdings_by_account=snapshots[-1]["positions"],
                sector_of={SYMBOL: "Other"})
    # Exercise JSON's exported types, without asking JavaScript to infer splits.
    return json.loads(json.dumps(data, allow_nan=False))


def _probe(tmp_path, data, days):
    scopes = [None, [SOURCE, DESTINATION], [SOURCE], [DESTINATION], [], ["Absent"]]
    return run_js(tmp_path, data, """
      const scopes = SCOPES.map(groups => groups === null ? null : new Set(groups));
      const answers = DAYS.map(day => {
        asOfDate = day;
        const snap = getAsOfSnapshot();
        return {
          day,
          scopes: scopes.map(groups => ({
            value: _snapshotValueForGroups(snap, groups),
            basis: _snapshotBasisForGroups(snap, groups),
            performance: historicalGroupPerformance(groups, day),
          })),
          holdings: asOfHoldingsByAccount(),
        };
      });
      process.stdout.write(JSON.stringify({answers, errors}));
    """.replace("SCOPES", json.dumps(scopes)).replace("DAYS", json.dumps(days)))


def _assert_python_parity(result, rows, snapshots):
    from src.analytics._shared import compute_money_weighted_return, compute_twr_summary
    from src.return_flows import scope_snapshot_basis, scope_snapshot_value

    assert not result["errors"]
    scopes = [None, {SOURCE, DESTINATION}, {SOURCE}, {DESTINATION}, set(), {"Absent"}]
    for answer in result["answers"]:
        selected = [h for h in snapshots if h["date"] <= answer["day"]]
        for group, actual in zip(scopes, answer["scopes"]):
            assert actual["value"] == scope_snapshot_value(selected[-1], group)
            assert actual["basis"] == scope_snapshot_basis(selected[-1], group)
            expected_twr = compute_twr_summary(rows, selected, [], group)
            expected_xirr = compute_money_weighted_return(rows, selected, [], group)
            if expected_twr is None:
                assert actual["performance"] is None
                continue
            performance = actual["performance"]
            assert performance["summary"]["cumulative"] == pytest.approx(
                expected_twr["cumulative"], abs=1e-6)
            assert performance["summary"]["start_date"] == expected_twr["start_date"]
            assert performance["summary"]["end_date"] == expected_twr["end_date"]
            if expected_xirr is None:
                assert performance["money_weighted"] is None
            else:
                assert performance["money_weighted"]["annualized"] == pytest.approx(
                    expected_xirr["annualized"], abs=1e-6)


@pytest.mark.parametrize("ratio", [2.0, 0.5])
@pytest.mark.parametrize("market_gain", [False, True])
def test_split_transit_scope_values_basis_and_returns_match_python(
        market, monkeypatch, tmp_path, ratio, market_gain):
    from src import history, prices, valuation

    market.update(splits=[("2024-03-01", ratio)], price=100 / ratio)
    if market_gain:
        original = valuation.get_price

        def quote(symbol, day):
            value = original(symbol, day)
            return value * 1.1 if symbol == SYMBOL and day >= "2024-03-02" else value

        for module in (history, prices, valuation):
            monkeypatch.setattr(module, "get_price", quote)
    rows = _split_case(ratio)
    _, snapshots = _walk(rows)
    days = ["2024-02-26", "2024-02-29", "2024-03-02", "2024-03-05", "2024-03-07"]
    result = _probe(tmp_path, _exported_payload(rows, snapshots), days)
    _assert_python_parity(result, rows, snapshots)

    for answer in result["answers"][1:3]:
        transit, = [p for p in answer["holdings"] if p.get("_inTransit")]
        # Both quantity and price are exported in departure units. JavaScript
        # consumes the dollar mark once, even after a forward or reverse split.
        assert transit["quantity"] == 10
        assert transit["value"] == transit["quantity"] * transit["price"]
        assert transit["cost_basis"] == 1000
    after_split = result["answers"][2]
    assert after_split["scopes"][0]["value"] == (1100 if market_gain else 1000)
    assert after_split["scopes"][0]["performance"]["summary"]["cumulative"] == pytest.approx(
        0.1 if market_gain else 0)
    assert after_split["scopes"][2]["performance"]["summary"]["cumulative"] == pytest.approx(0)


@pytest.mark.parametrize("has_dated_quote", [False, True])
def test_split_arrival_cache_miss_does_not_become_a_stale_quote_gain_in_javascript(
        market, tmp_path, has_dated_quote):
    market.update(splits=[("2024-03-01", 2.0)], price=50, missing={"2024-03-05"})
    rows = _split_case()
    if not has_dated_quote:
        rows[0]["price"] = 0
    _, snapshots = _walk(rows)
    expected_value = 1000 if has_dated_quote else None
    assert rows[-1]["account_transfer"]["flow"] == expected_value
    arrival = next(h for h in snapshots if h["date"] == "2024-03-05")
    destination, = arrival["positions"]
    assert destination["value"] == expected_value
    assert destination["cost_basis"] == 1000
    result = _probe(tmp_path, _exported_payload(rows, snapshots),
                    ["2024-02-29", "2024-03-04", "2024-03-05", "2024-03-06"])
    _assert_python_parity(result, rows, snapshots)
    assert [answer["scopes"][0]["value"] for answer in result["answers"]] == [
        1000, 1000, expected_value or 0, 1000]
    assert all(answer["scopes"][0]["basis"] == 1000 for answer in result["answers"])
