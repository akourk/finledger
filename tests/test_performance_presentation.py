"""Exercise Performance's scope controls and hierarchy in the shipped bundle.

Financial values come from an independently fictional two-account fixture.
Browser checks complement these probes with responsive layout and keyboard use.
"""
from __future__ import annotations

import re

import pytest

from tests.test_account_transfer_frontend import _data
from tests.test_frontend_review_regressions import payload, run_js


def _select(html, element_id):
    match = re.search(rf'<select\b[^>]*id="{element_id}"[^>]*>(.*?)</select>',
                      html, re.DOTALL)
    assert match, element_id
    return match.group(1)


def test_selected_summary_precedes_chart_and_secondary_figures_remain_available(tmp_path):
    result = run_js(tmp_path, _data(), """
      renderPerformance();
      process.stdout.write(JSON.stringify({html: NODES.get('performanceContent').innerHTML, errors}));
    """)
    assert not result["errors"]
    html = result["html"]
    assert html.index('id="perfControls"') < html.index('id="perfSelectedSummary"')
    assert html.index('id="perfSelectedSummary"') < html.index('id="benchmarkCompareSvg"')
    assert html.index('id="benchmarkCompareSvg"') < html.index('id="perfBenchmarkDetails"')
    assert re.search(r'<details[^>]*id="perfLifetimeReference">', html)
    reference = html.split('id="perfLifetimeReference"', 1)[1].split('</details>', 1)[0]
    for label in ["Total Return", "Realized", "Unrealized", "Net Contributed", "Fees Paid"]:
        assert f'<div class="label">{label}</div>' in reference
    assert 'not</strong> add up to Total Return' in reference
    primary = html.split('<div class="perf-primary-summary">', 1)[1].split('<details', 1)[0]
    assert primary.count('class="stat-card') == 4
    for label in ["Portfolio Value", "Total Return", "Cumulative Return", "Annualized Return"]:
        assert f'<div class="label">{label} ' in primary
    assert 'dollars · lifetime' in primary
    assert 'TWR · lifetime' in primary
    assert 'TWR per year · lifetime' in primary
    assert '0.0% of net contributed' in primary
    assert 'Latest positions · lifetime gains · all accounts' in html
    assert 'Total Portfolio · all available years' in html
    before_risk, risk = html.split('id="perfViewRisk"', 1)
    assert '<div class="label">Sharpe' not in before_risk
    assert '<div class="label">Sharpe' in risk
    assert '<div class="label">Sortino' in risk
    assert 'Whole-portfolio history and current-position daily moves' in risk


def test_mobile_controls_retain_account_and_complete_window_choices(tmp_path):
    result = run_js(tmp_path, _data(), """
      setPerformanceAccountFilter('Beta');
      setPerformanceWindow('6mo');
      process.stdout.write(JSON.stringify({html: NODES.get('performanceContent').innerHTML,
        presets: PERF_TWR_PRESETS, filter: performanceAccountFilter, window: performanceWindow, errors}));
    """)
    assert not result["errors"]
    assert (result["filter"], result["window"]) == ("Beta", "6mo")
    html = result["html"]
    account_options = _select(html, "perfAccountSelect")
    assert '<option value="Beta" selected>Beta</option>' in account_options
    assert {"", "__taxable__", "__retirement__", "Alpha", "Beta"} == set(
        re.findall(r'<option value="([^"]*)"', account_options))
    mobile = html.split('class="toggles-row mobile-only perf-mobile-ranges"', 1)[1].split('</div>', 1)[0]
    common = re.findall(r'data-perf-window="([^"]+)"', mobile)
    remaining = re.findall(r'<option value="([^"]+)"', _select(html, "perfWindowSelect"))
    assert common == ["lifetime", "1y", "ytd", "3mo"]
    assert set(common + remaining) == set(result["presets"])
    assert '<option value="6mo" selected>' in mobile
    assert 'aria-label="Performance window"' in mobile
    assert '<label for="perfAccountSelect">Account</label>' in html
    assert 'onchange="setPerformanceAccountFilter(this.value)"' in html
    assert 'onchange="setPerformanceWindow(this.value)"' in html


def test_custom_scope_displays_resolved_dollar_and_return_dates_separately(tmp_path):
    result = run_js(tmp_path, _data(), """
      setPerformanceAccountFilter('Alpha');
      setPerfTwrStart('2024-01-20'); setPerfTwrEnd('2024-03-20');
      const custom = NODES.get('performanceContent').innerHTML;
      setPerformanceWindow('3mo');
      process.stdout.write(JSON.stringify({custom, preset: NODES.get('performanceContent').innerHTML,
        start: perfTwrStart, end: perfTwrEnd, errors}));
    """)
    assert not result["errors"]
    html = result["custom"]
    assert '<h2 id="perfSelectedHeading">Alpha</h2>' in html
    assert 'Dollar figures: 2023-12-31 → 2024-02-29.' in html
    assert 'Time-weighted returns: 2024-01-31 → 2024-03-31.' in html
    assert 'value="2024-01-20" onchange="setPerfTwrStart(this.value)"' in html
    assert 'value="2024-03-20" onchange="setPerfTwrEnd(this.value)"' in html
    assert html.count('id="perfCustomStart"') == 1
    assert html.count('id="perfCustomEnd"') == 1
    assert 'min="2023-12-31" max="2024-03-31"' in html
    assert '<option value="custom" selected>' in _select(html, "perfWindowSelect")
    assert result["start"] is None and result["end"] is None
    assert 'id="perfCustomStart"' not in result["preset"]


def test_scope_changes_preserve_open_details_focus_and_risk_selection(tmp_path):
    result = run_js(tmp_path, _data(), """
      const details = ['perfLifetimeReference', 'perfDollarDetails', 'perfBenchmarkDetails', 'perfReturnMethods'];
      details.forEach(id => { nodeFor(id).open = true; });
      let focusCount = 0;
      document.activeElement = nodeFor('perfAccountSelect');
      nodeFor('perfAccountSelect').focus = () => { focusCount++; };
      setPerfView('risk'); setPerformanceAccountFilter('Alpha');
      const risk = NODES.get('performanceContent').innerHTML;
      setPerfView('returns');
      process.stdout.write(JSON.stringify({risk, details, focusCount,
        returnsPressed: nodeFor('perfViewBtnReturns').getAttribute('aria-pressed'),
        riskPressed: nodeFor('perfViewBtnRisk').getAttribute('aria-pressed'), errors}));
    """)
    assert not result["errors"]
    assert result["focusCount"] == 1
    for detail in result["details"]:
        assert re.search(rf'<details[^>]*id="{detail}" open>', result["risk"])
    assert 'id="perfViewReturns" style="display:none;"' in result["risk"]
    assert 'id="perfViewRisk" style=""' in result["risk"]
    assert result["returnsPressed"] == "true"
    assert result["riskPressed"] == "false"


@pytest.mark.parametrize("control", ["mobile", "desktop", "account", "aggregate", "total"])
def test_activated_buttons_restore_focus_to_the_replacement_control(tmp_path, control):
    result = run_js(tmp_path, _data(), r"""
      renderPerformance();
      const kind = '__CONTROL__';
      const content = nodeFor('performanceContent');
      let html = content.innerHTML;
      const buttons = [...html.matchAll(/<button\b[^>]*>.*?<\/button>/gs)].map(m => m[0]);
      const isWindow = kind === 'mobile' || kind === 'desktop';
      const tag = buttons.find(button => isWindow
        ? button.includes('id="perfWindowButton-' + kind + '-ytd"')
        : kind === 'account' ? button.endsWith('>Alpha</button>')
        : button.includes('id="perfAccountButton-' + (kind === 'aggregate' ? '__taxable__' : 'total') + '"'));
      const id = tag.match(/\bid="([^"]+)"/)[1];
      const previous = nodeFor(id);
      previous.dataset.perfWindow = 'ytd';
      previous.closest = selector => selector === '[data-perf-window]' && isWindow ? previous : null;
      document.activeElement = previous;
      let focusCount = 0;
      // Replacing innerHTML detaches the old button in a browser. Model that
      // boundary so a test cannot pass by leaving the original node focused.
      Object.defineProperty(content, 'innerHTML', {
        get: () => html,
        set: value => {
          html = value; document.activeElement = document.body;
          const replacement = makeNode(id);
          replacement.focus = () => { document.activeElement = replacement; focusCount++; };
          NODES.set(id, replacement);
        }
      });
      if (isWindow) {
        content.listeners.click({target: previous, detail: 0});
      } else {
        const handler = tag.match(/onclick="([^"]+)"/)[1].replaceAll('&quot;', '"').replaceAll('&#39;', "'");
        eval(handler);
      }
      process.stdout.write(JSON.stringify({id, focused: document.activeElement.id,
        replacement: document.activeElement !== previous, focusCount, html,
        filter: performanceAccountFilter, window: performanceWindow, errors}));
    """.replace("__CONTROL__", control))
    assert not result["errors"]
    assert result["focused"] == result["id"]
    assert result["replacement"] and result["focusCount"] == 1
    if control in {"mobile", "desktop"}:
        assert result["window"] == "ytd"
    else:
        assert result["filter"] == {"account": "Alpha", "aggregate": "__taxable__", "total": None}[control]
    control_ids = re.findall(r'\bid="((?:perfWindowButton-|perfAccountButton-|chip-setPerformanceAccountFilter)[^"]+)"',
                             result["html"])
    assert len(control_ids) == len(set(control_ids))


def test_account_labels_are_text_and_empty_returns_have_an_explicit_state(tmp_path):
    label = 'Example <img src=x onerror=alert(1)> & "Savings"'
    data = payload()
    data["holdings_by_account"] = [{"account_group": label, "account_type": "Taxable",
                                    "symbol": "XYZ", "quantity": 1, "value": 100,
                                    "cost_basis": 100, "unrealized_gain": 0}]
    result = run_js(tmp_path, data, """
      setPerformanceAccountFilter(holdingsByAccount[0].account_group);
      process.stdout.write(JSON.stringify({html: NODES.get('performanceContent').innerHTML, errors}));
    """)
    assert not result["errors"]
    assert '<img ' not in result["html"]
    assert '&lt;img ' in result["html"]
    assert 'Time-weighted returns: Not enough observations.' in result["html"]
    assert 'No measured return period' in result["html"]
