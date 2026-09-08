"""Balance declines retain cash-flow effects and describe them honestly.

These independently fictional balances exercise Python and the shipped JS
bundle without invoking the pipeline or reading price caches.
"""
from __future__ import annotations

from datetime import date, timedelta
import json
import re

import pytest

from tests.test_frontend_review_regressions import payload, run_js


def _history(values, dates=None):
    dates = dates or ["2024-01-31", "2024-02-29", "2024-03-31"]
    return [
        {"date": day, "total": value, "by_account_group": {"Example": value}}
        for day, value in zip(dates, values)
    ]


def _flow(action, amount, day="2024-02-29", *, group="Example"):
    from src.basis import txn_external_cash_flow

    row = {"date": day, "account_group": group, "action": action,
           "symbol": "USD", "quantity": amount, "amount": amount}
    row["cash_flow"] = txn_external_cash_flow(row)
    return row


@pytest.mark.parametrize("scenario,values,expected_max,expected_current", [
    ("withdrawal", [1000, 600], -0.4, -0.4),
    ("deposit_recovery", [1000, 800, 1800], -0.2, 0.0),
    ("income", [1000, 1100], 0.0, 0.0),
    ("fee", [1000, 990], -0.01, -0.01),
    ("internal_transfer", [1000, 1000], 0.0, 0.0),
    ("rollover", [1000, 0, 1000], 0.0, 0.0),
    ("wipeout", [1000, 0], -1.0, -1.0),
])
def test_balance_drawdown_keeps_cash_flow_effects_and_real_losses(
        tmp_path, scenario, values, expected_max, expected_current):
    from src.analytics.drawdown import compute_drawdown
    from src.analytics._shared import compute_twr_summary

    history = _history(values)
    bridges = []
    transactions = []
    if scenario == "withdrawal":
        transactions = [_flow("Withdrawal", 400)]
    elif scenario == "deposit_recovery":
        transactions = [_flow("Contribution", 1000, "2024-03-31")]
    elif scenario == "income":
        transactions = [_flow("Dividend", 100)]
    elif scenario == "fee":
        transactions = [_flow("Fee", 10)]
    elif scenario == "internal_transfer":
        transactions = [_flow("Transfer Out", 500), _flow("Transfer In", 500)]
    elif scenario == "rollover":
        bridges = [{"group": "Example", "start_date": "2024-02-15",
                    "end_date": "2024-03-15", "amount": 1000}]

    expected = compute_drawdown(history, bridges)
    assert expected["max_drawdown"] == pytest.approx(expected_max)
    assert expected["current_drawdown_pct"] == pytest.approx(expected_current)
    data = payload(history, transactions)
    data["analytics"] = {"drawdown": expected, "rollover_bridges": bridges}
    actual = run_js(tmp_path, data, """
      const whole = computeWindowedMetrics(null, 'lifetime');
      const account = computeWindowedMetrics('Example', 'lifetime');
      process.stdout.write(JSON.stringify({whole, account, errors}));
    """)
    assert not actual["errors"]
    for metrics in (actual["whole"], actual["account"]):
        assert metrics["mdd"] == pytest.approx(expected_max * 100)

    if scenario in ("withdrawal", "deposit_recovery"):
        twr = compute_twr_summary(transactions, history, [], None)
        expected_return = 0.0 if scenario == "withdrawal" else -0.2
        assert twr["cumulative"] == pytest.approx(expected_return)
        assert actual["whole"]["cum"] == pytest.approx(expected_return)
        if scenario == "deposit_recovery":
            assert expected["max_drawdown_window"]["recovery_date"] == "2024-03-31"


def _labels(html):
    return [re.sub(r"<[^>]+>", "", label).strip()
            for label in re.findall(r'<div class="(?:label|ds-label)">(.*?)</div>',
                                    html, flags=re.DOTALL)]


def test_all_rendered_drawdowns_name_balance_and_calmar_is_absent(tmp_path):
    from src.analytics.drawdown import compute_drawdown

    history = _history([1000, 600, 600])
    data = payload(history, [_flow("Withdrawal", 400)])
    data["analytics"]["drawdown"] = compute_drawdown(history)
    actual = run_js(tmp_path, data, """
      renderPerformance(); renderStats();
      const performance = NODES.get('performanceContent').innerHTML;
      const current = NODES.get('stats').innerHTML;
      asOfDate = '2024-02-29'; renderStats();
      process.stdout.write(JSON.stringify({performance, current,
        historical: NODES.get('stats').innerHTML,
        metrics: computeWindowedMetrics(null, 'lifetime'), errors}));
    """)
    assert not actual["errors"]
    assert "calmar" not in actual["metrics"]
    assert "calmar" not in actual["performance"].lower()
    for surface in ("performance", "current", "historical"):
        drawdown_labels = [label for label in _labels(actual[surface])
                           if "drawdown" in label.lower()]
        assert drawdown_labels, surface
        assert all("balance drawdown" in label.lower() for label in drawdown_labels)
    assert "-40.00%" in actual["current"]
    assert "-40.00%" in actual["historical"]


def test_zero_windowed_balance_drawdown_is_displayed_as_zero(tmp_path):
    from src.analytics.drawdown import compute_drawdown

    history = _history([1000, 1100])
    data = payload(history)
    data["analytics"]["drawdown"] = compute_drawdown(history)
    actual = run_js(tmp_path, data, """
      renderPerformance();
      process.stdout.write(JSON.stringify({html: NODES.get('performanceContent').innerHTML,
        metrics: computeWindowedMetrics(null, 'lifetime'), errors}));
    """)
    assert not actual["errors"]
    assert actual["metrics"]["mdd"] == 0
    assert re.search(
        r'<div class="label">Max Balance Drawdown\b.*?</div>'
        r'<div class="value">0\.00%', actual["html"], re.DOTALL)


@pytest.mark.parametrize("window,days", [
    ("30day", 30), ("3mo", 91), ("6mo", 183), ("1y", 365),
    ("2y", 730), ("3y", 1095), ("5y", 1825),
])
def test_trailing_windows_share_inclusive_boundaries_and_ignore_wall_clock(
        tmp_path, monkeypatch, window, days):
    from src.analytics.drawdown import compute_drawdown

    latest = date(2024, 12, 31)
    cutoff = latest - timedelta(days=days)
    dates = [(cutoff - timedelta(days=1)).isoformat(), cutoff.isoformat(),
             (cutoff + timedelta(days=1)).isoformat(), latest.isoformat()]
    history = _history([2000, 1000, 800, 900], dates)
    answers = []
    for wall_date in ("2024-12-31", "2028-08-15"):
        monkeypatch.setenv("FIN_AS_OF_DATE", wall_date)
        result = compute_drawdown(history)["windowed"][window]
        assert result["n_snapshots"] == 3
        assert result["magnitude_pct"] == -20.0
        assert result["peak_date"] == cutoff.isoformat()
        assert result["trough_date"] == dates[2]
        answers.append(result)
    assert answers[0] == answers[1]
    actual = run_js(tmp_path, payload(history), """
      process.stdout.write(JSON.stringify({
        cutoff: _windowCutoffIso(WINDOW, history[history.length - 1].date),
        metrics: computeWindowedMetrics(null, WINDOW)}));
    """.replace("WINDOW", json.dumps(window)))
    assert actual["cutoff"] == cutoff.isoformat()
    assert actual["metrics"]["mdd"] == -20.0
    assert actual["metrics"]["mddPeak"] == cutoff.isoformat()
    assert actual["metrics"]["mddTrough"] == dates[2]


def test_daily_window_anchors_to_latest_daily_observation(monkeypatch):
    from src.analytics.drawdown import compute_drawdown

    monkeypatch.setenv("FIN_AS_OF_DATE", "2030-01-01")
    history = _history([1000, 800], ["2024-01-01", "2024-01-31"])
    daily = [("2024-01-01", 2000), ("2024-01-02", 1000),
             ("2024-01-03", 800), ("2024-02-01", 900)]
    result = compute_drawdown(history, daily_totals=daily)
    window = result["windowed"]["30day"]
    assert result["resolution"] == "daily"
    assert window["n_snapshots"] == 3
    assert window["magnitude_pct"] == -20.0
    assert window["peak_date"] == "2024-01-02"


def test_historical_balance_drawdown_excludes_future_recovery(tmp_path):
    from src.analytics.drawdown import compute_drawdown

    history = _history([1000, 600, 1600])
    data = payload(history, [_flow("Withdrawal", 400),
                             _flow("Contribution", 1000, "2024-03-31")])
    data["analytics"]["drawdown"] = compute_drawdown(history)
    actual = run_js(tmp_path, data, """
      asOfDate = '2024-02-29'; renderStats();
      process.stdout.write(JSON.stringify(NODES.get('stats').innerHTML));
    """)
    assert "-40.00%" in actual
    assert any("balance drawdown" in label.lower() for label in _labels(actual))
