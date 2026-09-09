"""Tax presentation labels the date and account scope already used by each view."""
from __future__ import annotations

import re

import pytest

from tests.test_account_transfer_frontend import _card
from tests.test_frontend_review_regressions import payload, run_js


def _estimate(year):
    """Independently fictional exported estimates; no tax-law calculation here."""
    return {
        "year": year, "salary": 50000, "bonuses": 1000, "portfolio_income": 500,
        "portfolio_income_ytd": 250, "realized_st": 100, "realized_lt": 0,
        "k401": 1500, "k401_ytd": 750, "k401_limit": 20000,
        "gross_income": 51500, "agi": 50000, "taxable_income": 40000,
        "taxable_ordinary": 40000, "std_deduction": 10000,
        "marginal_short": 0.2, "marginal_long": 0.15,
        "est_cap_gains_tax_federal": 20, "est_cap_gains_tax_state": 0,
        "est_niit": 0, "est_cap_gains_tax_total": 20, "est_quarterly_payment": 5,
        "is_projection": year == 2024, "year_fraction_observed": 0.5,
        "headroom_to_next_bracket": 10000, "next_bracket_rate": 0.3,
        "ltcg_headroom": 12000, "ltcg_next_rate": 0.2, "niit_headroom": 150000,
        "niit_threshold": 200000,
        "bracket_fill": [
            {"lower": 0, "upper": 10000, "rate": 0.1, "in_bracket": 10000,
             "room_left": 0, "tax_paid": 1000},
            {"lower": 10000, "upper": 50000, "rate": 0.2, "in_bracket": 30000,
             "room_left": 10000, "tax_paid": 6000},
        ],
        "safe_harbor": {"prior_year": year - 1, "prior_year_tax": 6000,
            "prior_year_agi": 45000, "threshold_pct": 1, "prior_year_prong": 6000,
            "ninety_pct_prong": 6300, "effective_target": 6000,
            "projected_withholding": 6200, "covered": True, "shortfall": 0,
            "w4_withholding_est": 6200, "extra_withholding": 0,
            "est_total_federal_tax": 7000},
    }


def _data():
    transactions = [
        {"date": "2023-03-01", "account_group": "Example", "account_type": "Taxable",
         "symbol": "EARLIER", "action": "Sell", "quantity": 1, "amount": 200,
         "cost_basis": 100, "realized_gain": 100, "holding_days": 40},
        {"date": "2024-02-15", "account_group": "Example", "account_type": "Taxable",
         "symbol": "CURRENT", "action": "Sell", "quantity": 1, "amount": 50,
         "cost_basis": 100, "realized_gain": -50, "holding_days": 30},
        {"date": "2024-02-20", "account_group": "Example", "account_type": "Taxable",
         "symbol": "CURRENT", "action": "Buy", "quantity": 1, "amount": 50},
    ]
    data = payload(transactions=transactions)
    data.update(as_of="2024-06-30", generated="2024-06-30T12:00:00")
    data["analytics"]["tax"] = {
        "rate_estimates_by_year": {str(year): _estimate(year) for year in (2023, 2024)},
        "harvest_lots": [{"symbol": "HELD", "account_group": "Example", "qty": 2,
            "value": 100, "loss": -40, "st_loss": -40, "lt_loss": 0,
            "position_unrealized": 20, "wash_risk": True, "last_buy_date": "2024-06-20",
            "lots": [{"date": "2024-05-01", "qty": 2, "cost_basis": 140,
                      "value": 100, "loss": -40, "is_long_term": False, "days_held": 60}]}],
        "lt_horizon": [{"symbol": "HELD", "account_group": "Example", "qty": 2,
            "price": 50, "value": 100, "cost_basis": 140, "unrealized_gain": -40,
            "open_date": "2024-05-01", "is_long_term": False, "days_held": 60,
            "days_to_lt": 306, "lt_eligible_date": "2025-05-02"}],
        "form_8949": [{"description": "Fictional earlier disposal", "date_sold": "2023-03-01"},
                      {"description": "Fictional current disposal", "date_sold": "2024-02-15"}],
    }
    return data


def _table(html, caption):
    found = re.search(r'<table[^>]*>\s*<caption[^>]*>' + re.escape(caption)
                      + r'</caption>.*?</table>', html, re.S)
    assert found is not None, f"Missing table: {caption}"
    return found.group()


@pytest.mark.parametrize("year,scope,gain,count", [
    ("2023", "2023", "+$100.00", "1"),
    ("2024", "2024 through 2024-06-30", "-$50.00", "1"),
    ("all", "All recorded years", "+$50.00", "2"),
])
def test_year_selection_labels_realizations_and_independent_planning_scope(tmp_path, year, scope, gain, count):
    result = run_js(tmp_path, _data(), f"""
      asOfDate = '2023-03-01';
      setTaxYearFilter('{year}');
      process.stdout.write(JSON.stringify({{html: nodeFor('taxContent').innerHTML, errors}}));
    """)
    assert not result["errors"]
    html = result["html"]
    assert 'role="group" aria-label="Realization year"' in html
    assert f'Realizations · {scope}' in html
    assert _card(html, "Short-term Gain") == gain
    assert _card(html, "Closed Trades") == count
    selected = re.search(r'<button[^>]*id="tax-year-' + year + r'"[^>]*>', html).group()
    assert 'aria-pressed="true"' in selected
    assert 'Latest taxable holdings · 2024-06-30' in html
    assert 'All recorded loss sales · through 2024-06-30' in html
    assert 'HELD' in _table(html, "Tax-loss harvest candidates across taxable accounts")
    assert '2024-02-15' in _table(html, "Potential wash sales")
    estimates_year = "2024" if year == "all" else year
    assert f'{estimates_year} withholding estimate' in html
    assert f'{estimates_year} income estimate' in html
    assert ('2024 year-end projection' in html) is (year != "2023")
    for caption in ("Realized gains by year", "Realized gains by asset"):
        table = _table(html, caption)
        if year == "2023":
            assert "2024" not in table and "CURRENT" not in table
        elif year == "2024":
            assert "2023" not in table and "EARLIER" not in table


def test_essential_definitions_and_bracket_amounts_are_native_disclosures(tmp_path):
    result = run_js(tmp_path, _data(), """
      renderTax();
      process.stdout.write(JSON.stringify({html: nodeFor('taxContent').innerHTML, errors}));
    """)
    assert not result["errors"]
    html = result["html"]
    for identifier, summary in [
        ("tax-gain-definitions", "Gain and tax-estimate definitions"),
        ("tax-bracket-definitions", "Bracket amounts and headroom definitions"),
    ]:
        details = re.search(r'<details[^>]*id="' + identifier + r'"[^>]*>(.*?)</details>', html, re.S)
        assert details is not None
        assert re.match(r'\s*<summary>' + re.escape(summary) + r'</summary>', details[1])
        assert 'onclick=' not in details[1]
    bracket_details = re.search(r'<details[^>]*id="tax-bracket-definitions"[^>]*>(.*?)</details>', html, re.S)[1]
    assert '<b>LTCG</b> means long-term capital gains' in bracket_details
    assert '<b>NIIT</b> means Net Investment Income Tax' in bracket_details
    table = _table(bracket_details, "2024 estimated income by federal bracket")
    assert "$10,000.00" in table and "$30,000.00" in table
    assert "Income in bracket" in table and "Room left" in table


def test_historical_filter_exposes_unfiltered_csv_scope_and_preserves_download(tmp_path):
    result = run_js(tmp_path, _data(), """
      setTaxYearFilter('2023');
      let exported;
      _downloadCsv = (filename, columns, rows) => { exported = {filename, rows}; };
      downloadForm8949();
      process.stdout.write(JSON.stringify({html: nodeFor('taxContent').innerHTML, exported, errors}));
    """)
    assert not result["errors"]
    assert 'CSV export · all recorded years' in result["html"]
    assert [row["date_sold"] for row in result["exported"]["rows"]] == ["2023-03-01", "2024-02-15"]


def test_rate_controls_keep_existing_ids_and_update_the_same_figures(tmp_path):
    result = run_js(tmp_path, _data(), """
      setTaxYearFilter('2023');
      setTaxShortRate(0.25); setTaxLongRate(0.18);
      process.stdout.write(JSON.stringify({html: nodeFor('taxContent').innerHTML,
        shortRate: taxShortRate, longRate: taxLongRate, year: taxYearFilter, errors}));
    """)
    assert not result["errors"]
    assert (result["year"], result["shortRate"], result["longRate"]) == ("2023", 0.25, 0.18)
    assert _card(result["html"], "Estimated Tax") == "$25.00"
    assert 'id="taxShortRate" value="25.0"' in result["html"]
    assert 'id="taxLongRate" value="18.0"' in result["html"]
    assert 'Latest taxable holdings · 2024-06-30' in result["html"]


@pytest.mark.parametrize("past_disposal", [False, True])
def test_snapshot_year_without_disposals_is_selected_and_can_be_revisited(tmp_path, past_disposal):
    data = _data()
    data["transactions"] = [txn for txn in data["transactions"]
                            if "realized_gain" not in txn or (past_disposal and txn["date"].startswith("2023"))]
    data["analytics"]["tax"]["form_8949"] = [row for row in data["analytics"]["tax"]["form_8949"]
                                             if past_disposal and row["date_sold"].startswith("2023")]
    result = run_js(tmp_path, data, """
      renderTax();
      const initial = nodeFor('taxContent').innerHTML;
      setTaxYearFilter('all');
      const all = nodeFor('taxContent').innerHTML;
      setTaxYearFilter(String(snapshotYear()));
      process.stdout.write(JSON.stringify({initial, all, restored: nodeFor('taxContent').innerHTML, errors}));
    """)
    assert not result["errors"]
    for html in (result["initial"], result["restored"]):
        selected = re.findall(r'<button[^>]*id="tax-year-([^"]+)"[^>]*aria-pressed="true"', html)
        assert selected == ["2024"], "the empty default year must have a visible, selected control"
        assert _card(html, "Closed Trades") == "0"
        assert _card(html, "Estimated Tax") == "$0.00"
        assert "Realizations · 2024 through 2024-06-30" in html
        assert "2024 year-end projection" in html
    all_html = result["all"]
    assert re.findall(r'<button[^>]*id="tax-year-([^"]+)"[^>]*aria-pressed="true"', all_html) == ["all"]
    assert _card(all_html, "Closed Trades") == ("1" if past_disposal else "0")
    assert _card(all_html, "Short-term Gain") == ("+$100.00" if past_disposal else "+$0.00")
    assert 'id="tax-year-2024"' in all_html, "the snapshot year must remain selectable from All Years"
    assert ('id="tax-year-2023"' in all_html) is past_disposal
