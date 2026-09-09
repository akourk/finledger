"""Exercise Holdings view scopes against the shipped dashboard bundle.

All amounts and accounts below are independently fictional. The existing DOM
probe captures rendered output; browser checks additionally exercise real DOM
replacement and focus while switching between Table and Board.
"""
from __future__ import annotations

import re

import pytest

from tests.test_frontend_review_regressions import payload, run_js


@pytest.fixture
def holdings_payload():
    def position(account, symbol, quantity, price, basis):
        return {
            'account_group': account, 'account_type': 'Taxable',
            'symbol': symbol, 'sector': 'Other', 'quantity': quantity,
            'price': price, 'value': quantity * price, 'cost_basis': basis,
            'unrealized_gain': quantity * price - basis,
        }

    accounts = [position('Example A', 'DUP', 1, 100, 80),
                position('Example B', 'DUP', 2, 100, 220),
                position('Example B', 'SOLO', 1, 50, 40)]
    symbols = [position('', 'DUP', 3, 100, 300),
               position('', 'SOLO', 1, 50, 40)]
    prior = [position('Example A', 'DUP', 1, 60, 50),
             position('Example B', 'DUP', 2, 60, 110)]
    data = payload([
        {'date': '2024-03-31', 'total': 180, 'total_cost_basis': 160,
         'positions': prior, 'by_account_group': {'Example A': 60, 'Example B': 120}},
        {'date': '2024-12-31', 'total': 350, 'total_cost_basis': 340,
         'positions': accounts, 'by_account_group': {'Example A': 100, 'Example B': 250}},
    ])
    data['holdings_by_account'] = accounts
    data['holdings'] = symbols

    def board(row, members):
        return {**row, 'accounts': members, 'open_pnl': row['unrealized_gain'],
                'open_pnl_pct': row['unrealized_gain'] / row['cost_basis'] * 100,
                'windows': {'1d': {'pnl': row['unrealized_gain'], 'pct': 1}}}

    data['analytics']['position_pnl'] = {
        'as_of': '2024-12-31',
        'windows': [{'key': '1d', 'label': '1D', 'start_date': '2024-12-30'}],
        'by_account': [board(r, [r['account_group']]) for r in accounts],
        'by_symbol': [board(symbols[0], ['Example A', 'Example B']),
                      board(symbols[1], ['Example B'])],
    }
    return data


# Dispatch the actual inline Board control handlers from rendered markup.
# Table uses addEventListener, which the shared probe records directly.
CONTROLS = r'''
  function tableAccount(value) {
    const control = NODES.get('byAssetAccountGroupFilter');
    control.value = value;
    control.listeners.change();
  }
  function boardControl(id, event, value) {
    const tag = NODES.get('boardControls').innerHTML.match(
      new RegExp('<[^>]+id="' + id + '"[^>]*>'))[0];
    const handler = tag.match(new RegExp('on' + event + '="([^"]+)"'))[1];
    (function () { eval(handler); }).call({value});
  }
  function view() {
    return {
      header: NODES.get('byAssetTotalValue').textContent,
      title: NODES.get('byAssetTotalValue').title,
      tableFilter: byAssetGroupFilter, boardFilter: boardAccountFilter,
      scope: NODES.get('boardScopeContext')?.textContent || '',
      panes: NODES.get('boardPanes')?.innerHTML || '',
    };
  }
'''


def test_shared_total_tracks_active_view_and_independent_account_filters(tmp_path, holdings_payload):
    result = run_js(tmp_path, holdings_payload, CONTROLS + '''
      const steps = [];
      tableAccount('Example A'); steps.push(view());
      setBoardLayout('board'); steps.push(view());
      boardControl('boardAccountFilter', 'change', 'Example A'); steps.push(view());
      setBoardGroupBy('account'); steps.push(view());
      renderByAssetTable(); steps.push(view()); // hidden Table must not overwrite Board
      setBoardLayout('table'); steps.push(view());
      tableAccount('Example B'); steps.push(view());
      setBoardLayout('board'); steps.push(view());
      process.stdout.write(JSON.stringify({steps, errors}));
    ''')
    assert not result['errors']
    steps = result['steps']
    assert [s['header'] for s in steps] == [
        '$100.00', '$350.00', '$300.00', '$100.00', '$100.00',
        '$100.00', '$250.00', '$100.00',
    ]
    assert steps[1]['tableFilter'] == 'Example A'
    assert steps[1]['boardFilter'] == ''
    assert steps[-1]['tableFilter'] == 'Example B'
    assert steps[-1]['boardFilter'] == 'Example A'
    for index in (1, 2, 3, 4, 7):
        totals = re.findall(r'class="board-pane-total"[^>]*>([^<]+)', steps[index]['panes'])
        assert totals == [steps[index]['header']] * 3
    assert 'Symbols held in Example A' in steps[2]['scope']
    assert 'include all accounts' in steps[2]['scope']
    assert 'only to Board' in steps[2]['scope']
    assert 'amounts follow the selected account' in steps[3]['scope']


def test_empty_board_selection_shows_zero_and_recovers_without_changing_table(tmp_path, holdings_payload):
    result = run_js(tmp_path, holdings_payload, CONTROLS + '''
      tableAccount('Example A');
      setBoardLayout('board');
      boardControl('boardSearch', 'input', 'no matching fictional position');
      const empty = view();
      boardControl('boardSearch', 'input', 'DUP');
      const recovered = view();
      setBoardLayout('table');
      process.stdout.write(JSON.stringify({empty, recovered, table: view(), errors}));
    ''')
    assert not result['errors']
    assert result['empty']['header'] == '$0.00'
    assert result['empty']['panes'].count('No positions match.') == 3
    assert result['empty']['panes'].count('>$0.00</span>') >= 3
    assert result['recovered']['header'] == '$300.00'
    assert result['table']['header'] == '$100.00'


def test_historical_board_notice_clears_total_and_table_restores_snapshot_scope(tmp_path, holdings_payload):
    result = run_js(tmp_path, holdings_payload, CONTROLS + '''
      const selectedLabel = makeNode('selected-scope');
      const latestLabel = makeNode('latest-scope');
      const originalQuery = document.querySelectorAll;
      document.querySelectorAll = selector => selector === '.as-of-label' ? [selectedLabel]
        : selector === '.latest-scope-label' ? [latestLabel] : originalQuery(selector);
      renderDateScopes();
      const initialLabel = selectedLabel.textContent;
      tableAccount('Example A');
      setBoardLayout('board');
      boardControl('boardAccountFilter', 'change', 'Example A');
      setAsOfDate('2024-06-30');
      const historical = {...view(), notice: NODES.get('boardBody').innerHTML,
        label: selectedLabel.textContent, latestLabel: latestLabel.textContent,
        labelTitle: selectedLabel.title};
      setBoardLayout('table'); const historicalTable = view();
      setAsOfDate(null); const latestTable = view();
      setBoardLayout('board'); const restored = view();
      setAsOfDate('2020-01-01'); setBoardLayout('table');
      process.stdout.write(JSON.stringify({initialLabel, historical, historicalTable,
        latestTable, restored, beforeHistory: view(), noSnapshotLabel: selectedLabel.textContent, errors}));
    ''')
    assert not result['errors']
    assert result['initialLabel'] == 'Latest · 2024-12-31'
    assert result['historical']['header'] == ''
    assert 'Back to latest' in result['historical']['notice']
    assert result['historical']['label'] == 'As of · 2024-03-31'
    assert '2024-06-30' in result['historical']['labelTitle']
    assert result['historical']['latestLabel'] == 'Latest · 2024-12-31'
    assert result['historicalTable']['header'] == '$60.00'
    assert result['latestTable']['header'] == '$100.00'
    assert result['restored']['header'] == '$300.00'
    assert result['beforeHistory']['header'] == '$0.00'
    assert result['noSnapshotLabel'] == 'No prior snapshot'


def test_board_without_exported_position_data_does_not_keep_table_total(tmp_path, holdings_payload):
    del holdings_payload['analytics']['position_pnl']
    result = run_js(tmp_path, holdings_payload, CONTROLS + '''
      setBoardLayout('board'); const missing = view();
      const notice = NODES.get('boardBody').innerHTML;
      setBoardLayout('table');
      process.stdout.write(JSON.stringify({missing, notice, table: view(), errors}));
    ''')
    assert not result['errors']
    assert result['missing']['header'] == ''
    assert 'No position data available' in result['notice']
    assert result['table']['header'] == '$350.00'


def test_holdings_levels_are_neutral_while_gains_keep_their_sign_colors(tmp_path, holdings_payload):
    result = run_js(tmp_path, holdings_payload, '''
      process.stdout.write(JSON.stringify({positions: NODES.get('byAssetTbody').innerHTML,
        summary: NODES.get('holdingsTbody').innerHTML,
        topBar: NODES.get('topBarSummary').innerHTML, errors}));
    ''')
    assert not result['errors']
    positions = re.findall(r'<tr[^>]*>(.*?)</tr>', result['positions'], re.S)
    summary = re.findall(r'<tr[^>]*>(.*?)</tr>', result['summary'], re.S)
    assert len(positions) == 3
    assert len(summary) == 2
    for row in positions:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
        for index in (3, 4, 5, 6):  # quantity, price, value, basis
            assert not re.search(r'class="(?:positive|negative)"', cells[index])
        if 'Example B' in cells[1] and 'DUP' in cells[0]:
            assert 'class="negative"' in cells[7]  # unrealized loss
            assert 'class="negative"' in cells[9]  # total return
            assert 'class="negative"' in cells[10]  # return percent
        elif 'Example A' in cells[1]:
            assert 'class="positive"' in cells[7]
            assert 'class="positive"' in cells[9]
    for row in summary:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
        assert not re.search(r'class="(?:positive|negative)"', ''.join(cells[1:3]))
        assert re.search(r'class="(?:positive|negative)"', cells[3])
    assert 'tb-value tb-portfolio-value' in result['topBar']
    assert 'tb-value positive' in result['topBar']  # total return remains positive


def test_scroll_cues_follow_horizontal_overflow_and_clear_after_resize(tmp_path):
    result = run_js(tmp_path, payload(), '''
      const region = makeNode('fictional-table');
      region.clientWidth = 300; region.scrollWidth = 500;
      region.clientHeight = 200; region.scrollHeight = 200;
      const originalQuery = document.querySelectorAll;
      document.querySelectorAll = selector => selector.startsWith('.table-wrap')
        ? [region] : originalQuery(selector);
      applyScrollRegionFocus();
      const horizontal = {cue: region['data-overflow-x'], tabIndex: region.tabIndex};
      region.clientWidth = 600; region.scrollHeight = 400;
      applyScrollRegionFocus();
      const vertical = {cue: region['data-overflow-x'], tabIndex: region.tabIndex};
      region.scrollHeight = 200;
      applyScrollRegionFocus();
      process.stdout.write(JSON.stringify({horizontal, vertical,
        fittedCue: region['data-overflow-x'], errors}));
    ''')
    assert not result['errors']
    assert result['horizontal'] == {'cue': 'true', 'tabIndex': 0}
    assert result['vertical'] == {'cue': 'false', 'tabIndex': 0}
    assert result['fittedCue'] == 'false'
