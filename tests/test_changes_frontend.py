"""The shipped activity card consumes actual deltas and explains migration."""
import pytest

from tests.test_frontend_review_regressions import payload, run_js


def _render(tmp_path, changes):
    data = payload()
    data['analytics']['changes'] = changes
    result = run_js(tmp_path, data, '''
      renderOverviewStatus();
      process.stdout.write(JSON.stringify({
        html: NODES.get('overviewStatus').innerHTML, errors}));
    ''')
    assert not result['errors']
    return result['html']


def test_activity_displays_exported_actual_basis_and_gain(tmp_path):
    html = _render(tmp_path, {
        'first_run': False, 'prev_run_at': '2024-06-01',
        'txn_count_delta': 1, 'value_delta': 0,
        'basis_delta': -30, 'realized_delta': 10,
        'basis_comparison_available': True,
    })
    assert 'Cost Basis' in html and '-$30' in html
    assert 'Realized P&L' in html and '+$10' in html
    assert '+$30' not in html
    assert 'comparisons start' not in html


@pytest.mark.parametrize('value_delta', [0, 25])
def test_migration_explains_unavailable_basis_without_inventing_zero(tmp_path, value_delta):
    html = _render(tmp_path, {
        'first_run': False, 'prev_run_at': '2024-06-01',
        'txn_count_delta': 0, 'value_delta': value_delta,
        'basis_delta': None, 'realized_delta': None,
        'basis_comparison_available': False,
    })
    assert "What's Changed" in html
    assert 'Basis and realized-gain comparisons start from this update.' in html
    assert 'Cost Basis' not in html and 'Realized P&L' not in html
    assert '$0' not in html
    if value_delta:
        assert 'Portfolio Value' in html and '+$25' in html


def test_first_run_pending_snapshot_is_not_activity(tmp_path):
    html = _render(tmp_path, {'first_run': True, 'current': {'cost_basis': 30}})
    assert "What's Changed" not in html
