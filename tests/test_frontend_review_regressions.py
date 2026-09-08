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


def run_js(tmp_path, payload, driver, *, timezone=None, initial_hash=''):
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
    script += 'location.hash = ' + json.dumps(initial_hash) + ';\n'
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


@pytest.mark.parametrize('timezone', ['UTC', 'America/Los_Angeles', 'Pacific/Kiritimati'])
def test_calendar_windows_keep_dst_and_month_end_boundaries(tmp_path, timezone):
    data = payload()
    data['as_of'] = '2024-05-31'
    result = run_js(tmp_path, data, """
      _optWindow = '3mo';
      process.stdout.write(JSON.stringify({
        autumn: _windowCutoffIso('30day', '2024-11-30'),
        spring: _windowCutoffIso('30day', '2024-03-31'),
        yearAgo: shiftCalendarIso('2024-02-29', { years: -1 }),
        optionRange: _optWindowRange()
      }));
    """, timezone=timezone)
    assert result == {
        'autumn': '2024-10-31', 'spring': '2024-03-01',
        'yearAgo': '2023-02-28', 'optionRange': ['2024-02-29', '2024-05-31'],
    }


@pytest.mark.parametrize('timezone', ['UTC', 'America/Los_Angeles', 'Pacific/Kiritimati'])
def test_retirement_defaults_use_dated_signed_contributions_and_python_parity(tmp_path, timezone):
    from src.analytics._shared import contributions_by_year

    rows = [
        ('2023-12-30', 'Contribution', 10000, ''),
        ('2023-12-31', 'Contribution', 100, ''),
        ('2024-06-01', 'Contribution', 1000, ''),
        ('2024-06-02', 'Contribution Reversal', 200, ''),
        ('2024-06-03', 'Contribution', 300, 'Employer match'),
        ('2024-06-04', 'Transfer In', 5000, 'Custodian transfer'),
        ('2024-12-31', 'Contribution', 200, ''),
        ('2025-01-01', 'Contribution', 20000, ''),
    ]
    transactions = [dict(date=date, action=action, amount=amount, description=description,
                         account_group='401K', account_type='Retirement', symbol='XYZ')
                    for date, action, amount, description in rows]
    data = payload(transactions=transactions)
    data['retirement_meta'] = {'birthday': '1990-12-31'}
    result = run_js(tmp_path, data, """
      renderPlanning(); renderRetirement();
      process.stdout.write(JSON.stringify({
        total: trailingRetirementContributions(),
        employee: trailingRetirementContributions({ employeeOnly: true }),
        years: computeRetirementContributionsByYear(),
        planning: NODES.get('planningContent').innerHTML,
        forecast: _buildCashFlowForecast(0), errors
      }));
    """, timezone=timezone)
    assert not result['errors']
    assert result['total'] == 1400
    assert result['employee'] == 1100
    assert result['years'] == contributions_by_year(transactions)
    assert 'id="retAnnualContrib" value="1400"' in result['planning']
    assert '$1,100.00' in result['forecast']


@pytest.mark.parametrize('timezone', ['UTC', 'America/Los_Angeles', 'Pacific/Kiritimati'])
@pytest.mark.parametrize(('birthday', 'as_of', 'expected'), [
    ('1990-06-15', '2025-06-14', 34),
    ('1990-06-15', '2025-06-15', 35),
    ('1992-02-29', '2025-02-28', 32),
    ('1992-02-29', '2025-03-01', 33),
])
def test_retirement_age_and_projection_horizon_follow_calendar_anniversaries(
        tmp_path, timezone, birthday, as_of, expected):
    data = payload()
    data['as_of'] = as_of
    data['retirement_meta'] = {'birthday': birthday}
    result = run_js(tmp_path, data, """
      renderPlanning(); renderRetirement();
      process.stdout.write(JSON.stringify({age: currentAgeFromMeta(),
        retirement: NODES.get('retirementContent').innerHTML,
        planning: NODES.get('planningContent').innerHTML, errors}));
    """, timezone=timezone)
    assert not result['errors']
    assert result['age'] == expected
    assert f'Current Age</div><div class="value">{expected}</div>' in result['retirement']
    assert f'Value at age 67 ({67 - expected}y)' in result['planning']


def test_transactions_render_on_first_visit_and_reuse_hidden_column_search_text(tmp_path):
    data = payload(transactions=[
        {'date': '2024-01-01', 'symbol': 'AAA', 'description': 'Unique hidden phrase', 'amount': 100},
        {'date': '2024-01-02', 'symbol': 'BBB', 'description': 'Another entry', 'amount': 200},
    ])
    result = run_js(tmp_path, data, """
      const before = NODES.get('tbody').innerHTML;
      activateTab('transactions');
      const first = NODES.get('tbody').innerHTML;
      let searchReads = 0;
      for (const t of txns) {
        const description = t.description;
        Object.defineProperty(t, 'description', { get() { searchReads++; return description; } });
      }
      searchInput.value = 'unique hidden'; renderTable();
      const filtered = NODES.get('tbody').innerHTML;
      const count = NODES.get('countPill').textContent;
      searchInput.value = 'UNIQUE'; renderTable();
      searchInput.value = 'unique hidden phrase'; renderTable();
      activateTab('overview'); activateTab('transactions');
      process.stdout.write(JSON.stringify({before, first, filtered, count, searchReads,
        after: NODES.get('tbody').innerHTML, errors}));
    """)
    assert not result['errors']
    assert result['before'] == ''
    assert 'AAA' in result['first'] and 'BBB' in result['first']
    assert 'AAA' in result['filtered'] and 'BBB' not in result['filtered']
    assert 'Unique hidden phrase' not in result['filtered']
    assert result['count'] == '1 / 2'
    assert result['searchReads'] == 2
    assert result['after'] == result['filtered']


def test_transaction_deep_link_renders_on_initial_load(tmp_path):
    data = payload(transactions=[{'date': '2024-01-01', 'symbol': 'XYZ', 'amount': 100}])
    result = run_js(tmp_path, data, """
      process.stdout.write(JSON.stringify({html: NODES.get('tbody').innerHTML,
        rendered: TAB_RENDERED.has('transactions'), errors}));
    """, initial_hash='#transactions')
    assert not result['errors']
    assert result['rendered'] is True
    assert 'XYZ' in result['html']


@pytest.mark.parametrize('timezone', ['UTC', 'America/Los_Angeles', 'Pacific/Kiritimati'])
def test_existing_wash_risk_window_includes_the_thirtieth_day_across_dst(tmp_path, timezone):
    data = payload(transactions=[
        {'date': '2024-10-16', 'symbol': 'XYZ', 'account_group': 'Example',
         'action': 'Buy', 'amount': 1000},
        {'date': '2024-11-15', 'symbol': 'XYZ', 'account_group': 'Example',
         'action': 'Sell', 'amount': 900, 'realized_gain': -100},
    ])
    result = run_js(tmp_path, data, """
      renderTax();
      process.stdout.write(JSON.stringify({html: NODES.get('taxContent').innerHTML, errors}));
    """, timezone=timezone)
    assert not result['errors']
    assert 'No potential wash sales detected.' not in result['html']
    assert '2024-10-16' in result['html']


@pytest.mark.parametrize('group', ['Example Plan', '__proto__'])
def test_custom_retirement_groups_match_python_with_legacy_fallback(tmp_path, monkeypatch, group):
    from src import config
    from src.analytics._shared import contributions_by_year

    monkeypatch.setitem(config.ACCOUNT_TYPES, group, 'Retirement')
    transactions = [
        {'date': '2024-06-01', 'account_group': group, 'account_type': 'Retirement',
         'action': 'Contribution', 'amount': 200},
        {'date': '2024-06-01', 'account_group': '401K', 'action': 'Contribution', 'amount': 100},
        {'date': '2024-06-01', 'account_group': 'constructor', 'account_type': 'Taxable',
         'action': 'Contribution', 'amount': 900},
    ]
    data = payload(transactions=transactions)
    data['holdings_by_account'] = [
        {'account_group': group, 'account_type': 'Retirement', 'value': 2000, 'cost_basis': 1800},
        {'account_group': '401K', 'value': 1000, 'cost_basis': 900},
        {'account_group': 'constructor', 'account_type': 'Taxable', 'value': 9000, 'cost_basis': 8000},
    ]
    result = run_js(tmp_path, data, """
      renderRetirement();
      process.stdout.write(JSON.stringify({summary: computeRetirementSummary(),
        contributions: trailingRetirementContributions(), years: computeRetirementContributionsByYear(),
        html: NODES.get('retirementContent').innerHTML, errors}));
    """)
    assert not result['errors']
    assert result['summary']['value'] == 3000
    assert result['summary']['basis'] == 2700
    assert set(result['summary']['byGroup']) == {group, '401K'}
    assert result['contributions'] == 300
    assert result['years'] == contributions_by_year(transactions)
    assert '$3,000.00' in result['html']
