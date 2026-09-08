"""Allocation labels and empty states in the assembled fictional dashboard.

Real SVG fill geometry for full and almost-full rings is covered by the
Chrome checks in tools/browser_smoke.js; a DOM stub cannot detect empty arcs.
"""
from __future__ import annotations

import html
import json

import pytest

from tests.test_frontend_review_regressions import payload, run_js


@pytest.mark.parametrize("field", ["account_group", "account_type", "sector"])
@pytest.mark.parametrize("historical", [False, True])
def test_single_allocation_preserves_escaped_label_and_full_share(tmp_path, field, historical):
    label = 'Example <img src=x onerror="alert(1)"> & "quoted"'
    snapshot_field = {"account_group": "by_account_group", "account_type": "by_account_type",
                      "sector": "by_sector"}[field]
    data = payload([
        {"date": "2023-12-31", "total": 125, snapshot_field: {label: 125}},
        {"date": "2024-12-31", "total": 125},
    ])
    data["holdings_by_account"] = [{field: label, "value": 125}]
    result = run_js(tmp_path, data, f"""
      asOfDate = {json.dumps('2023-12-31' if historical else '2024-12-31')};
      _renderOneAllocationDonut(nodeFor('testSvg'), nodeFor('testLegend'),
        {json.dumps(field)}, Object.create(null));
      process.stdout.write(JSON.stringify({{svg: nodeFor('testSvg').innerHTML,
        legend: nodeFor('testLegend').innerHTML, errors}}));
    """)
    assert result["errors"] == []
    for text in [result["svg"], result["legend"]]:
        assert "<img " not in text
        assert label in html.unescape(text)
        assert "100.0%" in text
    assert "$125.00" in html.unescape(result["svg"])


@pytest.mark.parametrize("values", [[], [0], [0, -10], [None]])
def test_empty_allocation_has_zero_total_and_no_slices_or_legend(tmp_path, values):
    data = payload()
    data["holdings_by_account"] = [{"account_group": "Example", "value": value}
                                   for value in values]
    result = run_js(tmp_path, data, """
      _renderOneAllocationDonut(nodeFor('testSvg'), nodeFor('testLegend'),
        'account_group', Object.create(null));
      process.stdout.write(JSON.stringify({svg: nodeFor('testSvg').innerHTML,
        legend: nodeFor('testLegend').innerHTML, errors}));
    """)
    assert result["errors"] == []
    assert result["legend"] == ""
    assert "<path " not in result["svg"]
    assert ">$0</text>" in result["svg"]
    assert not any(token in result["svg"] for token in ["NaN", "Infinity", "undefined"])


def test_category_names_matching_object_properties_keep_their_values(tmp_path):
    data = payload()
    data["holdings_by_account"] = [
        {"account_group": "__proto__", "value": 75},
        {"account_group": "constructor", "value": 25},
    ]
    result = run_js(tmp_path, data, """
      _renderOneAllocationDonut(nodeFor('testSvg'), nodeFor('testLegend'),
        'account_group', Object.create(null));
      process.stdout.write(JSON.stringify({svg: nodeFor('testSvg').innerHTML,
        legend: nodeFor('testLegend').innerHTML, errors}));
    """)
    assert result["errors"] == []
    assert "__proto__: $75.00 (75.0%)" in result["svg"]
    assert "constructor: $25.00 (25.0%)" in result["svg"]
    assert ">$100</text>" in result["svg"]
