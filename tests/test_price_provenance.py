"""Fictional evidence behind current and historical valuation displays."""

from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest


@pytest.fixture
def evidence(isolated_workdir, monkeypatch):
    from src import price_provenance, prices

    monkeypatch.setenv("FIN_AS_OF_DATE", "2024-06-20")
    monkeypatch.setattr(prices, "_lazy_yf", Mock(side_effect=AssertionError("network forbidden")))
    _pin_clock(monkeypatch, "2024-06-20T21:00:00")
    return price_provenance, prices


def _pin_clock(monkeypatch, instant):
    from src import price_provenance, prices

    current = datetime.fromisoformat(instant).replace(tzinfo=timezone.utc)
    monkeypatch.setattr(prices, "_now_utc", lambda: current)
    monkeypatch.setattr(price_provenance, "_today", lambda: current.date())


def _txn(day, price, *, symbol="FAKEA", quantity=2.0, seq=0):
    return {"date": day, "symbol": symbol, "price": price, "seq": seq,
            "quantity": quantity, "amount": price * quantity, "fees": 0.0,
            "account": "Robinhood", "account_group": "Robinhood",
            "account_type": "Taxable", "action": "Buy", "raw_action": "Buy"}


def test_market_evidence_reports_bar_date_separately_from_download(evidence):
    provenance, prices = evidence
    prices._load_prices()["FAKEA"] = {"2024-06-19": 80.0}
    prices._load_meta()["symbols"]["FAKEA"] = {
        "last_successful_fetch": "2024-06-20T17:00:00"}
    assert provenance.describe_pricing("FAKEA", "2024-06-20", "cache") == {
        "source": "market", "date": "2024-06-19", "quote_symbol": "FAKEA",
        "stale": False, "provisional": False,
    }


def test_transaction_quotes_exclude_future_rows_and_rebase_without_redating(evidence):
    provenance, prices = evidence
    prices._load_splits()["FAKEA"] = [["2024-06-10", 2.0]]
    rows = [_txn("2024-06-01", 100.0), _txn("2024-06-21", 900.0),
            _txn("2024-06-20", 30.0, symbol="FAKEB", seq=9),
            _txn("2024-06-20", 50.0, symbol="FAKEB", seq=8)]
    assert provenance.transaction_quotes(rows, "2024-06-20") == (
        {"FAKEA": 50.0, "FAKEB": 30.0},
        {"FAKEA": "2024-06-01", "FAKEB": "2024-06-20"})
    assert provenance.transaction_quotes(list(reversed(rows)), "2024-06-20") == (
        provenance.transaction_quotes(rows, "2024-06-20"))
    assert rows[0]["price"] == 100.0
    result = provenance.describe_pricing("FAKEA", "2024-06-20", "txn", "2024-06-01")
    assert result["source"] == "transaction"
    assert result["date"] == "2024-06-01" and result["stale"] is True


@pytest.mark.parametrize("invalid", [float("inf"), float("nan"), 0.0, -10.0])
def test_invalid_transaction_quotes_preserve_the_prior_usable_observation(evidence, invalid):
    provenance, _ = evidence
    assert provenance.transaction_quotes([
        _txn("2024-06-01", 80.0), _txn("2024-06-19", invalid),
    ], "2024-06-20") == ({"FAKEA": 80.0}, {"FAKEA": "2024-06-01"})


@pytest.mark.parametrize("day,stale", [("2024-06-13", False), ("2024-06-12", True),
                                      (None, False), ("2024-06-21", False)])
def test_transaction_age_boundary_and_missing_or_future_evidence(evidence, day, stale):
    provenance, _ = evidence
    result = provenance.describe_pricing("FAKEA", "2024-06-20", "txn", day)
    assert result["source"] == "transaction"
    assert result["date"] == (day if day and day <= "2024-06-20" else None)
    assert result["stale"] is stale
    assert result["provisional"] is False


def test_proxy_and_intrinsic_sources_follow_the_actual_underlying_quote(evidence):
    provenance, prices = evidence
    prices._load_prices()["FAKEA"] = {"2024-06-14": 100.0, "2024-06-19": 120.0}
    prices._load_proxy_map().update({
        "Example Fund": {"proxy": "FAKEA", "method": "scaled",
                         "anchor_date": "2024-06-14", "anchor_price": 50.0},
        "DIRECT": {"proxy": "FAKEA", "method": "direct"},
    })
    proxy = provenance.describe_pricing("Example Fund", "2024-06-20", "cache")
    assert proxy["source"] == "proxy"
    assert proxy["quote_symbol"] == "FAKEA" and proxy["date"] == "2024-06-19"
    assert provenance.describe_pricing("DIRECT", "2024-06-20", "cache")["source"] == "market"
    option = provenance.describe_pricing("FAKEA 12/20/2024 Call $100.00",
                                         "2024-06-20", "intrinsic")
    assert option == {**proxy, "source": "intrinsic"}


@pytest.mark.parametrize("symbol,source,expected", [
    ("USD", "cash", "cash"), ("FAKEA", "none", "unavailable"),
    ("FAKEA", "cache", "unknown"),
    ("FAKEA 12/20/2024 Call $100.00", "intrinsic", "intrinsic"),
])
def test_cash_and_missing_evidence_do_not_invent_quote_dates(evidence, symbol, source, expected):
    provenance, _ = evidence
    assert provenance.describe_pricing(symbol, "2024-06-20", source) == {
        "source": expected, "date": None, "quote_symbol": None,
        "stale": False, "provisional": False,
    }


def test_annotating_holdings_preserves_every_financial_field(evidence):
    provenance, prices = evidence
    from src.valuation import mark

    prices._load_prices()["FAKEA"] = {"2024-06-14": 100.0, "2024-06-20": 120.0}
    prices._load_proxy_map()["Example Fund"] = {
        "proxy": "FAKEA", "method": "scaled", "anchor_date": "2024-06-14",
        "anchor_price": 50.0}
    option = "FAKEA 12/20/2024 Call $100.00"
    txns = [_txn("2024-06-01", 30.0, symbol="FAKEB"),
            _txn("2024-06-21", 900.0, symbol="FAKEB"),
            _txn("2024-06-14", 2.0, symbol=option)]
    transaction_values, _ = provenance.transaction_quotes(txns, "2024-06-20")
    account_rows = []
    for index, symbol in enumerate(["FAKEA", "Example Fund", "FAKEB", option, "USD", "FAKEC"]):
        marked = mark(symbol, index + 1, "2024-06-20", transaction_values, restate_qty=False)
        account_rows.append({"symbol": symbol, "quantity": marked.qty,
                             "price": marked.price, "value": marked.value,
                             "cost_basis": 100.0, "account_group": "Robinhood",
                             "account_type": "Taxable", "unrealized_gain": 50.0})
    holdings = deepcopy(account_rows)
    original = deepcopy((holdings, account_rows, txns))
    provenance.annotate_holdings(holdings, account_rows, txns, "2024-06-20")
    for rows, before in zip((holdings, account_rows), original[:2]):
        assert [{key: value for key, value in row.items() if key != "pricing"}
                for row in rows] == before
    assert txns == original[2]
    expected = ["market", "proxy", "transaction", "intrinsic", "cash", "unavailable"]
    assert [row["pricing"]["source"] for row in holdings] == expected
    assert [row["pricing"] for row in holdings] == [row["pricing"] for row in account_rows]
    assert account_rows[2]["pricing"]["date"] == "2024-06-01"
    account_rows[0]["pricing"]["source"] = "changed"
    assert holdings[0]["pricing"]["source"] == "market"


def test_summary_counts_account_positions_and_overlapping_warnings(evidence):
    provenance, _ = evidence
    rows = [
        {"account_group": "Group B", "symbol": "FAKEA", "value": 100.0,
         "pricing": {"source": "market", "date": "2024-06-20", "stale": False}},
        {"account_group": "Group A", "symbol": "FAKEA", "value": 100.0,
         "pricing": {"source": "transaction", "date": "2024-06-01", "stale": True}},
        {"account_group": "Group A", "symbol": "FUND", "value": 200.0,
         "pricing": {"source": "proxy", "date": "2024-06-20", "stale": False}},
        {"account_group": "Group A", "symbol": "OPTION", "value": 200.0,
         "pricing": {"source": "intrinsic", "date": "2024-06-20", "stale": False}},
        {"account_group": "Group B", "symbol": "MISSING", "value": None,
         "pricing": {"source": "unavailable", "stale": False}},
        {"account_group": "Group B", "symbol": "LEGACY", "value": 50.0},
        {"account_group": "Group B", "symbol": "USD", "value": 25.0},
    ]
    summary = provenance.summarize_pricing(rows, as_of="2024-06-20")
    assert summary["as_of"] == "2024-06-20"
    assert summary["total"] == {
        "positions": 7, "estimated": 3, "stale": 1, "unavailable": 1, "unknown": 1}
    assert summary["by_account"] == [
        {"account_group": "Group A", "positions": 3, "estimated": 3, "stale": 1,
         "unavailable": 0, "unknown": 0},
        {"account_group": "Group B", "positions": 4, "estimated": 0, "stale": 0,
         "unavailable": 1, "unknown": 1},
    ]


@pytest.mark.parametrize("symbol,instant,target,observed,stale,provisional", [
    # A Friday close remains usable on Sunday; one weekday of holiday slack.
    ("FAKEA", "2024-06-16T21:00:00", "2024-06-16", "2024-06-14", False, False),
    ("FAKEA", "2024-06-16T21:00:00", "2024-06-16", "2024-06-13", False, False),
    ("FAKEA", "2024-06-16T21:00:00", "2024-06-16", "2024-06-12", True, False),
    # At 17:00 ET equity is settled, while a fund may still await its NAV.
    ("FAKEA", "2024-06-20T21:00:00", "2024-06-20", "2024-06-18", True, False),
    ("FAKEX", "2024-06-20T21:00:00", "2024-06-20", "2024-06-18", False, False),
    ("FAKEX", "2024-06-20T23:00:00", "2024-06-20", "2024-06-18", True, False),
    ("FAKEX", "2024-06-20T21:00:00", "2024-06-20", "2024-06-20", False, True),
    ("FAKEA", "2024-06-20T19:00:00", "2024-06-20", "2024-06-20", False, True),
    # Crypto has no weekend slack and its UTC daily bar remains live all day.
    ("FAKE-USD", "2024-06-16T21:00:00", "2024-06-16", "2024-06-14", True, False),
    ("FAKE-USD", "2024-06-16T21:00:00", "2024-06-16", "2024-06-15", False, False),
    ("FAKE-USD", "2024-06-16T21:00:00", "2024-06-16", "2024-06-16", False, True),
])
def test_market_calendar_controls_staleness_and_provisional_quotes(
        evidence, monkeypatch, symbol, instant, target, observed, stale, provisional):
    provenance, prices = evidence
    _pin_clock(monkeypatch, instant)
    prices._load_prices()[symbol] = {observed: 80.0}
    result = provenance.describe_pricing(symbol, target, "cache")
    assert result["date"] == observed
    assert result["stale"] is stale
    assert result["provisional"] is provisional


def test_historical_quote_does_not_inherit_todays_provisional_cache(evidence, monkeypatch):
    provenance, prices = evidence
    prices._load_prices()["FAKEA"] = {"2024-06-14": 80.0, "2024-06-20": 100.0}
    prices._load_meta()["symbols"]["FAKEA"] = {
        "covered_end": "2024-06-20", "settled_through": "2024-06-19"}
    monkeypatch.setattr(provenance, "any_provisional",
                        Mock(side_effect=AssertionError("historical lookup used current metadata")))
    result = provenance.describe_pricing("FAKEA", "2024-06-14", "cache")
    assert result["date"] == "2024-06-14"
    assert result["provisional"] is False


def test_header_historical_provisional_flag_uses_snapshot_evidence(evidence):
    provenance, prices = evidence
    from src.analytics.header import compute_header_summary

    prices._load_prices()["FAKEA"] = {"2024-06-13": 75.0, "2024-06-14": 80.0,
                                      "2024-06-20": 100.0}
    prices._load_meta()["symbols"]["FAKEA"] = {
        "covered_end": "2024-06-20", "settled_through": "2024-06-19",
        "last_successful_fetch": "2024-06-20T12:00:00"}
    historical = [{"date": "2024-06-14", "total": 160.0, "positions": [
        {"symbol": "FAKEA", "quantity": 2.0, "value": 160.0,
         "account_group": "Robinhood", "cost_basis": 150.0,
         "pricing": provenance.describe_pricing("FAKEA", "2024-06-14", "cache")},
    ]}]
    result = compute_header_summary([_txn("2024-06-13", 75.0)], historical, [])
    assert result["prices_quote_date"] == "2024-06-14"
    assert result["prices_provisional"] is False


def test_history_snapshot_metadata_preserves_transaction_as_of_boundary(evidence):
    from src.history import compute_history

    # The fund is deliberately unquoted by the market; its later transaction
    # must not rewrite the earlier snapshot's quote or evidence date.
    rows = [_txn("2024-06-01", 50.0, symbol="Example Fund"),
            _txn("2024-06-19", 80.0, symbol="Example Fund")]
    snapshots = compute_history(rows, {"Example Fund": "Other"})
    earlier = next(snapshot for snapshot in snapshots if snapshot["date"] == "2024-06-15")
    before = next(row for row in earlier["positions"] if row["symbol"] == "Example Fund")
    assert (before["quantity"], before["price"], before["value"]) == (2.0, 50.0, 100.0)
    assert before["pricing"]["source"] == "transaction"
    assert before["pricing"]["date"] == "2024-06-01"
    after = next(row for row in snapshots[-1]["positions"] if row["symbol"] == "Example Fund")
    assert (after["quantity"], after["price"], after["value"]) == (4.0, 80.0, 320.0)
    assert after["pricing"]["date"] == "2024-06-19"


@pytest.mark.parametrize("source,quote_date,expected_value,expected_unknown", [
    ("market", "2024-06-18", 160.0, 0),
    ("transaction", "2024-06-10", 160.0, 0),
    ("transaction", None, 160.0, 1),
    ("unavailable", None, None, 0),
])
def test_transit_metadata_preserves_dated_undated_and_unavailable_quotes(
        evidence, source, quote_date, expected_value, expected_unknown):
    provenance, prices = evidence
    from src.return_flows import transit_positions

    tout = {"date": "2024-06-17", "account_group": "Group A",
            "symbol": "FAKEA", "quantity": 2.0}
    tin = {"date": "2024-06-21", "account_group": "Group B",
           "symbol": "FAKEA", "quantity": 2.0}
    if source == "market":
        prices._load_prices()["FAKEA"] = {quote_date: 80.0}
    fallback = {"FAKEA": 80.0} if source == "transaction" else {}
    dates = {"FAKEA": quote_date} if quote_date else None
    rows = transit_positions([(tout, tin)], "2024-06-20", fallback,
                             quote_dates=dates, carried_basis={id(tout): 150.0})
    assert len(rows) == 1
    row = rows[0]
    assert row["value"] == expected_value and row["quantity"] == 2.0
    assert row["cost_basis"] == 150.0
    assert row["pricing"]["source"] == source
    assert row["pricing"]["date"] == quote_date
    counts = provenance.summarize_pricing(rows)["total"]
    assert counts["unknown"] == expected_unknown
    assert counts["unavailable"] == int(expected_value is None)


def test_transit_invalid_split_evidence_is_reported_as_unavailable(evidence, monkeypatch):
    from src import valuation
    from src.return_flows import transit_positions

    monkeypatch.setattr(valuation, "split_factor_since", lambda symbol, day: float("inf"))
    tout = {"date": "2024-06-17", "account_group": "Group A",
            "symbol": "FAKEA", "quantity": 2.0}
    tin = {"date": "2024-06-21", "account_group": "Group B",
           "symbol": "FAKEA", "quantity": 2.0}
    row, = transit_positions([(tout, tin)], "2024-06-20", {"FAKEA": 80.0},
                             quote_dates={"FAKEA": "2024-06-10"})
    assert row["pricing"]["source"] == "unavailable"
    assert row["pricing"]["date"] is None
    assert row["value"] is None and row["valuation_issue"] == "split_during_transfer"
