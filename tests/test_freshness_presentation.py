"""Price provenance and transaction coverage rendered from fictional inputs."""
from __future__ import annotations

from copy import deepcopy

import pytest

from tests.test_frontend_review_regressions import payload, run_js
from tests.test_holdings_presentation import CONTROLS, holdings_payload  # noqa: F401


def pricing(source, date=None, *, stale=False, symbol=None):
    return {"source": source, "date": date, "quote_symbol": symbol,
            "stale": stale, "provisional": False}


@pytest.fixture
def freshness_payload(holdings_payload):
    data = deepcopy(holdings_payload)
    rows = data["holdings_by_account"]
    rows[0]["pricing"] = pricing("market", "2024-12-31")
    rows[1]["pricing"] = pricing("transaction", "2024-03-15", stale=True)
    rows[2]["pricing"] = pricing("unavailable")
    rows[2]["price"] = rows[2]["value"] = None
    prior = data["history"][0]["positions"]
    prior[0]["pricing"] = pricing("transaction", "2024-02-01", stale=True)
    prior[1]["pricing"] = pricing("market", "2024-03-28")
    # Full-precision history values and their visible snapshot metadata must
    # remain aligned even when an older precision row has no pricing field.
    data["history"][0]["valuation_precision"] = {"positions": [
        {key: value for key, value in row.items() if key != "pricing"}
        for row in prior]}
    data["analytics"]["header_summary"] = {
        "value": 300, "prices_quote_date": "2024-03-15",
        "prices_as_of": "2024-12-31T12:30:00",
        "prices_freshness_unknown": False, "prices_provisional": True,
    }
    data["analytics"]["account_freshness"] = [
        {"account_group": "Example A", "account_type": "Taxable",
         "latest_transaction_date": "2024-11-30", "days_since_latest": 31,
         "transaction_count": 12},
        {"account_group": "Example B", "account_type": "Taxable",
         "latest_transaction_date": None, "days_since_latest": None,
         "transaction_count": 0},
    ]
    return data


def test_caveats_use_inline_buttons_and_complete_sources_live_in_data_details(tmp_path, freshness_payload):
    result = run_js(tmp_path, freshness_payload, """
      renderHoldings(); renderByAssetTable(); renderStats(); renderTransactionCoverage();
      process.stdout.write(JSON.stringify({positions: NODES.get('byAssetTbody').innerHTML,
        groups: NODES.get('holdingsTbody').innerHTML,
        total: NODES.get('positionsPricingQuality').innerHTML,
        selected: NODES.get('stats').innerHTML,
        header: NODES.get('topBarSummary').innerHTML,
        details: NODES.get('transactionCoverage').innerHTML,
        dates: NODES.get('pricesAsOf').innerHTML, errors}));
    """)
    assert not result["errors"]
    assert "Market quote" not in result["positions"]
    assert "Market quote" in result["details"]
    assert "2024-12-31" in result["details"]
    assert "Transaction estimate · 2024-03-15 · stale" in result["positions"]
    assert "Price unavailable" in result["positions"]
    assert 'Excluded from this total' in result["positions"]
    assert result["positions"].count('class="pricing-note"') == 2
    assert 'class="price-provenance' not in result["positions"]
    assert result["positions"].count('>*</button>') == 2
    assert result["details"].count('<details ') == 1
    assert '<summary>Data details' in result["details"]
    for field in ("total", "selected", "header", "groups"):
        assert "Incomplete value" in result[field]
        assert "1 estimated, 1 stale, 1 unpriced" in result[field]
    assert "oldest price date 2024-03-15" in result["dates"]
    assert "last successful fetch 2024-12-31" in result["dates"]
    assert "provisional" in result["dates"]
    assert '>Price info</button>' in result["dates"]


def test_warnings_follow_table_filters_and_historical_positions(tmp_path, freshness_payload):
    result = run_js(tmp_path, freshness_payload, CONTROLS + """
      const quality = () => NODES.get('positionsPricingQuality').innerHTML;
      tableAccount('Example A'); const current = quality();
      setAsOfDate('2024-04-30'); const historical = quality();
      const historicalCells = NODES.get('byAssetTbody').innerHTML;
      tableAccount('Example B'); const otherHistorical = quality();
      setAsOfDate(null); const otherCurrent = quality();
      byAssetSearch = 'no matching position'; renderByAssetTable(); const empty = quality();
      process.stdout.write(JSON.stringify({current, historical, historicalCells,
        otherHistorical, otherCurrent, empty, errors}));
    """)
    assert not result["errors"]
    assert result["current"] == result["otherHistorical"] == result["empty"] == ""
    assert "1 estimated, 1 stale" in result["historical"]
    assert "2024-02-01" in result["historicalCells"]
    assert "2024-12-31" not in result["historicalCells"]
    assert "Incomplete value" in result["otherCurrent"]


def test_board_quality_respects_membership_versus_account_scope(tmp_path, freshness_payload):
    result = run_js(tmp_path, freshness_payload, CONTROLS + """
      setBoardLayout('board');
      boardControl('boardAccountFilter', 'change', 'Example A');
      const symbols = NODES.get('positionsPricingQuality').innerHTML;
      setBoardGroupBy('account'); const account = NODES.get('positionsPricingQuality').innerHTML;
      setAsOfDate('2024-03-31'); const notice = NODES.get('positionsPricingQuality').innerHTML;
      process.stdout.write(JSON.stringify({symbols, account, notice, errors}));
    """)
    assert not result["errors"]
    assert "1 estimated, 1 stale" in result["symbols"]
    assert result["account"] == result["notice"] == ""


def test_old_exports_do_not_claim_market_prices_or_successful_downloads(tmp_path, holdings_payload):
    holdings_payload["analytics"]["header_summary"] = {
        "prices_as_of": "2024-12-31T12:00:00", "prices_provisional": False}
    result = run_js(tmp_path, holdings_payload, """
      process.stdout.write(JSON.stringify({cells: NODES.get('byAssetTbody').innerHTML,
        header: NODES.get('topBarSummary').innerHTML,
        stamp: NODES.get('pricesAsOf').innerHTML, errors}));
    """)
    assert not result["errors"]
    assert "Price source unknown" in result["cells"]
    assert "Market quote" not in result["cells"]
    assert "Freshness unknown" in result["header"]
    assert "oldest price date unknown" in result["stamp"]
    assert "successful fetch unknown" in result["stamp"]
    assert "last successful fetch 2024" not in result["stamp"]


def test_coverage_is_explicit_and_imported_labels_are_escaped(tmp_path, freshness_payload):
    label = '<img src=x onerror="alert(1)"> & Example'
    freshness_payload["analytics"]["account_freshness"][0]["account_group"] = label
    freshness_payload["holdings_by_account"][0]["pricing"] = pricing(
        "proxy", "2024-12-31", symbol=label)
    result = run_js(tmp_path, freshness_payload, """
      renderTransactionCoverage();
      process.stdout.write(JSON.stringify({coverage: NODES.get('transactionCoverage').innerHTML,
        cells: NODES.get('byAssetTbody').innerHTML, errors}));
    """)
    assert not result["errors"]
    assert "Transaction coverage" in result["coverage"]
    assert "Latest imported activity does not prove statement completeness" in result["coverage"]
    assert "Inactivity may be normal" in result["coverage"]
    assert "2024-11-30 · 31 days before export" in result["coverage"]
    assert "Imported date unknown" in result["coverage"]
    for html in (result["coverage"], result["cells"]):
        assert '<img ' not in html
        assert '&lt;img ' in html
        assert '&amp; Example' in html


def test_cash_needs_no_market_date_and_missing_coverage_has_an_empty_state(tmp_path):
    data = payload()
    data["holdings_by_account"] = [{"account_group": "Example savings", "symbol": "USD",
        "account_type": "Savings", "sector": "Cash", "quantity": 40,
        "price": 1, "value": 40, "pricing": pricing("cash")}]
    result = run_js(tmp_path, data, """
      renderTransactionCoverage();
      process.stdout.write(JSON.stringify({quality: pricingQualityHtml(holdingsByAccount),
        cells: NODES.get('byAssetTbody').innerHTML,
        coverage: NODES.get('transactionCoverage').innerHTML, errors}));
    """)
    assert not result["errors"]
    assert result["quality"] == ""
    assert 'pricing-note' not in result["cells"]
    assert '>Cash</td>' in result["coverage"]
    assert 'coverage is unavailable' in result["coverage"]


@pytest.mark.parametrize('source', ['market', 'broker', 'cash'])
def test_healthy_observed_prices_have_no_marker(tmp_path, source):
    data = payload()
    data['holdings_by_account'] = [{"account_group": "Example", "symbol": "FAKE",
        "account_type": "Taxable", "sector": "Other", "quantity": 1,
        "price": 40, "value": 40, "pricing": pricing(source, None if source == 'cash' else '2024-12-31')}]
    data['analytics']['header_summary'] = {'prices_quote_date': '2024-12-31',
        'prices_as_of': None, 'prices_freshness_unknown': False, 'prices_market_required': False}
    result = run_js(tmp_path, data, """
      renderTransactionCoverage();
      process.stdout.write(JSON.stringify({cells: NODES.get('byAssetTbody').innerHTML,
        details: NODES.get('transactionCoverage').innerHTML,
        stamp: NODES.get('pricesAsOf').innerHTML,
        quality: pricingQualityHtml(holdingsByAccount), errors}));
    """)
    assert not result['errors']
    assert 'pricing-note' not in result['cells']
    assert result['quality'] == ''
    assert 'successful fetch unknown' not in result['stamp']
    if source == 'broker':
        assert 'Broker price' in result['details']


def test_stale_broker_mark_retains_its_observation_date(tmp_path, freshness_payload):
    row = freshness_payload['holdings_by_account'][0]
    row['pricing'] = pricing('broker', '2024-02-20', stale=True)
    result = run_js(tmp_path, freshness_payload, """
      process.stdout.write(JSON.stringify({cells: NODES.get('byAssetTbody').innerHTML,
        detail: priceDescription(holdingsByAccount[0]), errors}));
    """)
    assert not result['errors']
    assert result['detail'] == 'Broker price · 2024-02-20 · stale'
    assert 'Price details for DUP' in result['cells']
    assert 'Broker price · 2024-02-20 · stale' in result['cells']


@pytest.mark.parametrize('source', ['proxy', 'intrinsic'])
def test_expected_estimates_are_explained_without_failed_refresh_styling(tmp_path, freshness_payload, source):
    row = freshness_payload['holdings_by_account'][0]
    row['pricing'] = pricing(source, '2024-12-31', symbol='PROXY')
    result = run_js(tmp_path, freshness_payload, """
      process.stdout.write(JSON.stringify({marker: priceProvenanceHtml(holdingsByAccount[0]),
        total: pricingQualityHtml([holdingsByAccount[0]]), errors}));
    """)
    assert not result['errors']
    assert '>*</button>' in result['marker']
    assert 'configured valuation method' in result['total']
    assert 'failed' not in result['total'].lower()
    for html in result.values():
        if isinstance(html, str):
            assert 'yellow' not in html and 'price-caution' not in html


def test_filtered_price_quality_matches_python_for_missing_and_estimated_evidence(tmp_path):
    from src.price_provenance import summarize_pricing

    data = payload()
    evidence = [
        ("USD", 40, None),
        ("FAKEA", 12, pricing("market", "2024-12-31")),
        ("FAKEB", 3, pricing("transaction")),
        ("FAKEC", 2, None),
        ("FAKED", None, pricing("market", "2024-12-31")),
        ("FAKEE", None, pricing("unavailable")),
        ("FAKEF", 6, pricing("proxy", "2024-12-20", stale=True)),
    ]
    rows = [{"symbol": symbol, "account_group": "Example", "value": value,
             **({"pricing": detail} if detail else {})}
            for symbol, value, detail in evidence]
    data["holdings_by_account"] = rows
    expected = summarize_pricing(rows)
    data["analytics"]["pricing_quality"] = expected
    result = run_js(tmp_path, data, """
      process.stdout.write(JSON.stringify({latest: pricingQuality(holdingsByAccount),
        filtered: pricingQuality(holdingsByAccount.slice()), errors}));
    """)
    assert not result["errors"]
    assert result["latest"] == result["filtered"] == expected["total"]
    assert result["filtered"] == {"positions": 7, "estimated": 2, "stale": 1,
                                  "unavailable": 2, "unknown": 2}
