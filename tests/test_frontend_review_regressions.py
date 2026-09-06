"""Synthetic regressions for the portfolio review's frontend failure modes.

Execute the shipped bundle, rather than copies of the calculation functions.
Browser interaction tests complement this deliberately small DOM probe.
"""
from __future__ import annotations

import calendar
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_js(tmp_path, payload, driver, *, timezone=None):
    from src.dashboard import _read_app_js

    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for dashboard execution tests')
    probe = (ROOT / 'tools/dashboard_probe.js').read_text()
    prelude = probe[probe.index('function makeNode('):probe.index('// Console errors')]
    prelude = prelude.replace('    remove() {},', '''    remove() {},
    append(...nodes) { this.children.push(...nodes); },
    prepend(...nodes) { this.children.unshift(...nodes); },''')
    prelude = prelude.replace('    addEventListener() {},', '''    addEventListener(name, callback) {
      this.listeners = this.listeners || {};
      this.listeners[name] = callback;
    },''')
    script = prelude + '\nconst errors = []; console.error = (...args) => errors.push(args.map(String).join(" "));\n'
    script += _read_app_js().replace('__JSON_DATA__', json.dumps(payload))
    script += '\n' + driver
    target = tmp_path / 'regression.js'
    target.write_text(script)
    result = subprocess.run([node, str(target)], text=True, capture_output=True, timeout=30,
                            env={**os.environ, **({'TZ': timezone} if timezone else {})})
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def payload(history=None, transactions=None):
    return {
        'as_of': '2024-12-31', 'generated': '2024-12-31T12:00:00',
        'history': history or [], 'transactions': transactions or [],
        'holdings': [], 'holdings_by_account': [], 'analytics': {},
        'cash_summary': {}, 'basis_totals': {}, 'basis_methods': {},
        'retirement_meta': {}, 'action_catalog': {'actions': []},
    }


def test_historical_holdings_exclude_future_disposals_and_recompute_group_returns(tmp_path):
    history = [
        {'date': '2023-01-31', 'total': 1000, 'by_account_group': {'Example': 1000}},
        {'date': '2023-12-31', 'total': 1100, 'total_cost_basis': 1000,
         'by_account_group': {'Example': 1100}, 'positions': [
             {'account_group': 'Example', 'symbol': 'XYZ', 'value': 1100,
              'quantity': 10, 'cost_basis': 1000, 'price': 110}]},
        {'date': '2024-12-31', 'total': 1800, 'by_account_group': {'Example': 1800}},
    ]
    data = payload(history, [{'date': '2024-06-10', 'account_group': 'Example',
                             'symbol': 'XYZ', 'realized_gain': 999, 'cash_flow': 0}])
    data['analytics']['performance_by_filter'] = {'Example': {
        'filter_groups': ['Example'], 'summary': {'cumulative': 8, 'annualized': 4},
        'money_weighted': {'annualized': 3},
    }}
    result = run_js(tmp_path, data, '''
      asOfDate = '2023-12-31';
      renderByAssetTable();
      const groups = withPerformance(groupByBasisAware(asOfHoldingsByAccount(), 'account_group'));
      renderStats();
      process.stdout.write(JSON.stringify({html: NODES.get('byAssetTbody').innerHTML,
        groups, stats: NODES.get('stats').innerHTML, errors}));
    ''')
    assert not result['errors']
    assert '$999.00' not in result['html']
    assert '$100.00' in result['html']
    assert '10.00%' in result['html']
    assert result['groups'][0]['twr_cum'] == pytest.approx(10)
    assert 10 < result['groups'][0]['xirr'] < 12
    assert '0% <span class="sub">at all-time high' in result['stats']


def test_historical_drawdown_ignores_a_later_peak(tmp_path):
    data = payload([
        {'date': '2023-01-31', 'total': 1000},
        {'date': '2023-12-31', 'total': 800},
        {'date': '2024-12-31', 'total': 3000},
    ])
    data['analytics']['drawdown'] = {'current_drawdown_pct': 0}
    result = run_js(tmp_path, data, '''
      asOfDate = '2023-12-31'; renderStats();
      process.stdout.write(JSON.stringify(NODES.get('stats').innerHTML));
    ''')
    assert 'Drawdown at selected date' in result
    assert '-20.00%' in result


def test_monthly_risk_metrics_are_invariant_to_midmonth_sampling_and_match_python(tmp_path):
    from src.analytics.monthly_pnl import compute_monthly_pnl

    value = 1000.0
    history = [{'date': '2024-01-31', 'total': value, 'net_contributed': 1000}]
    for month, ret in enumerate([0.08, -0.04, 0.06, -0.02, 0.03, -0.01], 2):
        history.append({'date': f'2024-{month:02}-15', 'total': value * (1 + ret) ** 0.5,
                        'net_contributed': 1000})
        value *= 1 + ret
        history.append({'date': f'2024-{month:02}-{calendar.monthrange(2024, month)[1]}',
                        'total': value, 'net_contributed': 1000})
    answers = []
    for points in [history, [h for h in history if not h['date'].endswith('-15')]]:
        data = payload(points)
        result = run_js(tmp_path, data, '''
          process.stdout.write(JSON.stringify(computeWindowedMetrics(null, 'lifetime')));
        ''')
        answers.append({key: result[key] for key in ['nMonths', 'sharpe', 'sortino']})
    expected = compute_monthly_pnl(history, [])
    assert answers[0] == answers[1]
    assert answers[0]['nMonths'] == 6
    assert answers[0]['sharpe'] == expected['sharpe']
    assert answers[0]['sortino'] == expected['sortino']


def test_imported_strings_are_text_and_handler_arguments_round_trip(tmp_path):
    attack = "Example\\\"'><img src=x onerror=alert(1)> & &#39;"
    data = payload(transactions=[{
        'date': '2024-01-01', 'account_group': attack, 'account_type': 'Taxable',
        'symbol': attack, 'sector': attack, 'description': attack, 'source_file': attack,
        'action': attack, 'quantity': 1, 'amount': 100,
    }])
    result = run_js(tmp_path, data, '''
      const raw = txns[0].description;
      const cells = ['description', 'source_file', 'account_group', 'action', 'symbol', 'amount']
        .map(field => formatCell(raw, field));
      const chip = _renderAccountChip(raw, true, 'selectAccount(' + _jsString(raw) + ')');
      renderRecentTransactions();
      process.stdout.write(JSON.stringify({cells, chip, raw,
        recent: NODES.get('recentTxnsBody').innerHTML,
        roundTrip: JSON.parse(_jsString(raw)), errors}));
    ''')
    assert not result['errors']
    assert result['roundTrip'] == attack
    for html in result['cells'] + [result['chip'], result['recent']]:
        assert '<img ' not in html
        assert '&lt;img ' in html
    assert '&amp;#39;' in result['chip']
    assert 'onclick="selectAccount(&quot;' in result['chip']


def test_tab_failure_is_visible_and_retry_can_succeed(tmp_path):
    result = run_js(tmp_path, payload(), '''
      let attempts = 0;
      registerTabRenderer('retry-test', () => { if (++attempts === 1) throw new Error('synthetic'); });
      activateTab('retry-test');
      const panel = NODES.get('tab-retry-test');
      const error = panel.children[0];
      const recordedBeforeRetry = TAB_RENDERED.has('retry-test');
      const button = error.children[1];
      button.listeners.click();
      process.stdout.write(JSON.stringify({recordedBeforeRetry, attempts,
        message: error.children[0].textContent, role: error.role,
        recordedAfterRetry: TAB_RENDERED.has('retry-test')}));
    ''')
    assert result['recordedBeforeRetry'] is False
    assert result['recordedAfterRetry'] is True
    assert result['attempts'] == 2
    assert result['role'] == 'alert'
    assert 'could not be displayed' in result['message']


@pytest.mark.parametrize('timezone', ['UTC', 'America/Los_Angeles', 'Pacific/Kiritimati'])
def test_static_artifact_clock_does_not_drift_with_visit_date(tmp_path, timezone):
    result = run_js(tmp_path, payload(), '''
      process.stdout.write(JSON.stringify({date: SNAPSHOT_DATE, year: snapshotYear(),
        tax: taxYearFilter, optionYtd: (_optWindow = 'ytd', _optWindowRange())}));
    ''', timezone=timezone)
    assert result == {'date': '2024-12-31', 'year': 2024, 'tax': '2024',
                      'optionYtd': ['2024-01-01', '2024-12-31']}


def test_imported_prototype_names_are_regular_accounts_and_symbols(tmp_path):
    data = payload(transactions=[{
        'date': '2024-01-01', 'account_group': '__proto__', 'account_type': 'Taxable',
        'symbol': 'constructor', 'action': 'Buy', 'quantity': 1, 'amount': 100,
    }])
    holding = {'account_group': '__proto__', 'account_type': 'Taxable',
               'symbol': 'constructor', 'sector': 'Other', 'quantity': 1,
               'price': 110, 'cost_basis': 100, 'value': 110, 'unrealized_gain': 10}
    data['holdings_by_account'] = [holding]
    data['holdings'] = [holding]
    result = run_js(tmp_path, data, '''
      renderHoldings(); renderByAssetTable();
      process.stdout.write(JSON.stringify({symbol: symLabel('constructor'),
        table: NODES.get('holdingsTbody').innerHTML, errors}));
    ''')
    assert not result['errors']
    assert result['symbol'] == '<span class="sym-cell">constructor</span>'
    assert '__proto__' in result['table']
    assert '$110.00' in result['table']


def test_result_rerender_retains_original_editing_control_and_raw_value(tmp_path):
    result = run_js(tmp_path, payload(), '''
      let rawValue = '25', valueAssignments = 0;
      const active = {id: 'editing', tagName: 'INPUT',
        get value() { return rawValue; }, set value(value) { valueAssignments++; rawValue = value; },
        selectionStart: 2, selectionEnd: 2, focused: false,
        focus() { this.focused = true; },
        setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }};
      NODES.set('editing', active);
      document.activeElement = active;
      renderKeepingFocus(() => NODES.set('editing', {
        id: 'editing', value: '25000', replaceWith(node) { NODES.set('editing', node); }
      }));
      process.stdout.write(JSON.stringify({sameNode: NODES.get('editing') === active,
        value: active.value, focused: active.focused, selection: active.selectionStart, valueAssignments}));
    ''')
    assert result == {'sameNode': True, 'value': '25', 'focused': True, 'selection': 2, 'valueAssignments': 0}


def test_csv_download_escapes_formulas_without_changing_numeric_losses(tmp_path):
    result = run_js(tmp_path, payload(), '''
      let captured;
      URL.createObjectURL = blob => { captured = blob; return 'blob:synthetic'; };
      URL.revokeObjectURL = () => {};
      _downloadCsv('synthetic.csv', [['description', 'Description'], ['gain', 'Gain']],
        [{description: '=2+2', gain: -100}, {description: 'normal, text', gain: 10}]);
      captured.text().then(text => process.stdout.write(JSON.stringify(text)));
    ''')
    assert result.splitlines() == ['Description,Gain', "'=2+2,-100", '"normal, text",10']



def test_ytd_monthly_ratios_include_january_from_the_prior_close(tmp_path):
    from src.analytics.monthly_pnl import compute_monthly_pnl

    value = 1000.0
    history = [{'date': '2023-12-31', 'total': value, 'net_contributed': 1000}]
    for month, ret in enumerate([0.08, -0.04, 0.06, -0.02, 0.03, -0.01], 1):
        history.append({'date': f'2024-{month:02}-15', 'total': value * (1 + ret) ** 0.5,
                        'net_contributed': 1000})
        value *= 1 + ret
        history.append({'date': f'2024-{month:02}-{calendar.monthrange(2024, month)[1]}',
                        'total': value, 'net_contributed': 1000})
    result = run_js(tmp_path, payload(history), """
      process.stdout.write(JSON.stringify(computeWindowedMetrics(null, 'ytd')));
    """)
    expected = compute_monthly_pnl(history, [])
    assert result['nMonths'] == 6
    assert result['sharpe'] == expected['sharpe']
    assert result['sortino'] == expected['sortino']
