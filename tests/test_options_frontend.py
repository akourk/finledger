"""Options figures retain their units and scope in the shipped dashboard.

All activity below is independently fictional. The actual basis walker and
Python export feed the real concatenated JavaScript bundle; no personal files
or market prices are needed.
"""
from __future__ import annotations

import copy
import html as html_lib
import json
import re

import pytest

from tests.test_account_transfer_frontend import _card as _find_card
from tests.test_frontend_review_regressions import payload, run_js


CALL = "XYZ 12/20/2024 Call $50.00"
PUT = "XYZ 12/20/2024 Put $45.00"
EVEN = "XYZ 12/20/2024 Call $60.00"
PART = "PART 12/20/2024 Call $20.00"


def _txn(day, account, symbol, action, quantity, amount):
    return {"date": day, "account_group": account, "account": account,
            "account_type": "Taxable", "symbol": symbol, "action": action,
            "quantity": quantity, "amount": amount,
            "price": amount / quantity / 100, "fees": 0,
            "description": "Fictional option activity", "source": "test"}


def _export(rows):
    from src.analytics.options import compute_options_analytics
    from src.basis import compute_basis_default

    rows = copy.deepcopy(rows)
    compute_basis_default(rows)
    data = payload(transactions=rows)
    data["as_of"] = "2024-06-30"
    data["generated"] = "2024-06-30T12:00:00"
    data["analytics"]["options"] = compute_options_analytics(rows)
    # Undefined ratios must remain JSON null, including the Python side.
    json.dumps(data, allow_nan=False)
    return data


def _data():
    return _export([
        _txn("2023-11-01", "Alpha", CALL, "Option Buy", 2, 200),
        _txn("2023-12-15", "Alpha", CALL, "Option Sell", 1, 150),
        _txn("2024-02-01", "Alpha", CALL, "Option Sell", 1, 70),
        _txn("2024-03-01", "Alpha", PUT, "Option Buy", 1, 100),
        _txn("2024-03-02", "Alpha", PUT, "Option Sell", 1, 60),
        _txn("2024-04-01", "Alpha", EVEN, "Option Buy", 1, 80),
        _txn("2024-04-01", "Alpha", EVEN, "Option Sell", 1, 80),
        _txn("2024-01-01", "Beta", CALL, "Option Buy", 2, 200),
        _txn("2024-03-02", "Beta", CALL, "Option Sell", 2, 260),
        _txn("2024-04-10", "Alpha", PART, "Option Buy", 3, 300),
        _txn("2024-04-20", "Alpha", PART, "Option Sell", 1, 90),
        _txn("2024-05-01", "Gamma", CALL, "Option Buy", 3, 300),
    ])


def _scope(tmp_path, data, date_range=None, account=None):
    return run_js(tmp_path, data, """
      process.stdout.write(JSON.stringify(_optionsScope(ANALYTICS.options,
    """ + json.dumps(date_range) + ", " + json.dumps(account) + ")));" )


def _same(actual, expected):
    """Compare public values recursively without rounding away parity failures."""
    if isinstance(expected, dict):
        for key, value in expected.items():
            assert key in actual, f"Missing exported field {key}"
            _same(actual[key], value)
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for a, e in zip(actual, expected):
            _same(a, e)
    elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
        assert actual == pytest.approx(expected, abs=1e-9)
    else:
        assert actual == expected


def _render(tmp_path, data, driver="renderOptions();"):
    result = run_js(tmp_path, data, driver + """
      process.stdout.write(JSON.stringify({html: nodeFor('optionsContent').innerHTML, errors}));
    """)
    assert not result["errors"]
    assert not re.search(r"\b(?:NaN|undefined|Infinity)\b", result["html"])
    return result["html"]


def _table(html, caption):
    found = re.search(r'<table[^>]*>\s*<caption[^>]*>' + re.escape(caption)
                      + r'</caption>.*?</table>', html, re.S)
    assert found is not None, f"Missing table: {caption}"
    return found.group()


def _metric(html, label):
    found = re.search(r'<dt>' + re.escape(label) + r'</dt>\s*<dd>(.*?)</dd>',
                      html_lib.unescape(html), re.S)
    assert found is not None, f"Missing metric: {label}"
    return re.sub(r"<[^>]+>", "", found[1]).strip()


def _card(html, label):
    return _find_card(html_lib.unescape(html), re.escape(label))


def test_lifetime_dashboard_matches_the_python_export_with_distinct_units(tmp_path):
    data = _data()
    opt = data["analytics"]["options"]
    result = _scope(tmp_path, data)
    # These counts deliberately differ: four positions, six executions,
    # seven closed contracts, two open positions, and five open contracts.
    assert len(result["completed"]) == 4
    assert len(result["closed"]) == 6
    assert len(result["open"]) == 2
    assert result["stats"]["trades"] == 4
    assert result["stats"]["win_rate"] == pytest.approx(2 / 3)
    assert result["stats"]["total_pnl"] == 40
    assert result["realizedPnl"] == 30
    assert result["contractStats"]["contracts"] == 7
    assert result["contractStats"]["win_rate"] == 0.5
    for key in ("total_pnl", "trades", "wins", "losses", "breakevens",
                "win_rate", "avg_hold_days", "biggest_winner", "biggest_loser",
                "profit_factor", "expectancy", "avg_winner", "avg_loser",
                "call", "put"):
        _same(result["stats"][key], opt["stats"][key])
    _same(result["contractStats"], opt["contract_stats"])
    _same(result["byUnderlying"], opt["by_underlying"])
    _same(result["annual"], opt["annual_summary"])
    _same(result["cumulative"], opt["cumulative_pnl"])


@pytest.mark.parametrize("date_range,account,count,completed_pnl,realized,open_qty", [
    (None, "Alpha", 3, -20, -30, 2),
    (["2024-01-01", "2024-12-31"], "Alpha", 3, -20, -80, 2),
    (["2023-01-01", "2023-12-31"], "Alpha", 0, 0, 50, 2),
    (["2024-03-02", "2024-03-02"], None, 2, 20, 20, 5),
    (None, "Gamma", 0, 0, 0, 3),
    (None, "Missing", 0, 0, 0, 0),
])
def test_date_and_account_scope_keep_completions_and_realizations_separate(
        tmp_path, date_range, account, count, completed_pnl, realized, open_qty):
    from src.analytics.options import _contract_stats, _position_stats

    data = _data()
    opt = data["analytics"]["options"]

    def selected(row):
        return ((account is None or row["account_group"] == account)
                and (date_range is None or date_range[0] <= row["close_date"] <= date_range[1]))

    expected_positions = [row for row in opt["completed_positions"] if selected(row)]
    expected_closes = [row for row in opt["closed_trades"] if selected(row)]
    result = _scope(tmp_path, data, date_range, account)
    assert result["stats"]["trades"] == count
    assert result["stats"]["total_pnl"] == completed_pnl
    assert result["realizedPnl"] == realized
    assert sum(row["qty"] for row in result["open"]) == open_qty
    _same(result["stats"], _position_stats(expected_positions))
    _same(result["contractStats"], _contract_stats(expected_closes))
    assert sum(row["realized"] for row in result["byUnderlying"]) == realized
    assert sum(row["completed_pnl"] for row in result["annual"]) == completed_pnl
    if result["cumulative"]:
        assert result["cumulative"][-1]["value"] == realized


def test_annual_groups_include_realization_only_years_and_consistent_breakevens(tmp_path):
    result = _scope(tmp_path, _data())
    annual = {row["year"]: row for row in result["annual"]}
    assert annual["2023"]["realized"] == 50
    assert annual["2023"]["trades"] == 0
    assert annual["2023"]["completed_pnl"] == 0
    assert annual["2023"]["win_rate"] is None
    assert annual["2024"]["realized"] == -20
    assert annual["2024"]["completed_pnl"] == 40
    underlying = {row["underlying"]: row for row in result["byUnderlying"]}
    for row in (result["stats"], annual["2024"], underlying["XYZ"]):
        assert (row["wins"], row["losses"], row["breakevens"]) == (2, 1, 1)
        assert row["win_rate"] == pytest.approx(2 / 3)
    assert underlying["PART"]["trades"] == 0
    assert underlying["PART"]["realized"] == -10
    assert underlying["PART"]["win_rate"] is None


@pytest.mark.parametrize("separate_days", [False, True])
def test_splitting_closes_does_not_reweight_completed_positions_or_contracts(tmp_path, separate_days):
    base = [
        _txn("2024-01-01", "Example", CALL, "Option Buy", 4, 400),
        _txn("2024-01-01", "Example", PUT, "Option Buy", 1, 160),
        _txn("2024-01-10", "Example", PUT, "Option Sell", 1, 100),
    ]
    batched = _export(base + [_txn("2024-01-13", "Example", CALL, "Option Sell", 4, 480)])
    split = _export(base + [
        _txn(f"2024-01-{10 + i if separate_days else 13:02}", "Example", CALL,
             "Option Sell", 1, 120) for i in range(4)
    ])
    answers = [_scope(tmp_path, data) for data in (batched, split)]
    for result in answers:
        assert result["stats"]["trades"] == 2
        assert result["stats"]["win_rate"] == 0.5
        assert result["stats"]["avg_winner"] == 80
        assert result["stats"]["avg_loser"] == -60
        assert result["stats"]["expectancy"] == 10
        assert result["contractStats"]["win_rate"] == 0.8
        assert result["realizedPnl"] == 20
    for key in ("total_pnl", "trades", "wins", "losses", "breakevens", "win_rate",
                "avg_hold_days", "biggest_winner", "biggest_loser", "profit_factor",
                "expectancy", "avg_winner", "avg_loser", "call", "put"):
        _same(answers[0]["stats"][key], answers[1]["stats"][key])
    _same(answers[0]["contractStats"], answers[1]["contractStats"])
    assert len(answers[0]["closed"]) == 2
    assert len(answers[1]["closed"]) == 5
    if not separate_days:
        assert answers[0]["cumulative"] == answers[1]["cumulative"]
        assert len({point["date"] for point in answers[1]["cumulative"]}) == len(answers[1]["cumulative"])


def test_one_breakeven_execution_can_contain_winning_and_losing_contracts(tmp_path):
    data = _export([
        _txn("2024-01-01", "Example", CALL, "Option Buy", 1, 100),
        _txn("2024-01-02", "Example", CALL, "Option Buy", 1, 200),
        _txn("2024-01-03", "Example", CALL, "Option Sell", 2, 300),
    ])
    result = _scope(tmp_path, data)
    assert result["stats"]["breakevens"] == 1
    assert result["stats"]["win_rate"] is None
    assert result["contractStats"]["wins"] == 1
    assert result["contractStats"]["losses"] == 1
    assert result["contractStats"]["breakevens"] == 0
    assert result["contractStats"]["win_rate"] == 0.5
    assert result["stats"]["expectancy"] == 0


@pytest.mark.parametrize("direction", [1, -1])
def test_derived_half_cent_averages_keep_precision_until_display(tmp_path, direction):
    data = _export([
        _txn("2024-03-01", "Example", CALL, "Option Buy", 1, 100),
        _txn("2024-03-01", "Example", PUT, "Option Buy", 1, 100),
        _txn("2024-03-02", "Example", CALL, "Option Sell", 1, 100 + direction),
        _txn("2024-03-02", "Example", PUT, "Option Sell", 1, 100 + 1.25 * direction),
    ])
    opt = data["analytics"]["options"]
    average_field = "avg_winner" if direction > 0 else "avg_loser"
    assert opt["stats"][average_field] == 1.125 * direction
    assert opt["stats"]["expectancy"] == 1.125 * direction
    for account in (None, "Example"):
        scoped = _scope(tmp_path, data, account=account)
        _same(scoped["stats"], opt["stats"])
        driver = "renderOptions();" if account is None else "_setOptAccountFilter('Example');"
        html = _render(tmp_path, data, driver)
        outcomes = html.split("<h3>Completed-position outcomes</h3>", 1)[1].split("</div>", 1)[0]
        displayed = ("+" if direction > 0 else "-") + "$1.13"
        assert _metric(outcomes, "Avg P&L / Position") == displayed
        assert _metric(outcomes, "Avg Winner" if direction > 0 else "Avg Loser") == displayed


def test_missing_lot_coverage_is_explicit_and_never_inferred_from_execution_profit(tmp_path):
    data = _data()
    opt = data["analytics"]["options"]
    close = copy.deepcopy(opt["closed_trades"][0])
    close.update(qty=3, realized=90, lot_coverage_complete=False,
                 lot_outcomes=[{"qty": 1, "realized": 30, "outcome": "win"},
                               {"qty": 2, "realized": None, "outcome": "unknown"}])
    opt["closed_trades"] = [close]
    opt.pop("contract_stats", None)
    result = _scope(tmp_path, data)
    contract = result["contractStats"]
    assert contract["contracts"] == 3
    assert contract["wins"] == 1
    assert contract["losses"] == 0
    assert contract["unknown"] == 2
    assert contract["coverage_complete"] is False
    assert contract["win_rate"] == 1, "incomplete coverage uses explicitly labeled known outcomes"


def test_missing_realized_annotation_stays_unavailable_in_cards_tables_and_chart(tmp_path):
    from src.analytics.options import compute_options_analytics

    data = _data()
    next(row for row in data["transactions"] if row["action"] == "Option Sell").pop("realized_gain")
    data["analytics"]["options"] = compute_options_analytics(data["transactions"])
    result = _scope(tmp_path, data)
    opt = data["analytics"]["options"]
    assert result["realizedPnl"] is None
    assert result["stats"]["call"]["realized_pnl"] is None
    assert result["stats"]["put"]["realized_pnl"] == -40
    assert {r["underlying"]: r for r in result["byUnderlying"]}["XYZ"]["realized"] is None
    assert all(point["value"] is None for point in result["cumulative"])
    _same(result["byUnderlying"], opt["by_underlying"])
    _same(result["cumulative"], opt["cumulative_pnl"])
    html = _render(tmp_path, data)
    assert _card(html, "Realized P&L") == "—"


def test_legacy_execution_export_does_not_masquerade_as_completed_positions(tmp_path):
    data = _data()
    opt = data["analytics"]["options"]
    opt.pop("completed_positions")
    opt.pop("contract_stats")
    for row in opt["closed_trades"]:
        row.pop("lot_outcomes", None)
        row.pop("lot_coverage_complete", None)
    result = _scope(tmp_path, data)
    assert result["completed"] == []
    assert result["stats"]["trades"] == 0
    assert result["stats"]["win_rate"] is None
    assert result["realizedPnl"] == 30
    assert result["contractStats"]["unknown"] == 7
    assert result["contractStats"]["win_rate"] is None
    html = _render(tmp_path, data)
    assert re.search(r"(?:regenerate|rebuild|older export|unavailable)", html, re.I)
    assert _card(html, "Position Win Rate") == "—"
    assert _card(html, "Completed Positions") == "—"
    assert _card(html, "Completed Position P&L") == "—"
    assert _metric(html, "Completed Positions") == "—"
    assert _metric(html, "Wins / Losses / Breakevens") == "—"
    assert "Completed positions (unavailable)" in html
    assert "Completed option positions</caption>" not in html
    for caption in ("Option profit and loss by underlying",
                    "Option outcomes and realized profit and loss by year"):
        rows = re.findall(r"<tr>(.*?)</tr>", _table(html, caption), re.S)[1:]
        assert rows
        for row in rows:
            cells = [re.sub(r"<[^>]+>", "", cell) for cell in
                     re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            assert cells[1:4] == ["—", "—", "—"]
            assert cells[4] != "—"  # Execution-date P&L remains available.


def test_rendered_cards_and_tables_identify_positions_contracts_and_executions(tmp_path):
    html = _render(tmp_path, _data())
    assert _card(html, "Completed Positions") == "4"
    assert _card(html, "Open Contracts") == "5"
    assert _card(html, "Realized P&L") == "+$30.00"
    assert "66.7" in _card(html, "Position Win Rate")
    assert "50.0" in _card(html, "Contract Win Rate")
    assert "Completed P&L" in html_lib.unescape(html)
    assert "Closing executions" in html
    _table(html, "Completed option positions")
    _table(html, "Option closing executions")
    _table(html, "Open option positions")


def test_account_with_only_open_positions_is_selectable_and_date_does_not_hide_it(tmp_path):
    html = _render(tmp_path, _data(), "_setOptWindow('2023'); _setOptAccountFilter('Gamma');")
    assert re.search(r'<button[^>]*aria-pressed="true"[^>]*>Gamma</button>', html)
    assert _card(html, "Completed Positions") == "0"
    assert _card(html, "Open Contracts") == "3"
    assert _card(html, "Position Win Rate") == "—"
    opened = _table(html, "Open option positions")
    assert "Gamma" in opened and "Alpha" not in opened and "Beta" not in opened
    assert "2024-05-01" in opened
    assert re.search(r"(?:latest|current)", html, re.I)


def test_same_day_holding_duration_renders_as_zero_and_no_data_ratios_are_unavailable(tmp_path):
    data = _export([
        _txn("2024-04-01", "Example", CALL, "Option Buy", 1, 100),
        _txn("2024-04-01", "Example", CALL, "Option Sell", 1, 100),
    ])
    html = _render(tmp_path, data)
    assert _card(html, "Position Win Rate") == "—"
    assert _card(html, "Contract Win Rate") == "—"
    assert _metric(html, "Avg Position Days").startswith("0")
    assert _metric(html, "Avg P&L / Position") == "+$0.00"
    table = _table(html, "Completed option positions")
    assert re.search(r">0(?:\.0)?(?: days|d)?</td>", table)


def test_options_imported_accounts_and_symbols_are_escaped_in_all_rendered_views(tmp_path):
    attack = 'Example\\"\'><img src=x onerror=alert(1)> & &#39;'
    data = _data()
    opt = data["analytics"]["options"]
    for key in ("completed_positions", "closed_trades", "open_contracts"):
        for row in opt[key]:
            row["account_group"] = attack
            row["underlying"] = attack
            row["symbol"] = attack + " OPTION"
    html = _render(tmp_path, data)
    assert "<img " not in html
    assert "&lt;img " in html
    assert "&amp;#39;" in html


def test_empty_options_export_has_finite_unavailable_stats(tmp_path):
    data = _export([])
    result = _scope(tmp_path, data)
    assert result["stats"]["trades"] == 0
    assert result["stats"]["win_rate"] is None
    assert result["stats"]["expectancy"] is None
    assert result["contractStats"]["win_rate"] is None
    assert result["realizedPnl"] == 0
    _render(tmp_path, data)


@pytest.mark.parametrize("has_unmatched_close", [False, True])
def test_unmatched_execution_keeps_options_navigation_available(tmp_path, monkeypatch, has_unmatched_close):
    import src.dashboard as dashboard

    data = _export([_txn("2024-03-01", "Example", CALL, "Option Sell", 1, 70)]
                   if has_unmatched_close else [])
    assert data["analytics"]["options"]["completed_positions"] == []
    assert data["analytics"]["options"]["open_contracts"] == []
    bundle = dashboard._read_app_js()
    # Let the existing DOM probe expose this real navigation button before
    # bundle initialization, so both the hidden and visible cases are observed.
    setup = '''document.querySelector = selector =>
      selector === '.tab-btn[data-tab="options"]' ? nodeFor('tabbtn-options') : null;\n'''
    monkeypatch.setattr(dashboard, "_read_app_js", lambda: setup + bundle)
    result = run_js(tmp_path, data, """
      process.stdout.write(JSON.stringify({display: nodeFor('tabbtn-options').style.display || '', errors}));
    """)
    assert not result["errors"]
    assert (result["display"] != "none") is has_unmatched_close


def test_cumulative_chart_uses_calendar_spacing_and_changes_only_on_close_dates(tmp_path):
    result = run_js(tmp_path, payload(), """
      const points = [{date:'2024-01-01',value:10}, {date:'2024-01-02',value:15},
        {date:'2024-01-11',value:5}];
      const calendar = _buildMiniLineChartContent(points, 800, 240, '#ffffff', String, 'dated', true).content;
      const regular = _buildMiniLineChartContent(points, 800, 240, '#ffffff', String, 'regular').content;
      process.stdout.write(JSON.stringify({calendar, regular}));
    """)
    dated_path = re.search(r'<path class="series-line" d="([^"]+)"', result["calendar"])[1]
    # First interval is one day, second is nine days; the line remains flat
    # between realized events instead of implying daily accrual of gains.
    start_x = float(re.match(r"M([\d.]+),", dated_path)[1])
    next_x, last_x = map(float, re.findall(r"H([\d.]+)", dated_path))
    assert (next_x - start_x) / (last_x - start_x) == pytest.approx(0.1)
    assert dated_path.count(" V") == 2
    assert "2024-01-01" in result["calendar"]
    regular_path = re.search(r'<path class="series-line" d="([^"]+)"', result["regular"])[1]
    regular_x = [float(value) for value in re.findall(r"[ML]([\d.]+),", regular_path)]
    assert (regular_x[1] - regular_x[0]) / (regular_x[2] - regular_x[0]) == 0.5
