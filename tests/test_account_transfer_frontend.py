"""Account-boundary flow consumers execute the shipped dashboard bundle.

All marks and balances here are independently fictional. Backend tests own
transfer pairing and valuation; these tests pin the exported annotation's
meaning across interactive returns, charts, cards, and legacy JSON.
"""
from __future__ import annotations

import calendar
import copy
import re

import pytest

from tests.test_frontend_review_regressions import payload, run_js


def _row(day, group, *, external=0, transfer=None):
    row = {"date": day, "account_group": group, "account_type": "Taxable",
           "symbol": "XYZ", "action": "Transfer In", "quantity": 3,
           "price": 100, "amount": 3, "cash_flow": external}
    if transfer is not None:
        other, flow = transfer
        row["account_transfer"] = {"counterparty_group": other, "flow": flow}
        if flow is not None and flow < 0:
            row["action"] = "Transfer Out"
    return row


def _snapshot(day, a, b):
    return {"date": day, "total": a + b, "net_contributed": 2000,
            "by_account_group": {"Alpha": a, "Beta": b},
            "by_account_type": {"Taxable": a + b}, "benchmark_spy_price": 100,
            "positions": [
                {"account_group": group, "account_type": "Taxable",
                 "symbol": "XYZ", "quantity": value / 100, "price": 100,
                 "value": value, "cost_basis": value, "unrealized_gain": 0}
                for group, value in [("Alpha", a), ("Beta", b)]]}


def _data(*, delayed=False):
    transactions = [
        _row("2023-12-31", "Alpha", external=1000),
        _row("2023-12-31", "Beta", external=1000),
        _row("2024-01-15", "Alpha", transfer=("Beta", -300)),
        _row("2024-01-20" if delayed else "2024-01-15", "Beta",
             transfer=("Alpha", 300)),
    ]
    snapshots = [_snapshot("2023-12-31", 1000, 1000)]
    if delayed:
        snapshots.append(_snapshot("2024-01-16", 700, 1000))
    snapshots += [_snapshot(day, 700, 1300)
                  for day in ["2024-01-31", "2024-02-29", "2024-03-31"]]
    data = payload(snapshots, transactions)
    data["holdings_by_account"] = snapshots[-1]["positions"]
    data["cash_summary"] = {"net_contributed": 2000}
    return data


@pytest.mark.parametrize("delayed", [False, True])
def test_scope_flow_excludes_counterparts_and_honors_transaction_dates(tmp_path, delayed):
    actual = run_js(tmp_path, _data(delayed=delayed), """
      const scopes = [null, new Set(['Alpha']), new Set(['Beta']),
        new Set(['Alpha', 'Beta']), new Set()];
      process.stdout.write(JSON.stringify({
        complete: scopes.map(s => _netFlowBetween('2023-12-31', '2024-01-31', s)),
        during: scopes.map(s => _netFlowBetween('2023-12-31', '2024-01-16', s)),
        after: scopes.map(s => _netFlowBetween('2024-01-20', '2024-02-29', s))}));
    """)
    assert actual["complete"] == [0, -300, 300, 0, 0]
    assert actual["during"] == [0, -300, 0 if delayed else 300, 0, 0]
    assert actual["after"] == [0, 0, 0, 0, 0]


def test_unresolved_and_legacy_annotations_preserve_external_verdicts(tmp_path):
    rows = [_row("2024-01-15", "Alpha", external=125),
            _row("2024-01-15", "Alpha", transfer=("Beta", None)),
            _row("2024-01-15", "Alpha"),
            _row("2024-01-15", "Alpha", external=50, transfer=("Beta", 300))]
    actual = run_js(tmp_path, payload(transactions=rows), """
      process.stdout.write(JSON.stringify(txns.map(t =>
        [null, new Set(['Alpha']), new Set(['Beta']), new Set()].map(s =>
          _txnCashFlowForGroups(t, s)))));
    """)
    assert actual == [[125, 125, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [50, 50, 0, 0]]


@pytest.mark.parametrize("delayed", [False, True])
def test_annual_custom_twr_and_historical_xirr_do_not_book_transfers_as_gains(tmp_path, delayed):
    actual = run_js(tmp_path, _data(delayed=delayed), """
      process.stdout.write(JSON.stringify(['Alpha', 'Beta'].map(group => ({
        annual: computeAnnualReturns(group).find(r => r.year === '2024'),
        twr: computeTimeWeightedReturnForWindow(group, '2023-12-31', '2024-03-31'),
        historical: historicalGroupPerformance(new Set([group]), '2024-03-31'),
      }))));
    """)
    for result, net in zip(actual, [-300, 300]):
        assert result["annual"]["net"] == net
        assert result["annual"]["dollar_return"] == 0
        assert result["annual"]["pct"] == 0
        assert result["twr"]["cumulative"] == pytest.approx(0)
        assert result["historical"]["money_weighted"]["annualized"] == pytest.approx(0, abs=1e-10)


def test_monthly_risk_matches_flat_investments_after_transfer_only_changes(tmp_path):
    data = _data()
    data["history"] = [_snapshot("2023-12-31", 1000, 1000)]
    data["transactions"] = data["transactions"][:2]
    for month in range(1, 7):
        day = f"2024-{month:02}-15"
        data["transactions"] += [_row(day, "Alpha", transfer=("Beta", -100)),
                                 _row(day, "Beta", transfer=("Alpha", 100))]
        data["history"].append(_snapshot(
            f"2024-{month:02}-{calendar.monthrange(2024, month)[1]}",
            1000 - month * 100, 1000 + month * 100))
    actual = run_js(tmp_path, data, """
      process.stdout.write(JSON.stringify(['Alpha', 'Beta'].map(group =>
        computeWindowedMetrics(group, 'lifetime'))));
    """)
    for result in actual:
        assert result["nMonths"] == 6
        assert result["nInRatio"] == 6
        assert result["cum"] == 0
        assert result["sharpe"] is None
        assert result["sortino"] == pytest.approx(-3.464)


def test_filtered_benchmark_and_type_membership_include_account_capital(tmp_path):
    actual = run_js(tmp_path, _data(), """
      const scopes = [null, new Set(['Alpha']), new Set(['Beta']), new Set(['Alpha', 'Beta'])];
      const series = scopes.map(s => _simulateFilteredBenchmark(s, history, 'benchmark_spy_price'));
      historySelection.clear(); historySelection.add('type:Taxable');
      const typeGroups = [..._activeBenchFilterGroups()].sort();
      historySelection.clear(); historySelection.add('account:Alpha');
      process.stdout.write(JSON.stringify({series, typeGroups,
        accountGroups: [..._activeBenchFilterGroups()]}));
    """)
    assert [series[-1]["value"] for series in actual["series"]] == [2000, 700, 1300, 2000]
    assert actual["typeGroups"] == ["Alpha", "Beta"]
    assert actual["accountGroups"] == ["Alpha"]


def _card(html, label):
    matches = re.findall(
        r'<div class="label">' + label + r'.*?</div>\s*<div class="value">(.*?)</div>',
        html, re.DOTALL)
    return re.sub(r"<[^>]+>", "", matches[-1]).strip()


def test_performance_cards_and_contribution_chart_use_the_same_scope(tmp_path):
    actual = run_js(tmp_path, _data(), """
      const captured = [];
      const originalSimulation = _simulateFilteredBenchmark;
      _simulateFilteredBenchmark = (...args) => {
        const result = originalSimulation(...args);
        captured.push(result); return result;
      };
      const charts = [];
      const originalChart = renderMultiLineChart;
      renderMultiLineChart = (...args) => { charts.push(args); return originalChart(...args); };
      performanceAccountFilter = 'Beta'; renderPerformance();
      process.stdout.write(JSON.stringify({html: NODES.get('performanceContent').innerHTML,
        captured, charts, errors}));
    """)
    assert not actual["errors"]
    assert _card(actual["html"], "Net Contributed") == "$1,300.00"
    assert _card(actual["html"], "Total Return").startswith("+$0.00")
    assert actual["captured"][0][-1]["value"] == 1300
    # The chart arguments include the cumulative capital line, independent of
    # the benchmark series. Its ending value must match the adjacent card.
    contribution_lines = [series for args in actual["charts"] for series in args[0]
                          if series.get("label", "").startswith("Net Contributed")]
    assert contribution_lines
    assert contribution_lines[-1]["points"][-1]["value"] == 1300


def test_overview_account_capital_and_ledger_columns_do_not_expose_metadata(tmp_path):
    data = _data()
    # Put a marked row first to exercise dynamic columns, whose source is the
    # first transaction. A nested metadata object is not a ledger field.
    data["transactions"] = data["transactions"][2:] + data["transactions"][:2]
    actual = run_js(tmp_path, data, """
      _annualExpanded.add('Alpha'); _annualExpanded.add('Beta');
      renderAnnualBreakdown();
      process.stdout.write(JSON.stringify({html: NODES.get('annualBreakdown').innerHTML,
        columns, filters: FILTER_FIELDS, errors}));
    """)
    assert not actual["errors"]
    assert "account_transfer" not in actual["columns"]
    assert "account_transfer" not in actual["filters"]
    row = re.search(r'<th scope="row" class="ab-sticky-col">2024</th>(.*?)</tr>',
                    actual["html"], re.DOTALL).group(1)
    contribution_cells = re.findall(r'<td class="num ab-contrib[^>]*>(.*?)</td>', row)
    assert contribution_cells == ["−$300", "$700", "−$300", "+$300", "$1.3k", "+$300"]


def test_later_transfer_does_not_move_historical_account_xirr(tmp_path):
    data = _data()
    before = run_js(tmp_path, data, """
      process.stdout.write(JSON.stringify(historicalGroupPerformance(new Set(['Alpha']), '2024-01-31')));
    """)
    later = copy.deepcopy(data)
    later["transactions"].append(_row("2024-03-15", "Alpha", transfer=("Beta", -200)))
    later["history"][-1] = _snapshot("2024-03-31", 500, 1500)
    after = run_js(tmp_path, later, """
      process.stdout.write(JSON.stringify(historicalGroupPerformance(new Set(['Alpha']), '2024-01-31')));
    """)
    assert after == before


def test_account_capital_without_history_uses_the_artifact_date(tmp_path):
    data = payload(transactions=[
        _row("2024-01-15", "Alpha", external=1000),
        _row("2024-01-15", "Beta", external=1000),
        _row("2025-01-15", "Alpha", external=500),
    ])
    data["holdings_by_account"] = _snapshot("2024-12-31", 1000, 1000)["positions"]
    actual = run_js(tmp_path, data, """
      performanceAccountFilter = 'Alpha'; renderPerformance();
      process.stdout.write(JSON.stringify({html: NODES.get('performanceContent').innerHTML,
        errors}));
    """)
    assert not actual["errors"]
    # The benchmark chart has a separate contribution summary with no points
    # when history is empty; inspect the account/window capital card itself.
    assert _card(actual["html"], r'Net Contributed <span class="sub">') == "$1,000.00"
    assert _card(actual["html"], "Total Return").startswith("+$0.00")
