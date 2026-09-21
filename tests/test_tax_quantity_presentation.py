"""Tax position totals retain fractional lots before applying display thresholds.

All accounts, symbols, quantities, and amounts are independently fictional.
These checks execute the shipped bundle and compare its rendered tables.
"""
from __future__ import annotations

import html
import re

import pytest

from tests.test_frontend_review_regressions import payload, run_js
from tests.test_tax_presentation import _estimate


def _lot(symbol, qty, value, basis, *, long_term=True, account="Example"):
    return {
        "account_group": account, "symbol": symbol, "qty": qty,
        "price": value / qty if value is not None else None,
        "value": value, "cost_basis": basis,
        "unrealized_gain": value - basis if value is not None else None,
        "open_date": "2022-01-01" if long_term else "2024-01-05",
        "is_long_term": long_term,
        "days_held": 1095 if long_term else 361,
        "days_to_lt": -729 if long_term else 6,
        "lt_eligible_date": "2023-01-02" if long_term else "2025-01-06",
    }


def _data(lots):
    data = payload()
    data["analytics"]["tax"] = {
        "lt_horizon": lots,
        "rate_estimates_by_year": {"2024": _estimate(2024)},
    }
    grouped = {}
    for lot in lots:
        grouped.setdefault((lot["account_group"], lot["symbol"]), []).append(lot)
    for (account, symbol), group in grouped.items():
        value = (sum(lot["value"] for lot in group)
                 if all(lot["value"] is not None for lot in group) else None)
        basis = sum(lot["cost_basis"] for lot in group)
        data["holdings_by_account"].append({
            "account_group": account, "account_type": "Taxable",
            "symbol": symbol, "sector": "Other",
            "quantity": sum(lot["qty"] for lot in group),
            "price": group[0]["price"], "value": value,
            "cost_basis": basis,
            "unrealized_gain": value - basis if value is not None else None,
        })
    return data


def _text(markup):
    return " ".join(html.unescape(re.sub(r"<[^>]*>", " ", markup)).split())


def _cells(row):
    return [_text(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]


def _tax_rows(markup):
    rows = re.findall(r'<tr class="lt-asset-row"[^>]*>(.*?)</tr>', markup, re.S)
    return {cells[0].lstrip("▸▾ "): cells for cells in map(_cells, rows)}


def test_fractional_short_term_lot_counts_in_position_and_expanded_detail(tmp_path):
    data = _data([
        _lot("FRACTION", 6.875, 220, 165),
        _lot("FRACTION", 0.125, 4, 3, long_term=False),
    ])
    result = run_js(tmp_path, data, """
      renderByAssetTable(); renderTax();
      const collapsed = nodeFor('taxContent').innerHTML;
      const handler = collapsed.match(/onclick="(_toggleLtAsset[^\"]*)"/)[1];
      eval(handler.replaceAll('&quot;', '"'));
      process.stdout.write(JSON.stringify({collapsed,
        expanded: nodeFor('taxContent').innerHTML,
        holdings: nodeFor('byAssetTbody').innerHTML, errors}));
    """)
    assert not result["errors"]
    holdings_row = re.search(r"<tr[^>]*>(.*?)</tr>", result["holdings"], re.S)[1]
    holding_qty = _cells(holdings_row)[3]
    row = _tax_rows(result["collapsed"])["FRACTION"]
    assert holding_qty == "7"
    assert row[2] == f"6.875 LT / {holding_qty}"
    assert "Fully LT" not in row[3]
    assert "6d" in row[3] and "2025-01-06" in row[3]
    assert row[4:7] == ["$224.00", "$168.00", "+$56.00"]
    assert "0 fully LT" in _text(result["collapsed"])
    assert "$4.00 still ST" in _text(result["collapsed"])

    detail = re.search(r'<table[^>]*><caption[^>]*>Tax lots approaching long-term '
                       r'eligibility in this position</caption>.*?<tbody>(.*?)</tbody>',
                       result["expanded"], re.S)[1]
    lot_rows = [_cells(row) for row in re.findall(r"<tr[^>]*>(.*?)</tr>", detail, re.S)]
    assert sorted(row[1] for row in lot_rows) == ["0.125", "6.875"]
    assert sum(float(row[1]) for row in lot_rows) == float(holding_qty)
    assert sum(float(row[2].replace("$", "")) for row in lot_rows) == 168
    assert sum(float(row[3].replace("$", "")) for row in lot_rows) == 224


@pytest.mark.parametrize("value,basis", [(3, 2), (None, 4)])
def test_visibility_threshold_applies_to_complete_position_value_or_basis(tmp_path, value, basis):
    lots = [_lot("SMALLLOTS", 0.125, value, basis) for _ in range(4)]
    lots += [_lot("DUST", 0.125, 2, 2)]
    # The same symbol in a second account does not borrow the first
    # account's aggregate value to cross the position display threshold.
    lots += [_lot("SEPARATE", 0.125, 6, 4, account=account)
             for account in ("Example", "Other Example")]
    result = run_js(tmp_path, _data(lots), """
      renderTax();
      process.stdout.write(JSON.stringify({html: nodeFor('taxContent').innerHTML, errors}));
    """)
    assert not result["errors"]
    rows = _tax_rows(result["html"])
    assert set(rows) == {"SMALLLOTS"}
    assert rows["SMALLLOTS"][2] == "0.5 LT / 0.5"
    assert "Fully LT" in rows["SMALLLOTS"][3]
    if value is None:
        assert rows["SMALLLOTS"][5] == "$16.00"
    else:
        assert rows["SMALLLOTS"][4] == "$12.00"


def test_tax_and_holdings_preserve_eight_decimal_quantity_display(tmp_path):
    data = _data([_lot("PRECISE", 1.23456789, 40, 30)])
    result = run_js(tmp_path, data, """
      renderByAssetTable(); renderTax();
      const handler = nodeFor('taxContent').innerHTML.match(/onclick="(_toggleLtAsset[^\"]*)"/)[1];
      eval(handler.replaceAll('&quot;', '"'));
      process.stdout.write(JSON.stringify({tax: nodeFor('taxContent').innerHTML,
        holdings: nodeFor('byAssetTbody').innerHTML, errors}));
    """)
    assert not result["errors"]
    holdings_row = re.search(r"<tr[^>]*>(.*?)</tr>", result["holdings"], re.S)[1]
    holding_qty = _cells(holdings_row)[3]
    assert holding_qty == "1.23456789"
    assert _tax_rows(result["tax"])["PRECISE"][2] == f"{holding_qty} LT / {holding_qty}"
    assert re.search(r'<td class="num">1\.23456789</td>', result["tax"])
