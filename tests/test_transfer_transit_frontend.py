"""Shipped dashboard behavior for independently fictional in-transit assets."""
from __future__ import annotations

import copy
import json
import random

import pytest

from tests.test_account_transfer_frontend import _card
from tests.test_frontend_review_regressions import payload, run_js


def _position(group, symbol, qty, price, basis):
    return {"account_group": group, "account_type": "Taxable", "symbol": symbol,
            "quantity": qty, "price": price, "value": qty * price,
            "cost_basis": basis, "unrealized_gain": qty * price - basis,
            "sector": "Equity"}


def _snapshot(day, state, price=100):
    positions = [_position("Alpha", "AAA", 7, 100, 490),
                 _position("Beta", "BBB", 10, 100, 700)]
    if state != "transit":
        positions.append(_position("Alpha" if state == "before" else "Beta",
                                   "MOVE", 3, price, 210))
    values = {group: sum(p["value"] for p in positions if p["account_group"] == group)
              for group in ["Alpha", "Beta"]}
    bases = {group: sum(p["cost_basis"] for p in positions if p["account_group"] == group)
             for group in ["Alpha", "Beta"]}
    total, basis = sum(values.values()), sum(bases.values())
    snap = {"date": day, "total": total, "total_cost_basis": basis,
            "by_account_group": values, "cost_basis_by_group": bases,
            "by_account_type": {"Taxable": total}, "cost_basis_by_type": {"Taxable": basis},
            "by_sector": {"Equity": total}, "net_contributed": 2000,
            "benchmark_spy_price": 100, "positions": positions}
    if state == "transit":
        snap["in_transit"] = [{"source_group": "Alpha", "destination_group": "Beta",
                               "symbol": "MOVE", "quantity": 3, "price": price,
                               "value": 3 * price, "cost_basis": 210}]
    return snap


def _data(price=100):
    history = [_snapshot("2023-12-31", "before"),
               _snapshot("2024-01-15", "transit"),
               _snapshot("2024-01-20", "transit", price),
               _snapshot("2024-01-25", "after", price),
               _snapshot("2024-03-31", "after", price)]
    transactions = [
        {"date": "2023-12-31", "account_group": group, "account_type": "Taxable",
         "action": "Deposit", "symbol": "USD", "cash_flow": 1000, "amount": 1000}
        for group in ["Alpha", "Beta"]]
    for day, group, other, action, flow in [
            ("2024-01-15", "Alpha", "Beta", "Transfer Out", -300),
            ("2024-01-25", "Beta", "Alpha", "Transfer In", 3 * price)]:
        transactions.append({"date": day, "account_group": group, "account_type": "Taxable",
                             "symbol": "MOVE", "action": action, "quantity": 3, "amount": 3,
                             "cash_flow": 0,
                             "account_transfer": {"counterparty_group": other, "flow": flow}})
    data = payload(history, transactions)
    data["holdings_by_account"] = history[-1]["positions"]
    data["holdings"] = history[-1]["positions"]
    data["cash_summary"] = {"net_contributed": 2000}
    data["sector_of"] = {symbol: "Equity" for symbol in ["AAA", "BBB", "MOVE"]}
    return data


def test_transit_value_and_basis_belong_to_both_endpoints_only(tmp_path):
    result = run_js(tmp_path, _data(110), """
      const scopes = [null, new Set(['Alpha', 'Beta']), new Set(['Alpha']),
        new Set(['Beta']), new Set(['Absent']), new Set()];
      process.stdout.write(JSON.stringify(scopes.map(s => ({
        value: _snapshotValueForGroups(history[2], s),
        basis: _snapshotBasisForGroups(history[2], s),
        start: _snapshotValueForGroups(history[0], s),
        end: _snapshotValueForGroups(history[3], s),
      }))));
    """)
    assert result == [
        {"value": 2030, "basis": 1400, "start": 2000, "end": 2030},
        {"value": 2030, "basis": 1400, "start": 2000, "end": 2030},
        {"value": 700, "basis": 490, "start": 1000, "end": 700},
        {"value": 1000, "basis": 700, "start": 1000, "end": 1330},
        {"value": 0, "basis": 0, "start": 0, "end": 0},
        {"value": 0, "basis": 0, "start": 0, "end": 0},
    ]


def test_fractional_marks_are_combined_before_rounding_without_changing_account_custody(tmp_path):
    data = _data()
    snap = data["history"][2]
    posted = _position("Alpha", "MOVE", 0.5, 100.012, 40.006)
    snap.update({"total": 50.01, "total_cost_basis": 40.01,
                 "by_account_group": {"Alpha": 50.01}, "cost_basis_by_group": {"Alpha": 40.01},
                 "by_account_type": {"Taxable": 50.01}, "cost_basis_by_type": {"Taxable": 40.01},
                 "by_sector": {"Equity": 50.01},
                 "positions": [{**posted, "value": 50.01, "cost_basis": 40.01}],
                 "in_transit": [{"source_group": "Alpha", "destination_group": "Beta", "symbol": "MOVE",
                                 "quantity": 0.5, "price": 100.012, "value": 50.006, "cost_basis": 40.006}],
                 "valuation_precision": {"total": 50.006, "total_cost_basis": 40.006,
                                         "by_account_group": {"Alpha": 50.006},
                                         "cost_basis_by_group": {"Alpha": 40.006},
                                         "by_account_type": {"Taxable": 50.006},
                                         "cost_basis_by_type": {"Taxable": 40.006},
                                         "by_sector": {"Equity": 50.006}, "positions": [posted]}})
    result = run_js(tmp_path, data, """
      asOfDate = '2024-01-20'; renderHoldings();
      const snap = getAsOfSnapshot();
      process.stdout.write(JSON.stringify({
        scopes: [null, new Set(['Alpha', 'Beta']), new Set(['Alpha']), new Set(['Beta']), new Set()]
          .map(s => [_snapshotValueForGroups(snap, s), _snapshotBasisForGroups(snap, s)]),
        type: [_snapshotTypeAmount(snap, 'Taxable'), _snapshotTypeAmount(snap, 'Taxable', 'cost_basis')],
        breakdowns: ['by_account_group', 'by_account_type', 'by_sector']
          .map(f => _roundSnapshotCents(Object.values(_snapshotBreakdown(snap, f)).reduce((a, b) => a + b, 0))),
        holdingsTotal: nodeFor('holdingsTotalValue').textContent,
      }));
    """)
    assert result["scopes"] == [[100.01, 80.01], [100.01, 80.01], [50.01, 40.01], [0, 0], [0, 0]]
    assert result["type"] == [100.01, 80.01]
    assert result["breakdowns"] == [100.01, 100.01, 100.01]
    assert result["holdingsTotal"] == "$100.01"


def test_combined_snapshot_rounding_matches_python_binary_float_ties(tmp_path):
    rng = random.Random(42)
    values = [0, 1001.125, -1001.125, 2.675, 1.005, 100.012, -0.005, 0.125,
              1e-300, -1e-300, 1e14 + 0.125, 1e15, -1e15]
    values += [rng.uniform(-10**scale, 10**scale) for scale in range(-5, 16) for _ in range(10)]
    result = run_js(tmp_path, payload(),
                    "process.stdout.write(JSON.stringify(" + json.dumps(values) + ".map(_roundSnapshotCents)));")
    assert result == [round(value, 2) for value in values]


@pytest.mark.parametrize("price", [100, 110])
def test_returns_preserve_transit_market_movement_without_custody_gains(tmp_path, price):
    result = run_js(tmp_path, _data(price), """
      const scopes = [null, new Set(['Alpha', 'Beta']), 'Alpha', 'Beta'];
      process.stdout.write(JSON.stringify(scopes.map(s => ({
        throughFlight: computeTimeWeightedReturnForWindow(s, '2023-12-31', '2024-01-20'),
        fromFlight: computeTimeWeightedReturnForWindow(s, '2024-01-20', '2024-03-31'),
        annual: computeAnnualReturns(s).find(r => r.year === '2024'),
        xirr: historicalGroupPerformance(s instanceof Set ? s : new Set(s ? [s] : ['Alpha', 'Beta']), '2024-01-20'),
      }))));
    """)
    gain = 3 * (price - 100)
    for item in result[:2]:
        assert item["throughFlight"]["cumulative"] == pytest.approx(gain / 2000)
        assert item["fromFlight"]["cumulative"] == pytest.approx(0)
        assert item["annual"]["dollar_return"] == pytest.approx(gain)
        assert item["xirr"]["money_weighted"]["annualized"] == pytest.approx(
            (1 + gain / 2000) ** (365.25 / 20) - 1, abs=1e-9)
    for item in result[2:]:
        assert item["throughFlight"]["cumulative"] == pytest.approx(0)
        assert item["fromFlight"]["cumulative"] == pytest.approx(0)
        assert item["annual"]["dollar_return"] == pytest.approx(0)
        assert item["xirr"]["money_weighted"]["annualized"] == pytest.approx(0, abs=1e-9)


def test_month_end_transit_does_not_create_risk_or_drawdown(tmp_path):
    from src.analytics.monthly_pnl import compute_monthly_pnl

    data = _data()
    data["transactions"][2]["date"] = "2024-01-25"
    data["transactions"][3]["date"] = "2024-02-05"
    data["history"] = [_snapshot("2023-12-31", "before"), _snapshot("2024-01-31", "transit")]
    data["history"] += [_snapshot(day, "after") for day in [
        "2024-02-29", "2024-03-31", "2024-04-30", "2024-05-31", "2024-06-30"]]
    expected = compute_monthly_pnl(data["history"], data["transactions"])
    result = run_js(tmp_path, data, """
      process.stdout.write(JSON.stringify([null, '__taxable__'].map(s => computeWindowedMetrics(s, 'lifetime'))));
    """)
    for metric in result:
        assert metric["cum"] == pytest.approx(0)
        assert metric["mdd"] == 0 and metric["nMonths"] == 6
        assert metric["sharpe"] == expected["sharpe"]
        assert metric["sortino"] == expected["sortino"]


@pytest.mark.parametrize("start,end,expected", [
    ("2023-12-31", "2024-01-20", "+$30.00"),
    ("2024-01-20", "2024-03-31", "+$0.00"),
])
def test_performance_cards_and_benchmark_line_share_transit_balances(tmp_path, start, end, expected):
    result = run_js(tmp_path, _data(110), f"""
      const charts = [];
      const chart = renderMultiLineChart;
      renderMultiLineChart = (...args) => {{ charts.push(args); return chart(...args); }};
      performanceAccountFilter = '__taxable__'; performanceWindow = 'custom';
      perfTwrStart = '{start}'; perfTwrEnd = '{end}'; renderPerformance();
      process.stdout.write(JSON.stringify({{charts, html: NODES.get('performanceContent').innerHTML, errors}}));
    """)
    assert not result["errors"]
    assert _card(result["html"], "Total Return").startswith(expected)
    assert _card(result["html"], "Unrealized").startswith("+$630.00")
    lines = [series for args in result["charts"] for series in args[0]
             if series.get("label") == "Your Portfolio"]
    assert lines
    point = next(p for p in lines[-1]["points"] if p["date"] == "2024-01-20")
    assert point["value"] == 2030


def test_historical_holdings_overview_and_composition_reconcile_transit(tmp_path):
    result = run_js(tmp_path, _data(110), """
      asOfDate = '2024-01-20'; renderHoldings(); renderByAssetTable(); renderStats(); renderAllocation();
      const composition = ['account', 'type', 'sector'].map(dim => {
        historyCompositionDim = dim;
        _renderComposition(nodeFor('transitComposition'), nodeFor('transitTooltip'), nodeFor('transitLegend'), history);
        nodeFor('chartCapture').listeners.mousemove({clientX: 474, clientY: 100});
        return {legend: nodeFor('transitLegend').innerHTML, tooltip: nodeFor('transitTooltip').innerHTML};
      });
      process.stdout.write(JSON.stringify({
        holdings: asOfHoldingsByAccount(), assets: asOfHoldingsByAsset(),
        groups: withPerformance(groupByBasisAware(asOfHoldingsByAccount(), 'account_group')),
        table: nodeFor('holdingsTbody').innerHTML, byAsset: nodeFor('byAssetTbody').innerHTML,
        total: nodeFor('holdingsTotalValue').textContent, stats: nodeFor('stats').innerHTML,
        allocation: ['Group', 'Type', 'Sector'].map(s => ({svg: nodeFor('allocationSvg' + s).innerHTML,
          legend: nodeFor('allocationLegend' + s).innerHTML})),
        accountOptions: ACCOUNT_OPTIONS, assetOptions: nodeFor('byAssetAccountGroupFilter').innerHTML,
        composition, errors}));
    """)
    assert not result["errors"]
    assert sum(p["value"] for p in result["holdings"]) == 2030
    assert sum(p["cost_basis"] for p in result["holdings"]) == 1400
    assert sum(p["value"] for p in result["assets"]) == 2030
    transit = next(p for p in result["groups"] if p["account_group"] == "In transit")
    assert transit["twr_cum"] is None and transit["xirr"] is None
    assert "Alpha → Beta" in result["table"] and "In transit · Alpha → Beta" in result["byAsset"]
    assert "In transit" not in result["accountOptions"] and "In transit" not in result["assetOptions"]
    assert result["total"] == "$2,030.00"
    assert _card(result["stats"], "Cost Basis") == "$1,400.00"
    assert _card(result["stats"], "Unrealized P&amp;L").startswith("+$630.00")
    for allocation in result["allocation"]:
        assert "$2.0k" in allocation["svg"]
    for dimension in result["composition"]:
        assert "total $2,030.00" in dimension["tooltip"]
    for dimension in result["composition"][:2]:
        assert "In transit" in dimension["legend"] and "$330.00" in dimension["tooltip"]
    for allocation in result["allocation"][:2]:
        assert "In transit" in allocation["legend"]
    assert "In transit" not in result["allocation"][2]["legend"]


def test_type_and_sector_history_preserve_exposure_without_reclassifying_transit(tmp_path):
    data = _data(110)
    for row in data["transactions"] + data["holdings_by_account"]:
        row["account_type"] = "Retirement"
    for snap in data["history"]:
        snap["by_account_type"] = {"Retirement": snap["total"]}
        snap["cost_basis_by_type"] = {"Retirement": snap["total_cost_basis"]}
    result = run_js(tmp_path, data, """
      historySelection.clear();
      ['total', 'account:Alpha', 'type:Retirement', 'sector:Equity'].forEach(s => historySelection.add(s));
      historyOverlays.add('basis');
      process.stdout.write(JSON.stringify(buildHistorySeries()));
    """)
    series = {s["key"]: s for s in result}
    for key in ["Total", "Retirement", "Equity"]:
        assert series[key]["points"][2]["value"] == 2030
    for key in ["Total Basis", "Retirement Basis"]:
        assert series[key]["points"][2]["value"] == 1400
    assert series["Alpha"]["points"][2]["value"] == 700
    assert series["Alpha Basis"]["points"][2]["value"] == 490


def test_later_prices_do_not_change_a_selected_transit_snapshot(tmp_path):
    original = _data(110)
    changed = copy.deepcopy(original)
    changed["history"][-1] = _snapshot("2024-03-31", "after", 300)
    changed["holdings_by_account"] = changed["history"][-1]["positions"]
    driver = """
      asOfDate = '2024-01-20'; renderStats();
      process.stdout.write(JSON.stringify({holdings: asOfHoldingsByAccount(),
        stats: nodeFor('stats').innerHTML,
        returns: historicalGroupPerformance(new Set(['Alpha', 'Beta']), asOfDate)}));
    """
    assert run_js(tmp_path, original, driver) == run_js(tmp_path, changed, driver)
