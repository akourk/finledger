"""History presets preserve visible scope and the user's custom boundaries."""
from tests.test_frontend_review_regressions import payload, run_js


def history_payload():
    return payload([
        {'date': day, 'total': value, 'net_contributed': 80,
         'total_cost_basis': 80, 'positions': [], 'by_account_group': {}}
        for day, value in [('2023-12-31', 100), ('2024-03-31', 110),
                           ('2024-06-30', 120), ('2024-12-31', 130)]
    ])


def test_first_custom_range_starts_from_visible_preset(tmp_path):
    result = run_js(tmp_path, history_payload(), """
      setHistoryRange('ytd');
      setHistoryRange('custom');
      process.stdout.write(JSON.stringify({start: historyCustomStart,
        end: historyCustomEnd, dates: filteredHistory().map(row => row.date), errors}));
    """)
    assert result['errors'] == []
    assert result['start'] == '2024-03-31'
    assert result['end'] == '2024-12-31'
    assert result['dates'] == ['2024-03-31', '2024-06-30', '2024-12-31']


def test_custom_bounds_survive_a_visit_to_a_preset(tmp_path):
    result = run_js(tmp_path, history_payload(), """
      setHistoryCustomStart('2024-04-01');
      setHistoryCustomEnd('2024-09-30');
      setHistoryRange('lifetime');
      setHistoryRange('custom');
      process.stdout.write(JSON.stringify({start: historyCustomStart,
        end: historyCustomEnd, dates: filteredHistory().map(row => row.date), errors}));
    """)
    assert result['errors'] == []
    assert (result['start'], result['end']) == ('2024-04-01', '2024-09-30')
    assert result['dates'] == ['2024-06-30']


def test_balance_shortcut_restores_two_distinct_series_without_changing_dates(tmp_path):
    result = run_js(tmp_path, history_payload(), """
      setHistoryRange('ytd');
      historySelection.clear();
      historyOverlays.add('basis');
      seriesHidden.add('Total');
      setHistoryQuickView('composition');
      setHistoryQuickView('balance');
      process.stdout.write(JSON.stringify({range: historyRange, mode: historyChartMode,
        hidden: [...seriesHidden], series: buildHistorySeries().map(row =>
          ({name: row.key, values: row.points.map(point => point.value)})), errors}));
    """)
    assert result['errors'] == []
    assert result['range'] == 'ytd'
    assert result['mode'] == 'lines'
    assert result['hidden'] == []
    assert result['series'] == [
        {'name': 'Total', 'values': [110, 120, 130]},
        {'name': 'Net Contributed', 'values': [80, 80, 80]},
    ]
